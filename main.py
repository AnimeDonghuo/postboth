import os, re, threading, requests
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pymongo import MongoClient

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OWNER_ID = int(os.getenv("OWNER_ID", "0")) # Set this in Koyeb Env
client = MongoClient(MONGO_URI)
db = client['AnimePostBot']

# --- KOYEB HEALTH CHECK ---
app = Flask(__name__)
@app.route('/')
def health(): return "Bot is Online", 200
def run_web(): app.run(host='0.0.0.0', port=8000)

# --- ANILIST FETCH ---
def get_info(query):
    gql = '''query($s:String){Media(search:$s,type:ANIME){
        title{english romaji native} description averageScore genres bannerImage
        coverImage{extraLarge}}}'''
    try:
        r = requests.post('https://graphql.anilist.co', json={'query':gql, 'variables':{'s':query}})
        data = r.json().get('data', {}).get('Media')
        if not data: return None
        desc = re.sub(r'<[^>]+>', '', data.get('description', 'No synopsis available.'))
        data['description'] = re.sub(r'Note:.*', '', desc, flags=re.IGNORECASE).strip()
        return data
    except: return None

# --- UTILS ---
def chunk_buttons(buttons, n):
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]

# --- COMMANDS ---
async def start(u, c):
    await u.message.reply_text("<b>🚀 Donghua Post Bot</b>\n\n/addchannel [ID] [Tag]\n/channels - Manage\n/post [Name] | [Ep] | [Img/Links] | [Links]", parse_mode='HTML')

async def add_channel(u, c):
    if len(c.args) < 2: return await u.message.reply_text("Usage: /addchannel -100xxx Name", parse_mode='HTML')
    db.channels.update_one({"uid": u.effective_user.id, "cid": c.args[0]}, {"$set": {"tag": " ".join(c.args[1:])}}, upsert=True)
    await u.message.reply_text("<b>✅ Channel Added!</b>", parse_mode='HTML')

