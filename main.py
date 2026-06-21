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
channels_db = db['Channels']

app = Flask(__name__)
@app.route('/')
def health(): return "Bot Online", 200
def run_web(): app.run(host='0.0.0.0', port=8000)

# --- THE SMART AI PARSER (FIXED) ---
def advanced_title_cleaner(filename):
    # Remove file extension
    name = re.sub(r'\.(mp4|mkv|zip|rar|ts|mov|avi)$', '', filename, flags=re.I)
    
    # 1. Capture Episode and Quality before cleaning
    q_match = re.search(r'(\d+p|2K|4K|HD|FHD|q)', name, re.I)
    quality = q_match.group(1) if q_match else "HD"
    
    ep_match = re.search(r'(?:Episode|episode|Ep|ep|Special|S)\s*([\w\d\.]+)', name, re.I)
    episode = ep_match.group(0) if ep_match else "Full"

    # 2. Extract Year
    year_match = re.search(r'\b(19|20)\d{2}\b', name)
    year = year_match.group(0) if year_match else None

    # 3. SMART SPLIT: Find where the title ends
    # We split by common dividers and check where "noise" starts
    parts = re.split(r'[\s\.\-\(\)\[\]]', name)
    title_parts = []
    noise_keywords = {'HDCAM', 'HDTC', 'WEB', 'DL', 'BluRay', 'x264', 'x265', 'HEVC', 'CineVood', 'Hindi', 'English', 'Dual', 'Audio', 'Episode', 'Ep', 'Special', 'Esub', 'HC'}
    
    for p in parts:
        if not p: continue
        # Stop if we hit a year, a quality, or a noise word
        if (year and p == year) or (p.lower() in [k.lower() for k in noise_keywords]) or re.match(r'\d+p', p, re.I):
            break
        title_parts.append(p)
    
    clean_title = " ".join(title_parts).strip()
    
    # Fallback if cleaning is too aggressive
    if not clean_title or len(clean_title) < 3:
        clean_title = name.split('-')[0].split('(')[0].strip()

    return clean_title, episode, quality, year

# --- METADATA ENGINE ---
def get_metadata(query, year=None, med_type="anime"):
    # Strip "3D" or "2D" for search but keep "Lord of the Mysteries"
    search_q = re.sub(r'\b(Part|Official)\b', '', query, flags=re.I).strip()
    
    if med_type == "anime":
        gql = 'query($s:String){Media(search:$s,type:ANIME){title{english romaji}description averageScore genres bannerImage coverImage{extraLarge}}}'
        try:
            res = requests.post('https://graphql.anilist.co', json={'query':gql, 'variables':{'s':search_q}}, timeout=10).json()
            data = res['data']['Media']
            return {
                "title": data['title'].get('english') or data['title'].get('romaji'),
                "desc": html.unescape(re.sub(r'<[^>]+>', '', data.get('description', ''))[:180] + "..."),
                "rating": f"{data.get('averageScore', 80) / 10} / 10",
                "genres": " ".join([f"#{g.replace(' ', '_')}" for g in data.get('genres', [])]),
                "img": data.get('bannerImage') or data['coverImage']['extraLarge']
            }
        except: pass

    tmdb_path = "movie" if med_type == "movie" else "tv"
    try:
        url = f"https://api.themoviedb.org/3/search/{tmdb_path}?api_key={TMDB_KEY}&query={search_q}"
        if year: url += f"&year={year}"
        r = requests.get(url, timeout=10).json()
        if r['results']:
            res = r['results'][0]
            # Secondary call for genres
            det = requests.get(f"https://api.themoviedb.org/3/{tmdb_path}/{res['id']}?api_key={TMDB_KEY}").json()
            genres = " ".join([f"#{g['name'].replace(' ', '')}" for g in det.get('genres', [])])
            img = f"https://image.tmdb.org/t/p/w1280{res.get('backdrop_path')}" if res.get('backdrop_path') else f"https://image.tmdb.org/t/p/w780{res.get('poster_path')}"
            return {
                "title": res.get('title') or res.get('name'),
                "desc": res.get('overview', 'No synopsis available.'),
                "rating": f"{res.get('vote_average', 7.5)} IMDb",
                "genres": genres or "#Movie",
                "img": img,
                "date": res.get('release_date') or res.get('first_air_date') or "N/A"
            }
    except: pass
    return None

# --- AUTH SYSTEM ---
def is_admin(user_id):
    return user_id == OWNER_ID or admins_db.find_one({"uid": user_id})

