import os, re, threading, requests, html, base64
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from bson import ObjectId

# --- CONFIGURATION (HARDCODED) ---
TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URI = os.getenv("MONGO_URI") 
OWNER_ID = 1685470205
DB_CHANNEL_ID = -1002617067511
BOT_USERNAME = "auto_test_donghua_bot"

TMDB_KEY = "57c932b753612419360f21e739652579"
OMDB_KEYS = ["78aba0e3", "984f89be", "ce245f40"]

client = MongoClient(MONGO_URI)
db = client['AnimePostBot']
files_db = db['StoredFiles'] 
admins_db = db['Admins']
users_db = db['Users']
channels_db = db['Channels'] # Replacing original local list with persistent DB

app = Flask(__name__)
@app.route('/')
def health(): return "Bot Online", 200
def run_web(): app.run(host='0.0.0.0', port=8000)

# --- AI SMART PARSER ---
def smart_clean_title(filename):
    """Deep cleans filenames to extract only the name and year."""
    name = re.sub(r'\.(mp4|mkv|zip|rar|ts|mov|avi)$', '', filename, flags=re.I)
    
    # Extract Year
    year_match = re.search(r'\b(19|20)\d{2}\b', name)
    year = year_match.group(0) if year_match else None
    
    # Noise Keywords (Stop Words)
    noise = [
        r'HDTC', r'HDCAM', r'WEB-DL', r'BluRay', r'HDRip', r'x264', r'x265', r'HEVC', r'10bit', 
        r'AAC', r'DDP', r'HC', r'ESub', r'CineVood', r'Vegamovies', r'PSA', r'GalaxyRG',
        r'Multi', r'Hindi', r'English', r'Dual', r'Audio', r'1080p', r'720p', r'480p', r'2160p', r'4K',
        r'Special', r'Episode', r'Ep\b', r'Season', r'S\d+'
    ]
    
    clean_name = name
    for pattern in noise:
        match = re.search(r'\b' + pattern + r'\b', clean_name, re.I)
        if match:
            clean_name = clean_name[:match.start()]
    
    if year and year in clean_name:
        clean_name = clean_name.split(year)[0]
        
    clean_name = re.sub(r'[\.\-\(\)\[\]_]', ' ', clean_name).strip()
    return clean_name, year

def parse_auto_info(filename):
    clean_name, year = smart_clean_title(filename)
    ep_match = re.search(r'(?:Episode|Ep|Special)\s*([\w\d\.]+)', filename, re.I)
    episode = ep_match.group(0).strip() if ep_match else "Full"
    quality_match = re.search(r'(\d+p|4K)', filename, re.I)
    quality = quality_match.group(1) if quality_match else "HD"
    return clean_name, episode, quality, year

# --- METADATA ENGINE ---
def get_metadata(query, year=None, med_type="anime"):
    search_q = re.sub(r'\b(3D|2D|Part|Special|Official)\b', '', query, flags=re.I).strip()
    if med_type == "anime":
        gql = 'query($s:String){Media(search:$s,type:ANIME){title{english romaji}description averageScore genres bannerImage coverImage{extraLarge}}}'
        try:
            res = requests.post('https://graphql.anilist.co', json={'query':gql, 'variables':{'s':search_q}}).json()
            data = res['data']['Media']
            return {
                "title": data['title'].get('english') or data['title'].get('romaji'),
                "desc": html.unescape(re.sub(r'<[^>]+>', '', data.get('description', ''))[:450] + "..."),
                "rating": str(data.get('averageScore', 80) / 10),
                "genres": " ".join([f"#{g}" for g in data.get('genres', [])]),
                "img": data.get('bannerImage') or data['coverImage']['extraLarge']
            }
        except: pass

    tmdb_type = "movie" if med_type == "movie" else "tv"
    try:
        url = f"https://api.themoviedb.org/3/search/{tmdb_type}?api_key={TMDB_KEY}&query={search_q}"
        if year: url += f"&year={year}"
        search = requests.get(url).json()
        if search['results']:
            res = search['results'][0]
            details = requests.get(f"https://api.themoviedb.org/3/{tmdb_type}/{res['id']}?api_key={TMDB_KEY}").json()
            genres = " ".join([f"#{g['name'].replace(' ', '')}" for g in details.get('genres', [])])
            img = f"https://image.tmdb.org/t/p/w1280{res.get('backdrop_path')}" if res.get('backdrop_path') else f"https://image.tmdb.org/t/p/w780{res.get('poster_path')}" if res.get('poster_path') else None
            return {
                "title": res.get('title') or res.get('name'),
                "desc": res.get('overview', 'No synopsis available.'),
                "rating": str(res.get('vote_average', 7.5)),
                "genres": genres,
                "img": img,
                "date": res.get('release_date') or res.get('first_air_date') or "N/A"
            }
    except: pass
    return None

