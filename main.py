import os, re, threading, requests
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from pymongo import MongoClient

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client['AnimePostBot']

# --- KOYEB HEALTH CHECK ---
app = Flask(__name__)
@app.route('/')
def health(): return "Bot is Running", 200
def run_web(): app.run(host='0.0.0.0', port=8000)

# --- ANILIST FETCH ---
def get_info(query):
    gql = '''query($s:String){Media(search:$s,type:ANIME){
        title{english romaji}description averageScore genres bannerImage
        coverImage{extraLarge}}}'''
    try:
        r = requests.post('https://graphql.anilist.co', json={'query':gql, 'variables':{'s':query}})
        return r.json()['data']['Media']
    except: return None

# --- COMMANDS ---
async def start(u, c):
    await u.message.reply_text("<b>Welcome!</b>\n/addchannel [ID] [Tag]\n/channels - Manage yours\n/post Name | Ep | Img | Links\n/broadcastall - Reply to post", parse_mode='HTML')

async def add_channel(u, c):
    if len(c.args) < 2: return await u.message.reply_text("<b>Usage:</b> /addchannel -10012345 AnimXin", parse_mode='HTML')
    db.channels.update_one({"uid": u.effective_user.id, "cid": c.args[0]}, {"$set": {"tag": " ".join(c.args[1:])}}, upsert=True)
    await u.message.reply_text("<b>✅ Channel Added!</b>", parse_mode='HTML')

async def list_channels(u, c):
    chs = list(db.channels.find({"uid": u.effective_user.id}))
    if not chs: return await u.message.reply_text("No channels added.")
    kb = [[InlineKeyboardButton(f"❌ Remove {ch['tag']}", callback_data=f"rm_{ch['cid']}")] for ch in chs]
    await u.message.reply_text("<b>Your Channels:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def post_cmd(u, c):
    try:
        # Expected: Name | Ep | ImgLink | Links
        parts = [p.strip() for p in u.message.text.replace('/post','').split('|')]
        name = parts[0]
        ep = parts[1] if len(parts) > 1 else "01"
        user_img = parts[2] if len(parts) > 2 else None
        links_str = parts[3] if len(parts) > 3 else ""

        # Get Info from DB or AniList
        info = db.cache.find_one({"n": name.lower()})
        if not info:
            info = get_info(name)
            if info: db.cache.update_one({"n": name.lower()}, {"$set": info}, upsert=True)
        
        if not info:
            return await u.message.reply_text("<b>❌ Anime not found on AniList. Check spelling.</b>", parse_mode='HTML')

        # Formatting Synopsis (Half Hidden)
        raw_desc = re.sub('<[^<]+?>', '', info.get('description', 'No Synopsis Found')).replace('&quot;', '"')
        mid = len(raw_desc)//2
        synopsis = f"{raw_desc[:mid]}<tg-spoiler>{raw_desc[mid:]}</tg-spoiler>"
        
        caption = (
            f"<b>{info['title'].get('english') or info['title'].get('romaji')} Season 3 (Cang Yuan Tu)</b>\n"
            f"<b>⟣────────────────────⟢</b>\n"
            f"<b>‣ Audio ⌯ [Chinese | Eng-Sub]</b>\n"
            f"<b>‣ Rating ⌯ {info.get('averageScore', 0)/10} IMDB | 96% User Score</b>\n"
            f"<b>‣ Quality ⌯ 480p | 720p | 1080p</b>\n"
            f"<b>‣ Episode ⌯ {ep}</b>\n"
            f"<b>‣ Genres ⌯ {', '.join(['#'+g for g in info['genres']])}</b>\n"
            f"<b>⟣────────────────────⟢</b>\n"
            f"<b>‣ Synopsis ⌯</b>\n"
            f"<blockquote><b>{synopsis}</b></blockquote>\n"
            f"<b>🔗 Our Network @Donghua_Xin</b>"
        )

        final_img = user_img if (user_img and "http" in user_img) else (info.get('bannerImage') or info['coverImage']['extraLarge'])
        
        # Parse Links into Buttons
        btns = []
        for q, l in re.findall(r'(\d+p)\s*:\s*(https?://\S+)', links_str):
            btns.append([InlineKeyboardButton(f"🚀 {q} Download", url=l)])
        
        c.user_data['temp_post'] = {"cap": caption, "img": final_img, "btn": btns}
        c.user_data['sel'] = []

        # Channel Selector
        chs = list(db.channels.find({"uid": u.effective_user.id}))
        kb = [[InlineKeyboardButton(f"Select: {ch['tag']}", callback_data=f"sel_{ch['cid']}")] for ch in chs]
        kb.append([InlineKeyboardButton("✅ DONE / SEND", callback_data="confirm_send")])
        
        await u.message.reply_photo(photo=final_img, caption=caption + "\n\n<b>Select Channels to Post:</b>", 
                                  reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except Exception as e:
        await u.message.reply_text(f"<b>Error:</b> Use format <code>Name | Ep | ImgLink | 480p: link</code>", parse_mode='HTML')

async def cb_handler(u, c):
    q = u.callback_query
    uid = u.effective_user.id

    if q.data.startswith("rm_"):
        cid = q.data.replace("rm_", "")
        db.channels.delete_one({"uid": uid, "cid": cid})
        await q.answer("Channel Removed")
        return await q.edit_message_text("Channel Deleted.")

    if q.data.startswith("sel_"):
        cid = q.data.replace("sel_", "")
        if cid not in c.user_data['sel']: c.user_data['sel'].append(cid)
        await q.answer(f"Selected")

    if q.data == "confirm_send":
        p = c.user_data.get('temp_post')
        targets = c.user_data.get('sel', [])
        if not targets: return await q.answer("Select at least one channel!", show_alert=True)
        
        for cid in targets:
            try:
                await c.bot.send_photo(chat_id=cid, photo=p['img'], caption=p['cap'], 
                                     reply_markup=InlineKeyboardMarkup(p['btn']), parse_mode='HTML')
            except: pass
        await q.edit_message_caption("<b>🚀 Post sent to selected channels!</b>", parse_mode='HTML')

async def broadcast_all(u, c):
    if not u.message.reply_to_message:
        return await u.message.reply_text("Reply to the message you want to broadcast.")
    
    chs = list(db.channels.find({"uid": u.effective_user.id}))
    count = 0
    for ch in chs:
        try:
            await c.bot.copy_message(chat_id=ch['cid'], from_chat_id=u.message.chat_id, 
                                   message_id=u.message.reply_to_message.message_id)
            count += 1
        except: pass
    await u.message.reply_text(f"<b>Broadcasted to {count} channels.</b>", parse_mode='HTML')

if __name__ == '__main__':
    threading.Thread(target=run_web, daemon=True).start()
    bot = Application.builder().token(TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("addchannel", add_channel))
    bot.add_handler(CommandHandler("channels", list_channels))
    bot.add_handler(CommandHandler("post", post_cmd))
    bot.add_handler(CommandHandler("broadcastall", broadcast_all))
    bot.add_handler(CallbackQueryHandler(cb_handler))
    bot.run_polling()