# --- ALL COMMANDS RESTORED ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user_id = u.effective_user.id
    users_db.update_one({"uid": user_id}, {"$set": {"uid": user_id}}, upsert=True)
    if c.args:
        try:
            file_id = base64.urlsafe_b64decode((c.args[0] + '=' * (4 - len(c.args[0]) % 4)).encode()).decode()
            data = files_db.find_one({"_id": ObjectId(file_id)})
            if data: return await c.bot.copy_message(chat_id=u.effective_chat.id, from_chat_id=DB_CHANNEL_ID, message_id=data['msg_id'])
        except: pass
        return await u.message.reply_text("❌ Link Expired.")
    
    if is_admin(user_id):
        msg = f"🚀 <b>{BOT_USERNAME} Admin Panel</b>\n\n• /post - Auto Anime\n• /movie - Auto Movie\n• /webseries - Manual\n• /broadcast - Select\n• /broadcastall - Fast\n• /addadmin - Add ID\n• /addchannel - Add [ID type tag]\n• /status - Stats"
        await u.message.reply_text(msg, parse_mode='HTML')

async def status(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id): return
    users = users_db.count_documents({})
    files = files_db.count_documents({})
    size = db.command("dbStats")['dataSize'] / (1024*1024)
    await u.message.reply_text(f"📊 <b>Bot Stats</b>\n👥 Users: {users}\n📂 Files: {files}\n🗄 Storage: {size:.2f}MB / 512MB", parse_mode='HTML')

# --- POSTING LOGIC ---
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
    real_name = u.message.caption or getattr(file, "file_name", None) or "Unknown"
    title_tmp, ep_tmp, quality_tmp, year_tmp = advanced_title_cleaner(real_name)
    c.user_data['temp_files'].append({
        "msg_id": db_msg.message_id,
        "name": real_name,
        "quality": quality_tmp,
        "episode": ep_tmp
    })
    kb = [[InlineKeyboardButton("➕ Add More", callback_data="add_more")], [InlineKeyboardButton("✅ Done", callback_data="finish_auto")]]
    await u.message.reply_text(f"📥 Added {len(c.user_data['temp_files'])}.", reply_markup=InlineKeyboardMarkup(kb))

async def cb_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    if not is_admin(u.effective_user.id): return

    if q.data == "finish_auto":
        files, mode = c.user_data['temp_files'], c.user_data['mode']
        await q.edit_message_text("🔄 AI Searching Metadata...")
        title, ep, quality, year = advanced_title_cleaner(files[0]['name'])
        qualities = sorted(list(set(f.get("quality","HD") for f in files)))
        quality = " | ".join(qualities)
        meta = get_metadata(title, year, mode) or {"title": title, "desc": "N/A", "rating": "N/A", "genres": "#Store", "img": None}

        btns = []
        for f in files:
            f_ep = f.get("episode", "Full")
            f_q = f.get("quality", "HD")
            res = files_db.insert_one({"msg_id": f['msg_id'], "name": f['name']})
            link_id = base64.urlsafe_b64encode(str(res.inserted_id).encode()).decode().rstrip("=")
            btns.append(InlineKeyboardButton(f"🚀 {f_q} [{f_ep}]", url=f"https://t.me/{BOT_USERNAME}?start={link_id}"))

        network = "@Donghua_Xin" if mode == "anime" else "@Movies_Hindi_Plus"
        audio = "Chinese" if mode == "anime" else "Hindi | English"
        caption = (f"<b>{meta['title']}</b>\n<b>⟣────────────────────⟢</b>\n‣ Audio ⌯ {audio}\n‣ Rating ⌯ {meta['rating']}\n‣ Quality ⌯ {quality}\n‣ {'Episode' if mode == 'anime' else 'Released'} ⌯ {ep if mode == 'anime' else meta.get('date', 'N/A')}\n‣ Genres ⌯ {meta['genres']}\n<b>⟣────────────────────⟢</b>\n‣ Synopsis ⌯\n<blockquote expandable><b>{meta['desc']}</b></blockquote>\n\n🔗 <b>Our Network {network}</b>")
        
        if len(caption) > 950:
            caption = caption[:950] + "..."

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
        mode = c.user_data['post_data'].get('mode', 'anime')
        chs = list(channels_db.find({"type": mode}))
        kb = [[InlineKeyboardButton(f"{'✅ ' if ch['cid'] in sel else ''}{ch['tag']}", callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="send_final")])
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == "send_final":
        sel, p = c.user_data.get('selected', []), c.user_data.get('post_data')
        count = 0
        for cid in sel:
            try:
                await c.bot.send_photo(chat_id=cid, photo=p['img'], caption=p['cap'], reply_markup=InlineKeyboardMarkup(p['btns']), parse_mode='HTML')
                count += 1
            except: continue
        await q.message.reply_text(f"✅ Sent to {count} channels!")

