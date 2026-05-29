import os, re, threading, requests
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pymongo import MongoClient

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client['AnimePostBot']

# --- HEALTH CHECK SERVER (For Koyeb) ---
app = Flask(__name__)
@app.route('/')
def health(): return "OK", 200
def run_web(): app.run(host='0.0.0.0', port=8000)

# --- ANILIST FETCH (No Key Required) ---
def get_info(query):
    gql = 'query($s:String){Media(search:$s,type:ANIME){title{romaji english}descriptionaverageScoregenresbannerImagecoverImage{extraLarge}}}'
    try:
        r = requests.post('https://graphql.anilist.co', json={'query':gql, 'variables':{'s':query}})
        return r.json()['data']['Media']
    except: return None

# --- BOT LOGIC ---
async def start(u, c):
    await u.message.reply_text("<b>Bot Active.</b>\n/addchannel [ID] [Tag]\n/post [Name] | [Ep] | [Links]", parse_mode='HTML')

async def add_channel(u, c):
    if len(c.args) < 2: return await u.message.reply_text("Usage: /addchannel -100xxx Name")
    db.channels.update_one({"uid": u.effective_user.id, "cid": c.args[0]}, {"$set": {"tag": " ".join(c.args[1:])}}, upsert=True)
    await u.message.reply_text("<b>Channel Added!</b>", parse_mode='HTML')

async def post_cmd(u, c):
    try:
        # Input format: Name | Ep | Quality: Link Quality: Link
        data = u.message.text.replace('/post','').strip().split('|')
        name, ep, links_str = data[0].strip(), data[1].strip(), data[2].strip()
        
        info = db.cache.find_one({"n": name.lower()}) or get_info(name)
        if not info: return await u.message.reply_text("Anime not found.")
        db.cache.update_one({"n": name.lower()}, {"$set": info}, upsert=True)

        desc = re.sub('<[^<]+?>', '', info.get('description', 'No Synopsis'))
        mid = len(desc)//2
        # FORMATTING: Bold everything, Hidden synopsis half-way
        caption = (
            f"<b>{info['title'].get('english') or info['title'].get('romaji')}</b>\n"
            f"<b>⟣────────────────────⟢</b>\n"
            f"<b>‣ Audio ⌯ [Chinese | Eng-Sub]</b>\n"
            f"<b>‣ Rating ⌯ {info.get('averageScore', 0)/10} IMDB</b>\n"
            f"<b>‣ Quality ⌯ 480p | 720p | 1080p</b>\n"
            f"<b>‣ Episode ⌯ {ep}</b>\n"
            f"<b>‣ Genres ⌯ {', '.join(['#'+g for g in info['genres']])}</b>\n"
            f"<b>⟣────────────────────⟢</b>\n"
            f"<b>‣ Synopsis ⌯</b>\n"
            f"<b>{desc[:mid]}</b>||<b>{desc[mid:]}</b>||\n"
            f"<b>🔗 Our Network @Donghua_Xin</b>"
        )
        img = info.get('bannerImage') or info['coverImage']['extraLarge']
        
        btns = []
        for q, l in re.findall(r'(\d+p)\s*:\s*(https?://\S+)', links_str):
            btns.append([InlineKeyboardButton(f"🚀 {q} Download", url=l)])
        
        c.user_data['p'] = {"t": caption, "i": img, "b": btns}
        
        # Channel Selection
        ch_list = list(db.channels.find({"uid": u.effective_user.id}))
        kb = [[InlineKeyboardButton(f"Post to: {ch['tag']}", callback_data=f"s_{ch['cid']}")] for ch in ch_list]
        kb.append([InlineKeyboardButton("📢 Send All", callback_data="s_all")])
        
        await u.message.reply_photo(photo=img, caption=caption + "\n\n<b>Select Channel:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except Exception as e: await u.message.reply_text(f"Error: {e}")

async def cb_handler(u, c):
    q = u.callback_query
    p = c.user_data.get('p')
    if not p: return
    
    t_list = [ch['cid'] for ch in db.channels.find({"uid": u.effective_user.id})] if q.data == "s_all" else [q.data.replace('s_','')]
    for target in t_list:
        await c.bot.send_photo(chat_id=target, photo=p['i'], caption=p['t'], reply_markup=InlineKeyboardMarkup(p['b']), parse_mode='HTML')
    await q.edit_message_caption("<b>✅ Sent Successfully!</b>", parse_mode='HTML')

async def broadcast(u, c):
    if not u.message.reply_to_message: return
    for ch in db.channels.find({"uid": u.effective_user.id}):
        await c.bot.copy_message(chat_id=ch['cid'], from_chat_id=u.message.chat_id, message_id=u.message.reply_to_message.message_id)
    await u.message.reply_text("Broadcast Done.")

if __name__ == '__main__':
    threading.Thread(target=run_web, daemon=True).start()
    bot = Application.builder().token(TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("addchannel", add_channel))
    bot.add_handler(CommandHandler("post", post_cmd))
    bot.add_handler(CommandHandler("broadcastall", broadcast))
    bot.add_handler(CallbackQueryHandler(cb_handler))
    bot.run_polling()
