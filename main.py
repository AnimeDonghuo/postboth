import os, re, threading, requests, html, base64
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from bson import ObjectId # Added for permanent link fixing

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URI = os.getenv("MONGO_URI") 
OWNER_ID = int(os.getenv("OWNER_ID", "1685470205"))
DB_CHANNEL_ID = int(os.getenv("DB_CHANNEL_ID", "-1002617067511")) 
BOT_USERNAME = os.getenv("BOT_USERNAME", "auto_test_donghua_bot")

TMDB_KEY = "57c932b753612419360f21e739652579"
OMDB_KEYS = ["78aba0e3", "984f89be", "ce245f40", "2e8c5c65", "2451f643", "79803fd4", "f31bb8de"]

client = MongoClient(MONGO_URI)
db = client['AnimePostBot']
files_db = db['StoredFiles'] 
admins_db = db['Admins']

app = Flask(__name__)
@app.route('/')
def health(): return "Bot is Online", 200
def run_web(): app.run(host='0.0.0.0', port=8000)

# --- UTILS ---
def is_admin(user_id):
    if user_id == OWNER_ID: return True
    return admins_db.find_one({"uid": user_id}) is not None

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
    # Clean the search query (Removes technical noise that confuses the API)
    search_query = re.sub(r'\s(s\d+|season\s?\d+)', '', query, flags=re.I).strip()
    search_query = re.sub(r'\b(3D|2D|4K|Full|BD|1080p|720p|480p|800p|10bit|x265|HEVC|Multi|WEB-DL)\b', '', search_query, flags=re.I).strip()
    
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

# --- FILE STORE UTILS ---
def encode_id(db_id):
    return base64.urlsafe_b64encode(str(db_id).encode()).decode().rstrip("=")

def decode_id(code):
    padding = '=' * (4 - len(code) % 4)
    return base64.urlsafe_b64decode((code + padding).encode()).decode()

def parse_auto_name(filename):
    filename = re.sub(r'\.(mp4|mkv|zip|rar|ts)$', '', filename, flags=re.I)
    # Extracts quality like 800p, 1080p
    quality_match = re.search(r'(\d+p|4K)', filename, re.I)
    # Extracts episode like Episode 77 or Ep 77
    ep_match = re.search(r'(?:Episode|Ep)\s*(\d+)', filename, re.I)
    
    quality = quality_match.group(1) if quality_match else "HD"
    episode = ep_match.group(1) if ep_match else "Full"
    
    # Title is everything before the Episode/Quality part
    title = filename
    if ep_match: title = title.split(ep_match.group(0))[0]
    elif quality_match: title = title.split(quality_match.group(0))[0]
    
    title = re.sub(r'[-\s]+$', '', title).strip()
    return title, episode, quality

# --- BOT HANDLERS ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    # 1. Handle File Retrieval Links
    if c.args: 
        try:
            file_id_str = decode_id(c.args[0])
            # Critical Fix: Convert string ID back to ObjectId for Mongo search
            data = files_db.find_one({"_id": ObjectId(file_id_str)})
            if data:
                return await c.bot.copy_message(chat_id=u.effective_chat.id, from_chat_id=DB_CHANNEL_ID, message_id=data['msg_id'])
        except Exception as e:
            print(f"Start Error: {e}")
        return await u.message.reply_text("❌ Link Expired or Invalid.")

    # 2. Handle Admin vs User Welcome
    if is_admin(u.effective_user.id):
        msg = (
            "<b>🚀 Admin Control Panel:</b>\n\n"
            "<b>1. /post</b> - Auto Anime (Donghua)\n"
            "<b>2. /movie</b> - Auto Movie\n"
            "<b>3. /webseries</b> - Manual (Name|Ep|Img|Audio|Links)\n\n"
            "<b>Admin Management:</b>\n"
            "• /addadmin [User_ID]\n"
            "• /admins - List all admins\n"
            "• /addchannel [ID] [anime/movie/series] [Tag]\n"
            "• /channels - List channels"
        )
        await u.message.reply_text(msg, parse_mode='HTML')
    else:
        await u.message.reply_text("<b>Welcome to the File Store Bot!</b>\nSearch for your favorite anime in our channels.", parse_mode='HTML')

