import os
import time
import asyncio
import subprocess
from flask import Flask, request
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

app = Flask(__name__)
bot = Client("ad_merge_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_data = {}

@app.route('/')
def home():
    return "Bot is running!"

@bot.on_message(filters.command("start") & filters.private)
async def start(client, message: Message):
    user_data[message.chat.id] = {}
    await message.reply("👋 মুভি ভিডিও ফরওয়ার্ড করুন:")

@bot.on_message(filters.video & filters.private)
async def handle_video(client, message: Message):
    user_id = message.chat.id

    if "movie" not in user_data.get(user_id, {}):
        user_data[user_id]["movie"] = message
        await message.reply(
            "📺 আপনি কতটা বিজ্ঞাপন বসাতে চান?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("1টি", callback_data="ads_1")],
                [InlineKeyboardButton("2টি", callback_data="ads_2")],
                [InlineKeyboardButton("3টি", callback_data="ads_3")]
            ])
        )
    else:
        ads = user_data[user_id].get("ads", [])
        ads.append(message)
        user_data[user_id]["ads"] = ads

        required_ads = user_data[user_id].get("ad_count", 1)
        if len(ads) == required_ads:
            await message.reply("🔧 ভিডিও প্রসেস হচ্ছে...")
            await process_and_send(client, user_id)

@bot.on_callback_query()
async def handle_callback(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    if data.startswith("ads_"):
        count = int(data.split("_")[1])
        user_data[user_id]["ad_count"] = count
        user_data[user_id]["ads"] = []
        await callback_query.message.reply(f"📥 {count}টি বিজ্ঞাপন ভিডিও ফরওয়ার্ড করুন:")

async def process_and_send(client, user_id):
    movie_msg = user_data[user_id]["movie"]
    ads_msg = user_data[user_id]["ads"]

    movie_path = await movie_msg.download(file_name=f"movie_{user_id}.mp4")
    ad_paths = []
    for i, ad in enumerate(ads_msg):
        path = await ad.download(file_name=f"ad_{user_id}_{i}.mp4")
        ad_paths.append(path)

    segments = []
    duration = get_video_duration(movie_path)
    slice_duration = duration // (len(ad_paths) + 1)

    for i in range(len(ad_paths)):
        start = i * slice_duration
        end = start + slice_duration
        segment_file = f"segment_{i}_{user_id}.mp4"
        subprocess.call([
            "ffmpeg", "-i", movie_path, "-ss", str(start), "-t", str(slice_duration),
            "-c", "copy", segment_file, "-y"
        ])
        segments.append(segment_file)
        segments.append(ad_paths[i])

    last_segment = f"segment_last_{user_id}.mp4"
    subprocess.call([
        "ffmpeg", "-i", movie_path, "-ss", str(slice_duration * len(ad_paths)),
        "-c", "copy", last_segment, "-y"
    ])
    segments.append(last_segment)

    concat_list = f"concat_{user_id}.txt"
    with open(concat_list, "w") as f:
        for file in segments:
            f.write(f"file '{file}'\n")

    final_file = f"final_{user_id}.mp4"
    subprocess.call(["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_list, "-c", "copy", final_file, "-y"])

    await client.send_video(user_id, final_file, caption="✅ বিজ্ঞাপন সহ মুভি প্রস্তুত!")

    # Cleanup
    for f in [movie_path, final_file, concat_list] + segments + ad_paths:
        try: os.remove(f)
        except: pass

def get_video_duration(path):
    result = subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries',
        'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', path
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return int(float(result.stdout))

if __name__ == "__main__":
    bot.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
