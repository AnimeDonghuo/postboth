import os, re, threading, requests, html, base64
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from bson import ObjectId

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URI = os.getenv("MONGO_URI") 
OWNER_ID = 1685470205
DB_CHANNEL_ID = -1002617067511
BOT_USERNAME = "auto_test_donghua_bot"

TMDB_KEY = "57c932b753612419360f21e739652579"

client = MongoClient(MONGO_URI)
db = client['AnimePostBot']
files_db = db['StoredFiles'] 
admins_db = db['Admins']
users_db = db['Users']
channels_db = db['Channels']

app = Flask(__name__)
@app.route('/')
def health(): return "Bot Online", 200
def run_web(): app.run(host='0.0.0.0', port=8000)

# --- ADVANCED AI TITLE ENGINE ---
def ai_parse_filename(filename):
    """Greedy extraction of title by stripping suffixes from the right."""
    # Remove extension
    name = re.sub(r'\.(mp4|mkv|zip|rar|ts|mov|avi)$', '', filename, flags=re.I)
    
    # 1. Extract Quality (e.g., 816p, 2K, 4K)
    q_match = re.search(r'(\d+p|2K|4K|8K|HD|FHD|UHD)', name, re.I)
    quality = q_match.group(1) if q_match else "HD"
    
    # 2. Extract Episode/Special Number
    # Pattern looks for Episode 01, Ep 01, Special 1, etc.
    ep_pattern = r'(?:Episode|Ep|Special|S)\s*([\w\d\.]+)'
    ep_match = re.search(ep_pattern, name, re.I)
    episode = ep_match.group(0) if ep_match else "Full"

    # 3. Smart Title Extraction
    # We remove the quality and episode markers from the string to get the title
    clean_title = name
    if q_match: clean_title = clean_title.replace(q_match.group(0), "")
    
    # Remove Episode marker and everything after it
    ep_search = re.search(ep_pattern, clean_title, re.I)
    if ep_search:
        clean_title = clean_title[:ep_search.start()]

    # Clean up noise words and symbols
    noise = [r'HDCAM', r'HDTC', r'WEB-DL', r'BluRay', r'x264', r'x265', r'HEVC', r'CineVood', r'Hindi', r'English', r'Dual']
    for n in noise:
        clean_title = re.sub(r'\b' + n + r'\b', '', clean_title, flags=re.I)

    clean_title = re.sub(r'[\.\-\(\)\[\]_]', ' ', clean_title).strip()
    clean_title = re.sub(r'\s+', ' ', clean_title) # Remove double spaces
    
    # Identify Year
    year_match = re.search(r'\b(19|20)\d{2}\b', name)
    year = year_match.group(0) if year_match else None
    
    return clean_title, episode, quality, year

# --- METADATA ENGINE ---
def get_metadata(query, year=None, med_type="anime"):
    if len(query) < 2: return None
    
    # Filter query for search (Remove "3D" etc)
    search_q = re.sub(r'\b(3D|2D|Part|Official|Special)\b', '', query, flags=re.I).strip()
    
    if med_type == "anime":
        gql = 'query($s:String){Media(search:$s,type:ANIME){title{english romaji}description averageScore genres bannerImage coverImage{extraLarge}}}'
        try:
            res = requests.post('https://graphql.anilist.co', json={'query':gql, 'variables':{'s':search_q}}, timeout=10).json()
            data = res['data']['Media']
            return {
                "title": data['title'].get('english') or data['title'].get('romaji'),
                "desc": html.unescape(re.sub(r'<[^>]+>', '', data.get('description', ''))[:500] + "..."),
                "rating": f"{data.get('averageScore', 80) / 10} / 10",
                "genres": " ".join([f"#{g.replace(' ', '_')}" for g in data.get('genres', [])]),
                "img": data.get('bannerImage') or data['coverImage']['extraLarge']
            }
        except: pass

    # Movie/Series Logic
    tmdb_path = "movie" if med_type == "movie" else "tv"
    try:
        url = f"https://api.themoviedb.org/3/search/{tmdb_path}?api_key={TMDB_KEY}&query={search_q}"
        if year: url += f"&year={year}"
        res = requests.get(url, timeout=10).json()
        if res['results']:
            r = res['results'][0]
            # Fetch genres separately for better accuracy
            det = requests.get(f"https://api.themoviedb.org/3/{tmdb_path}/{r['id']}?api_key={TMDB_KEY}").json()
            genres = " ".join([f"#{g['name'].replace(' ', '')}" for g in det.get('genres', [])])
            img = f"https://image.tmdb.org/t/p/w1280{r.get('backdrop_path')}" if r.get('backdrop_path') else f"https://image.tmdb.org/t/p/w780{r.get('poster_path')}"
            return {
                "title": r.get('title') or r.get('name'),
                "desc": r.get('overview', 'No synopsis available.'),
                "rating": f"{r.get('vote_average', 7.5)} IMDb",
                "genres": genres or "#Movie",
                "img": img,
                "date": r.get('release_date') or r.get('first_air_date') or "N/A"
            }
    except: pass
    return None

