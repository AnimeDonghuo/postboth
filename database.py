from pymongo import MongoClient
import os

client = MongoClient(os.getenv("MONGO_URI"))
db = client['AnimeBotDB']

def save_channel(user_id, channel_id, channel_name):
    db.channels.update_one(
        {"user_id": user_id, "channel_id": channel_id},
        {"$set": {"name": channel_name}},
        upsert=True
    )

def get_channels(user_id):
    return list(db.channels.find({"user_id": user_id}))

def save_anime_data(name, data):
    db.anime.update_one({"name": name.lower()}, {"$set": data}, upsert=True)

def get_anime_data(name):
    return db.anime.find_one({"name": name.lower()})