def build_caption(t, mode):
    footer = "\n\n🔗 𝗢𝘂𝗿 𝗡𝗲𝘁𝘄𝗼𝗿𝗸 " + ("@Donghua_Xin" if mode == "anime" else "@Movies_Hindi_Plus")
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
    return f"{header}<blockquote expandable><b>{t['synopsis']}</b></blockquote>{footer}"

# --- PERMISSION SYSTEM ---
def is_admin(user_id):
    return user_id == OWNER_ID or admins_db.find_one({"uid": user_id})

# --- COMMAND HANDLERS ---
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
        msg = (
            f"<b>🚀 {BOT_USERNAME} Admin Panel</b>\n\n"
            "• /post - Auto Anime\n"
            "• /movie - Auto Movie\n"
            "• /webseries - Manual Series\n"
            "• /broadcast - Select Broadcast\n"
            "• /broadcastall - Fast Broadcast\n"
            "• /addadmin - Add Admin ID\n"
            "• /addchannel - Add Channel [ID type tag]\n"
            "• /status - DB Stats"
        )
        await u.message.reply_text(msg, parse_mode='HTML')
    else:
        await u.message.reply_text("<b>Welcome to Auto File Store!</b>")

async def status(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id): return
    users = users_db.count_documents({})
    files = files_db.count_documents({})
    db_stats = db.command("dbStats")
    storage = db_stats['dataSize'] / (1024*1024)
    await u.message.reply_text(f"📊 <b>Bot Status</b>\n👥 Subscribers: {users}\n📂 Stored Files: {files}\n🗄 Storage: {storage:.2f}MB / 512MB", parse_mode='HTML')

# --- POST FLOWS ---
async def auto_post_init(u: Update, c: ContextTypes.DEFAULT_TYPE, mode):
    if not is_admin(u.effective_user.id): return
    c.user_data.clear()
    c.user_data['is_auto'] = True
    c.user_data['auto_mode'] = mode
    c.user_data['temp_files'] = []
    await u.message.reply_text(f"📥 Send files for <b>{mode.upper()}</b>.", parse_mode='HTML')