# --- PERMISSIONS ---
def is_admin(user_id):
    return user_id == OWNER_ID or admins_db.find_one({"uid": user_id})

# --- COMMANDS ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user_id = u.effective_user.id
    users_db.update_one({"uid": user_id}, {"$set": {"uid": user_id}}, upsert=True)
    
    if c.args:
        try:
            file_id = base64.urlsafe_b64decode((c.args[0] + '=' * (4 - len(c.args[0]) % 4)).encode()).decode()
            data = files_db.find_one({"_id": ObjectId(file_id)})
            if data:
                return await c.bot.copy_message(chat_id=u.effective_chat.id, from_chat_id=DB_CHANNEL_ID, message_id=data['msg_id'])
        except: pass
        return await u.message.reply_text("❌ Link Expired or Invalid.")

    if is_admin(user_id):
        msg = f"🚀 <b>{BOT_USERNAME} Admin Panel</b>\n\n• /post - Anime\n• /movie - Movie\n• /broadcastall\n• /status\n• /addchannel"
        await u.message.reply_text(msg, parse_mode='HTML')

async def status(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id): return
    users = users_db.count_documents({})
    files = files_db.count_documents({})
    db_size = db.command("dbStats")['dataSize'] / (1024*1024)
    await u.message.reply_text(f"📊 <b>Bot Status</b>\n👥 Users: {users}\n📂 Files: {files}\n🗄 Storage: {db_size:.2f}MB / 512MB", parse_mode='HTML')

async def auto_post_init(u: Update, c: ContextTypes.DEFAULT_TYPE, mode):
    if not is_admin(u.effective_user.id): return
    c.user_data.clear()
    c.user_data['is_auto'], c.user_data['mode'], c.user_data['temp_files'] = True, mode, []
    await u.message.reply_text(f"📥 Send files for <b>{mode.upper()}</b>.", parse_mode='HTML')

async def file_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not c.user_data.get('is_auto'): return
    file = u.message.document or u.message.video
    if not file: return
    db_msg = await u.message.copy(chat_id=DB_CHANNEL_ID)
    c.user_data['temp_files'].append({"msg_id": db_msg.message_id, "name": file.file_name or "Video File"})
    kb = [[InlineKeyboardButton("➕ Add More", callback_data="add_more")], [InlineKeyboardButton("✅ Done", callback_data="finish_auto")]]
    await u.message.reply_text(f"📥 Received {len(c.user_data['temp_files'])} files.", reply_markup=InlineKeyboardMarkup(kb))

