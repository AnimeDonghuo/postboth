import os, re, threading, requests, html
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pymongo import MongoClient

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URI = os.getenv("MONGO_URI") 
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

TMDB_KEY = "57c932b753612419360f21e739652579"
OMDB_KEYS = ["78aba0e3", "984f89be", "ce245f40", "2e8c5c65", "2451f643", "79803fd4", "f31bb8de"]

client = MongoClient(MONGO_URI)
db = client['AnimePostBot']

app = Flask(__name__)
@app.route('/')
def health(): return "Bot is Online", 200
def run_web(): app.run(host='0.0.0.0', port=8000)

# --- UTILS ---
def clean_text(text):
    if not text: return "No synopsis available."
    text = re.sub(r'<(br|p|/p|br /)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'Note:.*', '', text, flags=re.IGNORECASE).strip()
    return text

def get_omdb_rating(title):
    for key in OMDB_KEYS:
        try:
            r = requests.get(f"http://www.omdbapi.com/?t={title}&apikey={key.strip()}").json()
            if r.get('Response') == 'True':
                return {
                    "imdb": r.get('imdbRating', '7.5'), 
                    "rt": r.get('Ratings', [{},{"Value":"90%"}])[1].get('Value', '90%') if len(r.get('Ratings', [])) > 1 else "90%", 
                    "year": r.get('Year', 'N/A')
                }
        except: continue
    return {"imdb": "7.5", "rt": "90%", "year": "N/A"}

def get_metadata(query, med_type="anime"):
    search_query = re.sub(r'\s(s\d+|season\s?\d+)', '', query, flags=re.I).strip()
    year_match = re.search(r'\b(19|20)\d{2}\b', search_query)
    year = year_match.group(0) if year_match else None
    if year: search_query = search_query.replace(year, "").strip()

    if med_type == "anime":
        gql = 'query($s:String){Media(search:$s,type:ANIME){title{english romaji}description averageScore genres bannerImage coverImage{extraLarge}}}'
        try:
            res = requests.post('https://graphql.anilist.co', json={'query':gql, 'variables':{'s':search_query}}).json()
            data = res['data']['Media']
            return {
                "title": data['title'].get('english') or data['title'].get('romaji'),
                "desc": clean_text(data.get('description')),
                "rating": str(data.get('averageScore', 84) / 10),
                "genres": data.get('genres', []),
                "img": data.get('bannerImage') or data['coverImage']['extraLarge']
            }
        except: pass

    tmdb_path = "movie" if med_type == "movie" else "tv" if med_type == "series" else "multi"
    try:
        url = f"https://api.themoviedb.org/3/search/{tmdb_path}?api_key={TMDB_KEY}&query={search_query}"
        if year: url += f"&year={year}"
        search = requests.get(url).json()
        if search['results']:
            res = search['results'][0]
            return {
                "title": res.get('title') or res.get('name'),
                "desc": clean_text(res.get('overview')),
                "rating": str(res.get('vote_average', 7.5)),
                "genres": ["Action", "Adventure"],
                "img": f"https://image.tmdb.org/t/p/original{res.get('backdrop_path')}" if res.get('backdrop_path') else None,
                "release": res.get('release_date') or res.get('first_air_date') or "N/A"
            }
    except: pass
    return None

def build_caption(t, mode):
    footer = "\n\n🔗 𝗢𝘂𝗿 𝗡𝗲𝘁𝘄𝗼𝗿𝗸 @Movies_Hindi_Plus" if mode != "anime" else "\n\n🔗 𝗢𝘂𝗿 𝗡𝗲𝘁𝘄𝗼𝗿𝗸 @Donghua_Xin"
    header = (
        f"<b>{t['title']}</b>\n"
        f"<b>⟣────────────────────⟢</b>\n"
        f"<b>‣ Audio ⌯ {t['audio']}</b>\n"
        f"<b>‣ Rating ⌯ {t['rating']}</b>\n"
        f"<b>‣ Quality ⌯ {t['quality']}</b>\n"
        f"<b>‣ {t['extra_label']} ⌯ {t['extra_val']}</b>\n"
        f"<b>‣ Genres ⌯ {t['genres']}</b>\n"
        f"<b>⟣────────────────────⟢</b>\n"
        f"<b>‣ Synopsis ⌯</b>\n"
    )
    max_synopsis = 1024 - (len(header) + len(footer) + 60)
    synopsis = t['synopsis']
    if len(synopsis) > max_synopsis: synopsis = synopsis[:max_synopsis] + "..."
    return f"{header}<blockquote expandable><b>{synopsis}</b></blockquote>{footer}"

# --- BOT HANDLERS ---
async def start(u, c):
    msg = (
        "<b>🚀 Post Bot Commands:</b>\n\n"
        "<b>1. /post Name | Ep | [Img] | Links</b>\n"
        "<b>2. /movie Name | [Img] | Audio | Links</b>\n"
        "<b>3. /webseries Name | Ep/Season | [Img] | Audio | Links</b>\n\n"
        "<b>Broadcast:</b>\n"
        "• <code>/broadcast</code> (Reply to message) - Select channels to broadcast.\n"
        "• <code>/broadcastall</code> (Reply to message) - Send to all linked channels.\n\n"
        "<b>Admin:</b>\n"
        "• /addchannel [ID] [Tag]\n"
        "• /channels - Manage linked channels"
    )
    await u.message.reply_text(msg, parse_mode='HTML')

async def post_logic(u, c, mode):
    try:
        raw = u.message.text.split(None, 1)[1]
        parts = [p.strip() for p in raw.split('|')]
        name = parts[0]
        img_user, audio, ep, links = None, "Hindi & English", "01", ""

        if mode == "anime":
            ep = parts[1] if len(parts) > 1 else "01"
            audio = "Chinese | Eng-Sub"
            if len(parts) > 3: img_user, links = parts[2], parts[3]
            elif len(parts) == 3: links = parts[2]
        elif mode == "movie":
            if len(parts) > 3: img_user, audio, links = parts[1], parts[2], parts[3]
            elif len(parts) == 3: audio, links = parts[1], parts[2]
            elif len(parts) == 2: links = parts[1]
        elif mode == "series":
            ep = parts[1] if len(parts) > 1 else "Season 01"
            if len(parts) > 4: img_user, audio, links = parts[2], parts[3], parts[4]
            elif len(parts) == 4: audio, links = parts[2], parts[3]
            elif len(parts) == 3: links = parts[2]

        meta = get_metadata(name, "anime" if mode == "anime" else "movie" if mode == "movie" else "series")
        if not meta: return await u.message.reply_text("❌ Title not found.")

        final_img = img_user if (img_user and img_user.startswith("http")) else meta.get('img')
        ratings = get_omdb_rating(name)
        q_list = re.findall(r'(\d+p|4K)', links, re.I)
        q_label = " | ".join(sorted(list(set(q_list)))) if q_list else "480p | 720p | 1080p"
        
        template_data = {
            "title": meta['title'], "audio": audio,
            "rating": f"{ratings['imdb']} IMDb | {ratings['rt']} RT" if mode != "anime" else f"{meta['rating']} / 10",
            "quality": q_label, "extra_label": "Episode" if mode != "movie" else "Released On",
            "extra_val": ep if mode != "movie" else meta.get('release', ratings['year']),
            "genres": " ".join(['#'+g.replace(' ','_') for g in meta.get('genres', [])]),
            "synopsis": meta['desc']
        }

        caption = build_caption(template_data, mode)
        btns = [InlineKeyboardButton(f"🚀 {q.upper()} Download", url=l) for q, l in re.findall(r'(\d+p|4K)\s*:\s*(https?://\S+)', links, re.I)]
        
        c.user_data['post_data'] = {"caption": caption, "image": final_img, "buttons": [btns[i:i + 2] for i in range(0, len(btns), 2)]}
        c.user_data['bc_msg'] = None # Clear broadcast context
        c.user_data['selected_channels'] = []

        chs = list(db.channels.find({"uid": u.effective_user.id}))
        kb = [[InlineKeyboardButton(ch['tag'], callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
        await u.message.reply_photo(photo=final_img, caption=caption + "\n\n<b>Select channels:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except Exception as e: await u.message.reply_text(f"<b>Error:</b> {e}", parse_mode='HTML')

async def broadcast_cmd(u, c):
    if u.effective_user.id != OWNER_ID: return
    if not u.message.reply_to_message:
        return await u.message.reply_text("❌ Reply to a message to broadcast it.")
    
    # Store the message to be broadcasted
    c.user_data['bc_msg'] = u.message.reply_to_message.message_id
    c.user_data['bc_chat'] = u.message.chat_id
    c.user_data['post_data'] = None # Clear post context
    c.user_data['selected_channels'] = []

    chs = list(db.channels.find({"uid": u.effective_user.id}))
    if not chs: return await u.message.reply_text("No channels linked.")
    
    kb = [[InlineKeyboardButton(ch['tag'], callback_data=f"sel_{ch['cid']}")] for ch in chs]
    kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
    await u.message.reply_text("<b>Broadcast Selection:</b>\nSelect channels where you want to send the replied message.", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def callback_handler(u, c):
    q = u.callback_query
    data = q.data
    uid = u.effective_user.id

    if data.startswith("rm_"):
        db.channels.delete_one({"uid": uid, "cid": data.replace("rm_","")})
        return await q.edit_message_text("Removed.")

    if data.startswith("sel_"):
        selected = c.user_data.get('selected_channels', [])
        cid = data.replace("sel_", "")
        if cid in selected: selected.remove(cid)
        else: selected.append(cid)
        c.user_data['selected_channels'] = selected
        
        chs = list(db.channels.find({"uid": uid}))
        new_kb = [[InlineKeyboardButton(f"{'✅ ' if ch['cid'] in selected else ''}{ch['tag']}", callback_data=f"sel_{ch['cid']}")] for ch in chs]
        new_kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_kb))

    if data == "final_send":
        sel = c.user_data.get('selected_channels', [])
        if not sel: return await q.answer("❌ Select at least one channel!", show_alert=True)
        
        post = c.user_data.get('post_data')
        bc_mid = c.user_data.get('bc_msg')

        count = 0
        for cid in sel:
            try:
                if bc_mid: # It's a Broadcast
                    await c.bot.copy_message(chat_id=cid, from_chat_id=c.user_data['bc_chat'], message_id=bc_mid)
                else: # It's a Post
                    await c.bot.send_photo(chat_id=cid, photo=post['image'], caption=post['caption'], reply_markup=InlineKeyboardMarkup(post['buttons']), parse_mode='HTML')
                count += 1
            except: continue
        
        await q.edit_message_text(f"<b>✅ Successfully Sent to {count} channels!</b>", parse_mode='HTML') if bc_mid else await q.edit_message_caption(f"<b>✅ Successfully Sent to {count} channels!</b>", parse_mode='HTML')

async def add_channel(u, c):
    if len(c.args) < 2: return await u.message.reply_text("Usage: /addchannel -100xxx Tag")
    db.channels.update_one({"uid": u.effective_user.id, "cid": c.args[0]}, {"$set": {"tag": " ".join(c.args[1:])}}, upsert=True)
    await u.message.reply_text("✅ Channel Added!")

async def list_channels(u, c):
    chs = list(db.channels.find({"uid": u.effective_user.id}))
    kb = [[InlineKeyboardButton(f"❌ Remove {ch['tag']}", callback_data=f"rm_{ch['cid']}")] for ch in chs]
    await u.message.reply_text("Manage Channels:", reply_markup=InlineKeyboardMarkup(kb))

async def broadcast_all(u, c):
    if u.effective_user.id != OWNER_ID or not u.message.reply_to_message: return
    chs = list(db.channels.find({"uid": u.effective_user.id}))
    for ch in chs:
        try: await c.bot.copy_message(chat_id=ch['cid'], from_chat_id=u.message.chat_id, message_id=u.message.reply_to_message.message_id)
        except: pass
    await u.message.reply_text("Broadcast to all done.")

if __name__ == '__main__':
    threading.Thread(target=run_web, daemon=True).start()
    bot = Application.builder().token(TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("addchannel", add_channel))
    bot.add_handler(CommandHandler("channels", list_channels))
    bot.add_handler(CommandHandler("broadcast", broadcast_cmd))
    bot.add_handler(CommandHandler("broadcastall", broadcast_all))
    bot.add_handler(CommandHandler("post", lambda u, c: post_logic(u, c, "anime")))
    bot.add_handler(CommandHandler("movie", lambda u, c: post_logic(u, c, "movie")))
    bot.add_handler(CommandHandler("webseries", lambda u, c: post_logic(u, c, "series")))
    bot.add_handler(CallbackQueryHandler(callback_handler))
    bot.run_polling()
