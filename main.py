import os, re, threading, requests, html
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pymongo import MongoClient

# --- HARDCODED CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN") # Keep Token in Env for Security
MONGO_URI = os.getenv("MONGO_URI") # Keep Mongo in Env for Security
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# HARDCODED API KEYS
TMDB_KEY = "57c932b753612419360f21e739652579"
OMDB_KEYS = [
    "78aba0e3", "984f89be", "ce245f40", "2e8c5c65", 
    "2451f643", "79803fd4", "f31bb8de"
]

client = MongoClient(MONGO_URI)
db = client['AnimePostBot']

# --- KOYEB HEALTH CHECK ---
app = Flask(__name__)
@app.route('/')
def health(): return "Bot is Online", 200
def run_web(): app.run(host='0.0.0.0', port=8000)

# --- CLEANING & METADATA LOGIC ---
def clean_text(text):
    if not text: return "No synopsis available."
    # Remove HTML tags properly to avoid Telegram Parse Errors
    text = re.sub(r'<(br|p|/p|br /)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    # Remove AniList's "Note:" section often found in Donghuas
    text = re.sub(r'Note:.*', '', text, flags=re.IGNORECASE).strip()
    return text

def get_omdb_rating(title):
    for key in OMDB_KEYS:
        try:
            r = requests.get(f"http://www.omdbapi.com/?t={title}&apikey={key}").json()
            if r.get('Response') == 'True':
                return r.get('imdbRating', '8.4')
        except: continue
    return "8.4"

def get_metadata(query):
    # 1. Try AniList First (Best for Donghua)
    gql = 'query($s:String){Media(search:$s,type:ANIME){title{english romaji}descriptionaverageScoregenresbannerImagecoverImage{extraLarge}}}'
    try:
        res = requests.post('https://graphql.anilist.co', json={'query':gql, 'variables':{'s':query}}).json()
        data = res['data']['Media']
        if data:
            return {
                "title": data['title'].get('english') or data['title'].get('romaji'),
                "desc": clean_text(data.get('description')),
                "rating": str(data.get('averageScore', 84) / 10),
                "genres": data.get('genres', []),
                "img": data.get('bannerImage') or data['coverImage']['extraLarge']
            }
    except: pass

    # 2. Fallback to TMDB (Best for Movies/Series)
    try:
        search = requests.get(f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_KEY}&query={query}").json()
        if search['results']:
            res = search['results'][0]
            return {
                "title": res.get('title') or res.get('name'),
                "desc": clean_text(res.get('overview')),
                "rating": str(res.get('vote_average', 8.4)),
                "genres": ["Action", "Fantasy"],
                "img": f"https://image.tmdb.org/t/p/original{res.get('backdrop_path')}"
            }
    except: pass
    return None

# --- BOT HANDLERS ---
async def start(u, c):
    await u.message.reply_text("<b>🚀 Bot Started Successfully!</b>\n\n<b>Commands:</b>\n/addchannel [ID] [Tag]\n/channels - List Channels\n/post Name | Ep | [Img] | Links\n/broadcastall - Reply to a message", parse_mode='HTML')

async def add_channel(u, c):
    if len(c.args) < 2: return await u.message.reply_text("<b>Format:</b> /addchannel -100xxx TagName")
    db.channels.update_one({"uid": u.effective_user.id, "cid": c.args[0]}, {"$set": {"tag": " ".join(c.args[1:])}}, upsert=True)
    await u.message.reply_text("<b>✅ Channel Added!</b>", parse_mode='HTML')

async def list_channels(u, c):
    chs = list(db.channels.find({"uid": u.effective_user.id}))
    if not chs: return await u.message.reply_text("No channels added.")
    kb = [[InlineKeyboardButton(f"❌ Remove {ch['tag']}", callback_data=f"rm_{ch['cid']}")] for ch in chs]
    await u.message.reply_text("<b>Manage your channels:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def post_cmd(u, c):
    try:
        # Split by |
        parts = [p.strip() for p in u.message.text.replace('/post','').split('|')]
        name_input = parts[0]
        ep = parts[1] if len(parts) > 1 else "01"
        
        user_img = None
        links_text = ""
        
        # Determine if Img is provided
        if len(parts) >= 4:
            user_img = parts[2]
            links_text = parts[3]
        elif len(parts) == 3:
            if parts[2].startswith("http") and "p:" not in parts[2]: user_img = parts[2]
            else: links_text = parts[2]

        # Fetch Meta & Handle Image Memory
        meta = db.cache.find_one({"n": name_input.lower()}) or get_metadata(name_input)
        if not meta: return await u.message.reply_text("❌ Could not find details.")
        db.cache.update_one({"n": name_input.lower()}, {"$set": meta}, upsert=True)

        if user_img:
            db.media.update_one({"n": name_input.lower()}, {"$set": {"img": user_img}}, upsert=True)
            final_img = user_img
        else:
            saved = db.media.find_one({"n": name_input.lower()})
            final_img = saved['img'] if saved else meta['img']

        # Formatting Content
        imdb = get_omdb_rating(name_input)
        mid = len(meta['desc'])//2
        synopsis = f"{meta['desc'][:mid]}<tg-spoiler>{meta['desc'][mid:]}</tg-spoiler>"
        
        quals = re.findall(r'(\d+p|4K)', links_text, re.I)
        q_label = " | ".join(sorted(list(set(quals)))) if quals else "480p | 720p | 1080p"

        caption = (
            f"<b>{meta['title']}</b>\n"
            f"<b>⟣────────────────────⟢</b>\n"
            f"<b>‣ Audio ⌯ [Chinese | Eng-Sub]</b>\n"
            f"<b>‣ Rating ⌯ {imdb} IMDB | 96% User Score</b>\n"
            f"<b>‣ Quality ⌯ {q_label}</b>\n"
            f"<b>‣ Episode ⌯ {ep}</b>\n"
            f"<b>‣ Genres ⌯ {', '.join(['#'+g.replace(' ','_') for g in meta['genres']])}</b>\n"
            f"<b>⟣────────────────────⟢</b>\n"
            f"<b>‣ Synopsis ⌯</b>\n"
            f"<blockquote><b>{synopsis}</b></blockquote>\n"
            f"<b>🔗 Our Network @Donghua_Xin</b>"
        )

        # Build Link Buttons (Grid style 2 per row)
        btns = []
        links_found = re.findall(r'(\d+p|4K)\s*:\s*(https?://\S+)', links_text, re.I)
        for label, url in links_found:
            btns.append(InlineKeyboardButton(f"🚀 {label.upper()} Download", url=url))
        
        # Save temp data for this user
        c.user_data['post_data'] = {
            "caption": caption, 
            "image": final_img, 
            "buttons": [btns[i:i + 2] for i in range(0, len(btns), 2)]
        }
        c.user_data['selected_channels'] = []

        # Multi-Select Keyboard
        chs = list(db.channels.find({"uid": u.effective_user.id}))
        kb = [[InlineKeyboardButton(ch['tag'], callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])

        await u.message.reply_photo(photo=final_img, caption=caption + "\n\n<b>Select channels to post:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except Exception as e: await u.message.reply_text(f"Error: {e}")

async def callback_handler(u, c):
    q = u.callback_query
    uid = u.effective_user.id
    data = q.data

    if data.startswith("rm_"):
        db.channels.delete_one({"uid": uid, "cid": data.replace("rm_","")})
        return await q.edit_message_text("Channel Removed.")

    if data.startswith("sel_"):
        cid = data.replace("sel_", "")
        selected = c.user_data.get('selected_channels', [])
        if cid in selected: selected.remove(cid)
        else: selected.append(cid)
        c.user_data['selected_channels'] = selected
        
        # Refresh buttons with checkmarks
        chs = list(db.channels.find({"uid": uid}))
        new_kb = []
        for ch in chs:
            mark = "✅ " if ch['cid'] in selected else ""
            new_kb.append([InlineKeyboardButton(f"{mark}{ch['tag']}", callback_data=f"sel_{ch['cid']}")])
        new_kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_kb))

    if data == "final_send":
        p = c.user_data.get('post_data')
        sel = c.user_data.get('selected_channels', [])
        if not sel: return await q.answer("❌ Select at least one channel!", show_alert=True)
        
        for cid in sel:
            try: await c.bot.send_photo(chat_id=cid, photo=p['image'], caption=p['caption'], reply_markup=InlineKeyboardMarkup(p['buttons']), parse_mode='HTML')
            except: pass
        await q.edit_message_caption("<b>✅ Posted Successfully!</b>", parse_mode='HTML')

async def broadcast(u, c):
    if u.effective_user.id != OWNER_ID or not u.message.reply_to_message:
        return await u.message.reply_text("Restricted Command.")
    
    chs = list(db.channels.find({"uid": u.effective_user.id}))
    for ch in chs:
        try: await c.bot.copy_message(chat_id=ch['cid'], from_chat_id=u.message.chat_id, message_id=u.message.reply_to_message.message_id)
        except: pass
    await u.message.reply_text("Broadcast Done.")

if __name__ == '__main__':
    threading.Thread(target=run_web, daemon=True).start()
    bot = Application.builder().token(TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("addchannel", add_channel))
    bot.add_handler(CommandHandler("channels", list_channels))
    bot.add_handler(CommandHandler("post", post_cmd))
    bot.add_handler(CommandHandler("broadcastall", broadcast))
    bot.add_handler(CallbackQueryHandler(callback_handler))
    bot.run_polling()