async def cb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    uid = u.effective_user.id
    if not is_admin(uid): return

    if q.data == "finish_auto":
        files, mode = c.user_data['temp_files'], c.user_data['mode']
        await q.edit_message_text("🔄 AI Searching Metadata...")
        
        # Identify title from the FIRST file
        title, ep, quality, year = ai_parse_filename(files[0]['name'])
        meta = get_metadata(title, year, mode) or {"title": title, "desc": "N/A", "rating": "N/A", "genres": "#Post", "img": None}

        btns = []
        for f in files:
            _, f_ep, f_q, _ = ai_parse_filename(f['name'])
            res = files_db.insert_one({"msg_id": f['msg_id'], "name": f['name']})
            link_id = base64.urlsafe_b64encode(str(res.inserted_id).encode()).decode().rstrip("=")
            link = f"https://t.me/{BOT_USERNAME}?start={link_id}"
            btns.append(InlineKeyboardButton(f"🚀 {f_q} [{f_ep}]", url=link))

        network = "@Donghua_Xin" if mode == "anime" else "@Movies_Hindi_Plus"
        caption = (
            f"<b>{meta['title']}</b>\n"
            f"<b>⟣────────────────────⟢</b>\n"
            f"<b>‣ Audio ⌯ Hindi | English</b>\n"
            f"<b>‣ Rating ⌯ {meta['rating']}</b>\n"
            f"<b>‣ Quality ⌯ {quality}</b>\n"
            f"<b>‣ {'Episode' if mode == 'anime' else 'Released'} ⌯ {ep if mode == 'anime' else meta.get('date', 'N/A')}</b>\n"
            f"<b>‣ Genres ⌯ {meta['genres']}</b>\n"
            f"<b>⟣────────────────────⟢</b>\n"
            f"<b>‣ Synopsis ⌯</b>\n"
            f"<blockquote expandable><b>{meta['desc']}</b></blockquote>\n\n"
            f"🔗 <b>Our Network {network}</b>"
        )
        
        c.user_data['post_data'] = {"cap": caption, "img": meta['img'], "btns": [btns[i:i+2] for i in range(0, len(btns), 2)], "mode": mode}
        c.user_data['selected'] = []
        
        chs = list(channels_db.find({"type": mode}))
        kb = [[InlineKeyboardButton(ch['tag'], callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="send_final")])
        
        if meta['img']: await q.message.reply_photo(photo=meta['img'], caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else: await q.message.reply_text(caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif q.data.startswith("sel_"):
        sel = c.user_data.get('selected', [])
        cid = q.data.replace("sel_", "")
        if cid in sel: sel.remove(cid)
        else: sel.append(cid)
        c.user_data['selected'] = sel
        mode = c.user_data['post_data']['mode']
        chs = list(channels_db.find({"type": mode}))
        kb = [[InlineKeyboardButton(f"{'✅ ' if ch['cid'] in sel else ''}{ch['tag']}", callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="send_final")])
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == "send_final":
        sel = c.user_data.get('selected', [])
        p = c.user_data['post_data']
        count = 0
        for cid in sel:
            try:
                await c.bot.send_photo(chat_id=cid, photo=p['img'], caption=p['cap'], reply_markup=InlineKeyboardMarkup(p['btns']), parse_mode='HTML')
                count += 1
            except: continue
        await q.message.reply_text(f"✅ Sent to {count} channels!")

# --- ORIGINAL COMMANDS RESTORED ---
async def add_channel(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id): return
    if len(c.args) < 3: return await u.message.reply_text("/addchannel [ID] [anime/movie/series] [Tag]")
    channels_db.update_one({"cid": c.args[0]}, {"$set": {"type": c.args[1].lower(), "tag": " ".join(c.args[2:])}}, upsert=True)
    await u.message.reply_text("✅ Channel Added.")

async def broadcast_fast(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id) or not u.message.reply_to_message: return
    chs = list(channels_db.find({}))
    for ch in chs:
        try: await c.bot.copy_message(chat_id=ch['cid'], from_chat_id=u.message.chat_id, message_id=u.message.reply_to_message.message_id)
        except: pass
    await u.message.reply_text("✅ Complete.")

if __name__ == '__main__':
    threading.Thread(target=run_web, daemon=True).start()
    bot = Application.builder().token(TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("status", status))
    bot.add_handler(CommandHandler("post", lambda u, c: auto_post_init(u, c, "anime")))
    bot.add_handler(CommandHandler("movie", lambda u, c: auto_post_init(u, c, "movie")))
    bot.add_handler(CommandHandler("addchannel", add_channel))
    bot.add_handler(CommandHandler("broadcastall", broadcast_fast))
    bot.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, file_handler))
    bot.add_handler(CallbackQueryHandler(cb_handler))
    bot.run_polling()