# --- ALL OTHER CMDS RESTORED ---
async def webseries_manual(u, c):
    if not is_admin(u.effective_user.id): return
    try:
        raw = u.message.text.split(None, 1)[1]
        parts = [p.strip() for p in raw.split('|')]
        meta = get_metadata(parts[0], None, "series")
        cap = (f"<b>{meta['title']}</b>\n<b>⟣────────────────────⟢</b>\n‣ Audio ⌯ {parts[3]}\n‣ Quality ⌯ HD\n‣ Season ⌯ {parts[1]}\n<b>⟣────────────────────⟢</b>\n<blockquote expandable>{meta['desc']}</blockquote>")
        c.user_data['post_data'] = {"cap": cap, "img": meta['img'], "btns": [], "mode": "series"}
        chs = list(channels_db.find({"type": "series"}))
        kb = [[InlineKeyboardButton(ch['tag'], callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="send_final")])
        await u.message.reply_photo(photo=meta['img'], caption=cap, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except: await u.message.reply_text("Format: Name | Season | Img | Audio | Links")

async def broadcast_select(u, c):
    if not is_admin(u.effective_user.id) or not u.message.reply_to_message: return
    c.user_data['bc_msg'], c.user_data['bc_chat'], c.user_data['selected'] = u.message.reply_to_message.message_id, u.message.chat_id, []
    chs = list(channels_db.find({}))
    kb = [[InlineKeyboardButton(ch['tag'], callback_data=f"sel_{ch['cid']}")] for ch in chs]
    kb.append([InlineKeyboardButton("🚀 SEND NOW", callback_data="send_final")])
    c.user_data['post_data'] = {'mode': 'all'}
    await u.message.reply_text("Broadcast Select:", reply_markup=InlineKeyboardMarkup(kb))

async def broadcast_fast(u, c):
    if not is_admin(u.effective_user.id) or not u.message.reply_to_message: return
    chs = list(channels_db.find({}))
    for ch in chs:
        try: await c.bot.copy_message(chat_id=ch['cid'], from_chat_id=u.message.chat_id, message_id=u.message.reply_to_message.message_id)
        except: pass
    await u.message.reply_text("✅ Fast Broadcast Done.")

async def add_admin(u, c):
    if u.effective_user.id != OWNER_ID or not c.args: return
    admins_db.update_one({"uid": int(c.args[0])}, {"$set": {"uid": int(c.args[0])}}, upsert=True)
    await u.message.reply_text("✅ Admin Added.")

async def admins_list(u, c):
    if not is_admin(u.effective_user.id): return
    ads = list(admins_db.find({}))
    msg = f"<b>Admins:</b>\nOwner: {OWNER_ID}\n" + "\n".join([str(a['uid']) for a in ads])
    await u.message.reply_text(msg, parse_mode='HTML')

async def add_channel(u, c):
    if not is_admin(u.effective_user.id) or len(c.args) < 3: return
    channels_db.update_one({"cid": c.args[0]}, {"$set": {"type": c.args[1].lower(), "tag": " ".join(c.args[2:])}}, upsert=True)
    await u.message.reply_text("✅ Channel Added.")

async def list_channels(u, c):
    if not is_admin(u.effective_user.id): return
    chs = list(channels_db.find({}))
    msg = "<b>Channels:</b>\n" + "\n".join([f"{ch['cid']} ({ch['type']}) - {ch['tag']}" for ch in chs])
    await u.message.reply_text(msg, parse_mode='HTML')

async def error_handler(update, context):
    print("BOT ERROR:", context.error)

if __name__ == '__main__':
    threading.Thread(target=run_web, daemon=True).start()
    bot = Application.builder().token(TOKEN).build()
    bot.add_error_handler(error_handler)
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("status", status))
    bot.add_handler(CommandHandler("addadmin", add_admin))
    bot.add_handler(CommandHandler("admins", admins_list))
    bot.add_handler(CommandHandler("addchannel", add_channel))
    bot.add_handler(CommandHandler("channels", list_channels))
    bot.add_handler(CommandHandler("broadcast", broadcast_select))
    bot.add_handler(CommandHandler("broadcastall", broadcast_fast))
    bot.add_handler(CommandHandler("post", lambda u, c: auto_post_init(u, c, "anime")))
    bot.add_handler(CommandHandler("movie", lambda u, c: auto_post_init(u, c, "movie")))
    bot.add_handler(CommandHandler("webseries", webseries_manual))
    bot.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, file_handler))
    bot.add_handler(CallbackQueryHandler(cb_handler))
    bot.run_polling()