# --- ADMIN COMMANDS ---
async def add_admin(u, c):
    if u.effective_user.id != OWNER_ID: return
    if not c.args: return await u.message.reply_text("Usage: /addadmin 12345678")
    admins_db.update_one({"uid": int(c.args[0])}, {"$set": {"uid": int(c.args[0])}}, upsert=True)
    await u.message.reply_text("✅ User added as Admin!")

async def list_admins(u, c):
    if not is_admin(u.effective_user.id): return
    admins = list(admins_db.find())
    msg = f"<b>Admins:</b>\n• {OWNER_ID} (Owner)\n"
    for a in admins: msg += f"• {a['uid']}\n"
    await u.message.reply_text(msg, parse_mode='HTML')

# --- AUTO POST LOGIC ---
async def auto_post_start(u: Update, c: ContextTypes.DEFAULT_TYPE, mode):
    if not is_admin(u.effective_user.id): return
    c.user_data['is_auto'] = True
    c.user_data['auto_mode'] = mode
    c.user_data['temp_files'] = []
    await u.message.reply_text(f"📥 **Auto Mode: {mode.upper()}**\nPlease send your file(s) one by one.")

async def file_receiver(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not c.user_data.get('is_auto'): return
    file = u.message.document or u.message.video
    if not file: return

    # Permanent copy to storage channel
    db_msg = await u.message.copy(chat_id=DB_CHANNEL_ID)
    c.user_data['temp_files'].append({"msg_id": db_msg.message_id, "name": file.file_name if hasattr(file, 'file_name') else "Video File"})
    
    kb = [[InlineKeyboardButton("➕ Send More", callback_data="add_more")], [InlineKeyboardButton("✅ Done", callback_data="auto_done")]]
    await u.message.reply_text(f"📥 Added: {len(c.user_data['temp_files'])} files", reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    uid = u.effective_user.id
    if not is_admin(uid): return

    if q.data == "add_more":
        await q.answer("Ready for next file")
    
    elif q.data == "auto_done":
        files = c.user_data.get('temp_files', [])
        mode = c.user_data.get('auto_mode')
        await q.edit_message_text("🔄 Processing metadata...")
        
        raw_title, ep, quality = parse_auto_name(files[0]['name'])
        meta = get_metadata(raw_title, mode) or {"title": raw_title, "desc": "N/A", "rating": "8.0", "genres": [], "img": None}
        
        btns = []
        for f in files:
            _, f_ep, f_q = parse_auto_name(f['name'])
            res = files_db.insert_one({"msg_id": f['msg_id'], "name": f['name']})
            # Generate permanent link
            link = f"https://t.me/{BOT_USERNAME}?start={encode_id(res.inserted_id)}"
            btns.append(InlineKeyboardButton(f"🚀 {f_q} [Ep {f_ep}]", url=link))
            
        ratings = get_omdb_rating(meta['title'])
        template_data = {
            "title": meta['title'], "audio": "Hindi | English" if mode == "movie" else "Chinese | Eng-Sub",
            "rating": f"{ratings['imdb']} IMDb" if mode == "movie" else f"{meta['rating']} / 10",
            "quality": "480p | 720p | 1080p", "extra_label": "Released" if mode == "movie" else "Episode",
            "extra_val": ratings['year'] if mode == "movie" else ep,
            "genres": " ".join(['#'+g.replace(' ','_') for g in meta.get('genres', [])]), "synopsis": meta['desc']
        }
        c.user_data['post_data'] = {"caption": build_caption(template_data, mode), "image": meta['img'], "buttons": [btns[i:i + 2] for i in range(0, len(btns), 2)], "mode": mode}
        
        chs = list(db.channels.find({"uid": OWNER_ID, "type": mode}))
        kb = [[InlineKeyboardButton(ch['tag'], callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
        if meta['img']: await q.message.reply_photo(photo=meta['img'], caption=c.user_data['post_data']['caption'], reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else: await q.message.reply_text(c.user_data['post_data']['caption'], reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif q.data.startswith("sel_"):
        sel = c.user_data.get('selected_channels', [])
        cid = q.data.replace("sel_", "")
        if cid in sel: sel.remove(cid)
        else: sel.append(cid)
        c.user_data['selected_channels'] = sel
        mode = c.user_data.get('post_data', {}).get('mode')
        chs = list(db.channels.find({"uid": OWNER_ID, "type": mode})) if mode else list(db.channels.find({"uid": OWNER_ID}))
        new_kb = [[InlineKeyboardButton(f"{'✅ ' if ch['cid'] in sel else ''}{ch['tag']}", callback_data=f"sel_{ch['cid']}")] for ch in chs]
        new_kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_kb))

    elif q.data == "final_send":
        sel = c.user_data.get('selected_channels', [])
        post = c.user_data.get('post_data')
        bc_mid = c.user_data.get('bc_msg')
        count = 0
        for cid in sel:
            try:
                if bc_mid: await c.bot.copy_message(chat_id=cid, from_chat_id=c.user_data['bc_chat'], message_id=bc_mid)
                else: await c.bot.send_photo(chat_id=cid, photo=post['image'], caption=post['caption'], reply_markup=InlineKeyboardMarkup(post['buttons']), parse_mode='HTML')
                count += 1
            except: continue
        await q.message.reply_text(f"✅ Sent to {count} channels!")

# --- ORIGINAL MANUAL & BROADCAST ---
async def webseries_manual(u, c):
    if not is_admin(u.effective_user.id): return
    try:
        raw = u.message.text.split(None, 1)[1]
        parts = [p.strip() for p in raw.split('|')]
        meta = get_metadata(parts[0], "series")
        template_data = {"title": meta['title'], "audio": parts[3] if len(parts)>3 else "Hindi", "rating": "7.5 IMDb", "quality": "HD", "extra_label": "Episode", "extra_val": parts[1], "genres": " ".join(['#'+g.replace(' ','_') for g in meta.get('genres', [])]), "synopsis": meta['desc']}
        caption = build_caption(template_data, "series")
        btns = [InlineKeyboardButton(f"Download", url=l) for l in re.findall(r'https?://\S+', parts[-1])]
        c.user_data['post_data'] = {"caption": caption, "image": meta['img'], "buttons": [btns[i:i + 2] for i in range(0, len(btns), 2)], "mode": "series"}
        chs = list(db.channels.find({"uid": OWNER_ID, "type": "series"}))
        kb = [[InlineKeyboardButton(ch['tag'], callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
        await u.message.reply_photo(photo=meta['img'], caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except Exception as e: await u.message.reply_text(f"Error: {e}")

async def broadcast_cmd(u, c):
    if not is_admin(u.effective_user.id) or not u.message.reply_to_message: return
    c.user_data['bc_msg'] = u.message.reply_to_message.message_id
    c.user_data['bc_chat'] = u.message.chat_id
    c.user_data['selected_channels'] = []
    chs = list(db.channels.find({"uid": OWNER_ID}))
    kb = [[InlineKeyboardButton(f"[{ch['type']}] {ch['tag']}", callback_data=f"sel_{ch['cid']}")] for ch in chs]
    kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
    await u.message.reply_text("Broadcast Select:", reply_markup=InlineKeyboardMarkup(kb))

async def broadcast_all(u, c):
    if not is_admin(u.effective_user.id) or not u.message.reply_to_message: return
    chs = list(db.channels.find({"uid": OWNER_ID}))
    for ch in chs:
        try: await c.bot.copy_message(chat_id=ch['cid'], from_chat_id=u.message.chat_id, message_id=u.message.reply_to_message.message_id)
        except: continue
    await u.message.reply_text("✅ Done.")

async def add_channel(u, c):
    if not is_admin(u.effective_user.id): return
    if len(c.args) < 3: return await u.message.reply_text("Usage: /addchannel ID anime/movie/series Tag")
    db.channels.update_one({"uid": OWNER_ID, "cid": c.args[0]}, {"$set": {"type": c.args[1].lower(), "tag": " ".join(c.args[2:])}}, upsert=True)
    await u.message.reply_text("✅ Added!")

if __name__ == '__main__':
    threading.Thread(target=run_web, daemon=True).start()
    bot = Application.builder().token(TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("post", lambda u, c: auto_post_start(u, c, "anime")))
    bot.add_handler(CommandHandler("movie", lambda u, c: auto_post_start(u, c, "movie")))
    bot.add_handler(CommandHandler("webseries", webseries_manual))
    bot.add_handler(CommandHandler("broadcast", broadcast_cmd))
    bot.add_handler(CommandHandler("broadcastall", broadcast_all))
    bot.add_handler(CommandHandler("addadmin", add_admin))
    bot.add_handler(CommandHandler("admins", list_admins))
    bot.add_handler(CommandHandler("addchannel", add_channel))
    bot.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, file_receiver))
    bot.add_handler(CallbackQueryHandler(callback_handler))
    bot.run_polling()