async def file_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not c.user_data.get('is_auto'): return
    file = u.message.document or u.message.video
    if not file: return
    db_msg = await u.message.copy(chat_id=DB_CHANNEL_ID)
    c.user_data['temp_files'].append({"msg_id": db_msg.message_id, "name": file.file_name or "Video File"})
    kb = [[InlineKeyboardButton("➕ Add More", callback_data="add_more")], [InlineKeyboardButton("✅ Done", callback_data="finish_auto")]]
    await u.message.reply_text(f"📥 Received: {len(c.user_data['temp_files'])}", reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    if not is_admin(u.effective_user.id): return

    if q.data == "finish_auto":
        files = c.user_data['temp_files']
        mode = c.user_data['auto_mode']
        await q.edit_message_text("🔄 AI Searching Metadata...")
        
        title, ep, quality, year = parse_auto_info(files[0]['name'])
        meta = get_metadata(title, year, mode) or {"title": title, "desc": "N/A", "rating": "N/A", "genres": "", "img": None}

        btns = []
        for f in files:
            _, f_ep, f_q, _ = parse_auto_info(f['name'])
            res = files_db.insert_one({"msg_id": f['msg_id'], "name": f['name']})
            link_id = base64.urlsafe_b64encode(str(res.inserted_id).encode()).decode().rstrip("=")
            link = f"https://t.me/{BOT_USERNAME}?start={link_id}"
            btns.append(InlineKeyboardButton(f"🚀 {f_q} [{f_ep}]", url=link))

        t_data = {
            "title": meta['title'], "audio": "Hindi | English", "rating": meta['rating'],
            "quality": quality, "extra_label": "Episode" if mode == "anime" else "Released",
            "extra_val": ep if mode == "anime" else meta.get('date', 'N/A'),
            "genres": meta['genres'], "synopsis": meta['desc']
        }
        caption = build_caption(t_data, mode)
        c.user_data['post_data'] = {"caption": caption, "image": meta['img'], "buttons": [btns[i:i + 2] for i in range(0, len(btns), 2)], "mode": mode}
        
        chs = list(channels_db.find({"type": mode}))
        kb = [[InlineKeyboardButton(ch['tag'], callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
        if meta['img']: await q.message.reply_photo(photo=meta['img'], caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else: await q.message.reply_text(caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif q.data.startswith("sel_"):
        sel = c.user_data.get('selected_channels', [])
        cid = q.data.replace("sel_", "")
        if cid in sel: sel.remove(cid)
        else: sel.append(cid)
        c.user_data['selected_channels'] = sel
        mode = c.user_data.get('post_data', {}).get('mode', 'all')
        chs = list(channels_db.find({"type": mode})) if mode != 'all' else list(channels_db.find({}))
        kb = [[InlineKeyboardButton(f"{'✅ ' if ch['cid'] in sel else ''}{ch['tag']}", callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == "final_send":
        sel = c.user_data.get('selected_channels', [])
        post = c.user_data.get('post_data')
        bc_id = c.user_data.get('bc_msg')
        count = 0
        for cid in sel:
            try:
                if bc_id: await c.bot.copy_message(chat_id=cid, from_chat_id=c.user_data['bc_chat'], message_id=bc_id)
                else: await c.bot.send_photo(chat_id=cid, photo=post['image'], caption=post['caption'], reply_markup=InlineKeyboardMarkup(post['buttons']), parse_mode='HTML')
                count += 1
            except: continue
        await q.message.reply_text(f"✅ Sent to {count} channels!")

# --- ORIGINAL MANUAL & ADMIN COMMANDS ---
async def webseries_manual(u, c):
    if not is_admin(u.effective_user.id): return
    try:
        raw = u.message.text.split(None, 1)[1]
        parts = [p.strip() for p in raw.split('|')]
        meta = get_metadata(parts[0], None, "series")
        t_data = {"title": meta['title'], "audio": parts[3] if len(parts)>3 else "Hindi", "rating": "7.5", "quality": "HD", "extra_label": "Season", "extra_val": parts[1], "genres": "#Series", "synopsis": meta['desc']}
        caption = build_caption(t_data, "series")
        c.user_data['post_data'] = {"caption": caption, "image": meta['img'], "buttons": [], "mode": "series"}
        chs = list(channels_db.find({"type": "series"}))
        kb = [[InlineKeyboardButton(ch['tag'], callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
        await u.message.reply_photo(photo=meta['img'], caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except: await u.message.reply_text("Format: Name | Season | Img | Audio | Links")

async def broadcast_select(u, c):
    if not is_admin(u.effective_user.id) or not u.message.reply_to_message: return
    c.user_data['bc_msg'] = u.message.reply_to_message.message_id
    c.user_data['bc_chat'] = u.message.chat_id
    c.user_data['post_data'] = {'mode': 'all'}
    c.user_data['selected_channels'] = []
    chs = list(channels_db.find({}))
    kb = [[InlineKeyboardButton(f"[{ch['type']}] {ch['tag']}", callback_data=f"sel_{ch['cid']}")] for ch in chs]
    kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="final_send")])
    await u.message.reply_text("Select channels for broadcast:", reply_markup=InlineKeyboardMarkup(kb))

async def broadcast_fast(u, c):
    if not is_admin(u.effective_user.id) or not u.message.reply_to_message: return
    chs = list(channels_db.find({}))
    for ch in chs:
        try: await c.bot.copy_message(chat_id=ch['cid'], from_chat_id=u.message.chat_id, message_id=u.message.reply_to_message.message_id)
        except: pass
    await u.message.reply_text("✅ Fast Broadcast Complete.")

async def add_admin(u, c):
    if u.effective_user.id != OWNER_ID: return
    if not c.args: return
    admins_db.update_one({"uid": int(c.args[0])}, {"$set": {"uid": int(c.args[0])}}, upsert=True)
    await u.message.reply_text("✅ Admin Added.")

async def add_channel(u, c):
    if not is_admin(u.effective_user.id): return
    if len(c.args) < 3: return await u.message.reply_text("/addchannel [ID] [anime/movie/series] [Tag]")
    channels_db.update_one({"cid": c.args[0]}, {"$set": {"type": c.args[1].lower(), "tag": " ".join(c.args[2:])}}, upsert=True)
    await u.message.reply_text("✅ Channel Added.")

if __name__ == '__main__':
    threading.Thread(target=run_web, daemon=True).start()
    bot = Application.builder().token(TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("status", status))
    bot.add_handler(CommandHandler("addadmin", add_admin))
    bot.add_handler(CommandHandler("addchannel", add_channel))
    bot.add_handler(CommandHandler("broadcast", broadcast_select))
    bot.add_handler(CommandHandler("broadcastall", broadcast_fast))
    bot.add_handler(CommandHandler("post", lambda u, c: auto_post_init(u, c, "anime")))
    bot.add_handler(CommandHandler("movie", lambda u, c: auto_post_init(u, c, "movie")))
    bot.add_handler(CommandHandler("webseries", webseries_manual))
    bot.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, file_handler))
    bot.add_handler(CallbackQueryHandler(callback_handler))
    bot.run_polling()