async def list_channels(u, c):
    chs = list(db.channels.find({"uid": u.effective_user.id}))
    if not chs: return await u.message.reply_text("No channels.")
    kb = [[InlineKeyboardButton(f"🗑 Remove {ch['tag']}", callback_data=f"rm_{ch['cid']}")] for ch in chs]
    await u.message.reply_text("<b>Your Channels:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def post_cmd(u, c):
    try:
        # Input: Name | Ep | (Optional Img) | Links
        parts = [p.strip() for p in u.message.text.replace('/post','').split('|')]
        name_input = parts[0]
        ep = parts[1] if len(parts) > 1 else "01"
        
        # Image logic
        user_img = None
        links_text = ""
        
        if len(parts) == 3:
            # Check if 3rd part is an Image URL or just Links
            if parts[2].startswith("http") and not any(q in parts[2] for q in ["p :", "p:"]):
                user_img = parts[2]
            else:
                links_text = parts[2]
        elif len(parts) >= 4:
            user_img = parts[2]
            links_text = parts[3]

        # Fetch Info & Handle Image Database
        info = db.cache.find_one({"n": name_input.lower()}) or get_info(name_input)
        if not info: return await u.message.reply_text("Anime not found.")
        db.cache.update_one({"n": name_input.lower()}, {"$set": info}, upsert=True)

        # Image Logic: Save if provided, Get if missing
        if user_img:
            db.media.update_one({"n": name_input.lower()}, {"$set": {"img": user_img}}, upsert=True)
            final_img = user_img
        else:
            saved_media = db.media.find_one({"n": name_input.lower()})
            final_img = saved_media['img'] if saved_media else (info.get('bannerImage') or info['coverImage']['extraLarge'])

        # Formatting
        alt_name = info['title'].get('romaji') or info['title'].get('native')
        display_name = f"{name_input} ({alt_name})" if alt_name and name_input.lower() != alt_name.lower() else name_input
        qualities = re.findall(r'(\d+p|4K|360p)', links_text, re.I)
        q_label = " | ".join(sorted(list(set(qualities)))) if qualities else "480p | 720p | 1080p"
        
        desc = info['description']
        mid = len(desc)//2
        synopsis = f"{desc[:mid]}<tg-spoiler>{desc[mid:]}</tg-spoiler>"

        caption = (
            f"<b>{display_name}</b>\n"
            f"<b>⟣────────────────────⟢</b>\n"
            f"<b>‣ Audio ⌯ [Chinese | Eng-Sub]</b>\n"
            f"<b>‣ Rating ⌯ {info.get('averageScore', 0)/10} IMDB | 96% User Score</b>\n"
            f"<b>‣ Quality ⌯ {q_label}</b>\n"
            f"<b>‣ Episode ⌯ {ep}</b>\n"
            f"<b>‣ Genres ⌯ {', '.join(['#'+g for g in info['genres']])}</b>\n"
            f"<b>⟣────────────────────⟢</b>\n"
            f"<b>‣ Synopsis ⌯</b>\n"
            f"<blockquote><b>{synopsis}</b></blockquote>\n"
            f"<b>🔗 Our Network @Donghua_Xin</b>"
        )

        # Buttons (2 per row)
        btns = []
        links_found = re.findall(r'(\d+p|4K|360p)\s*:\s*(https?://\S+)', links_text, re.I)
        for label, url in links_found:
            btns.append(InlineKeyboardButton(f"🚀 {label.upper()} Download", url=url))
        
        c.user_data['temp'] = {"cap": caption, "img": final_img, "kb": chunk_buttons(btns, 2)}
        
        chs = list(db.channels.find({"uid": u.effective_user.id}))
        sel_kb = [[InlineKeyboardButton(f"Post to: {ch['tag']}", callback_data=f"sel_{ch['cid']}")] for ch in chs]
        sel_kb.append([InlineKeyboardButton("✅ SEND TO ALL", callback_data="send_all")])

        await u.message.reply_photo(photo=final_img, caption=caption, reply_markup=InlineKeyboardMarkup(sel_kb), parse_mode='HTML')
    except Exception as e:
        await u.message.reply_text(f"<b>Error:</b> Use Name | Ep | [Img] | Links\n{e}", parse_mode='HTML')

async def callback(u, c):
    q = u.callback_query
    temp = c.user_data.get('temp')
    if not temp: return
    
    if q.data.startswith("rm_"):
        db.channels.delete_one({"uid": u.effective_user.id, "cid": q.data.replace("rm_","")})
        return await q.edit_message_text("Channel removed.")

    targets = [ch['cid'] for ch in db.channels.find({"uid": u.effective_user.id})] if q.data == "send_all" else [q.data.replace("sel_","")]
    for cid in targets:
        try: await c.bot.send_photo(chat_id=cid, photo=temp['img'], caption=temp['cap'], reply_markup=InlineKeyboardMarkup(temp['kb']), parse_mode='HTML')
        except: pass
    await q.edit_message_caption("<b>✅ Sent Successfully!</b>", parse_mode='HTML')

async def broadcast(u, c):
    # Only Owner Check
    if u.effective_user.id != OWNER_ID:
        return await u.message.reply_text("<b>❌ Access Denied. Only the bot owner can broadcast.</b>", parse_mode='HTML')
    
    if not u.message.reply_to_message:
        return await u.message.reply_text("Reply to a message to broadcast it.")
    
    # Broadcast logic for owner's added channels
    chs = list(db.channels.find({"uid": u.effective_user.id}))
    for ch in chs:
        try: await c.bot.copy_message(chat_id=ch['cid'], from_chat_id=u.message.chat_id, message_id=u.message.reply_to_message.message_id)
        except: pass
    await u.message.reply_text(f"<b>Broadcasted to {len(chs)} channels.</b>", parse_mode='HTML')

if __name__ == '__main__':
    threading.Thread(target=run_web, daemon=True).start()
    bot = Application.builder().token(TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("addchannel", add_channel))
    bot.add_handler(CommandHandler("channels", list_channels))
    bot.add_handler(CommandHandler("post", post_cmd))
    bot.add_handler(CommandHandler("broadcastall", broadcast))
    bot.add_handler(CallbackQueryHandler(callback))
    bot.run_polling()
