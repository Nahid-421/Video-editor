import os
import asyncio
import subprocess
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

bot = Client("ad_merge_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_sessions = {}

@bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_sessions[message.from_user.id] = {}
    await message.reply("👋 মুভি ভিডিও ফরওয়ার্ড করুন:")

@bot.on_message(filters.video & filters.private)
async def handle_video(client, message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id, {})

    if "movie" not in session:
        # মুভি ভিডিও প্রথমবার পেয়েছে
        session["movie"] = message
        user_sessions[user_id] = session
        await message.reply(
            "📢 কতটি বিজ্ঞাপন ভিডিও বসাবেন?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("1", callback_data="ads_1")],
                [InlineKeyboardButton("2", callback_data="ads_2")],
                [InlineKeyboardButton("3", callback_data="ads_3")],
            ])
        )
    else:
        # বিজ্ঞাপন ভিডিও যোগ হচ্ছে
        ads = session.get("ads", [])
        ads.append(message)
        session["ads"] = ads
        user_sessions[user_id] = session

        if len(ads) == session.get("ad_count", 1):
            await message.reply("🔧 ভিডিও প্রসেস শুরু করছি, একটু অপেক্ষা করুন...")
            await process_videos(client, user_id)
        else:
            await message.reply(f"অনুগ্রহ করে আরও {session['ad_count'] - len(ads)}টি বিজ্ঞাপন ভিডিও পাঠান।")

@bot.on_callback_query()
async def callback_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    session = user_sessions.get(user_id, {})

    if data.startswith("ads_"):
        count = int(data.split("_")[1])
        session["ad_count"] = count
        session["ads"] = []
        user_sessions[user_id] = session
        await callback_query.message.edit_text(f"✅ {count}টি বিজ্ঞাপন ভিডিও পাঠান এখন:")

async def process_videos(client, user_id):
    session = user_sessions.get(user_id)
    movie_msg = session.get("movie")
    ads_msg = session.get("ads", [])

    # ডাউনলোড ভিডিও
    movie_path = await movie_msg.download(file_name=f"movie_{user_id}.mp4")
    ad_paths = []
    for idx, ad in enumerate(ads_msg):
        path = await ad.download(file_name=f"ad_{user_id}_{idx}.mp4")
        ad_paths.append(path)

    # ভিডিও মিক্সিং শুরু
    try:
        duration = get_duration(movie_path)
        segment_duration = duration // (len(ad_paths) + 1)

        segments = []

        # movie segments + ads interleaved
        for i in range(len(ad_paths)):
            seg_file = f"segment_{user_id}_{i}.mp4"
            cut_video(movie_path, segment_duration * i, segment_duration, seg_file)
            segments.append(seg_file)
            segments.append(ad_paths[i])

        # শেষ সেগমেন্ট
        last_seg = f"segment_{user_id}_last.mp4"
        cut_video(movie_path, segment_duration * len(ad_paths), duration - (segment_duration * len(ad_paths)), last_seg)
        segments.append(last_seg)

        # concat ফাইল তৈরি
        concat_file = f"concat_{user_id}.txt"
        with open(concat_file, "w") as f:
            for seg in segments:
                f.write(f"file '{seg}'\n")

        # ফাইনাল মিক্সড ভিডিও
        final_video = f"final_{user_id}.mp4"
        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c", "copy", final_video, "-y"
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # ভিডিও পাঠান ইউজারকে
        await client.send_video(user_id, final_video, caption="✅ বিজ্ঞাপনসহ মুভি প্রস্তুত হয়েছে!")

    except Exception as e:
        await client.send_message(user_id, f"❌ কিছু সমস্যা হয়েছে: {e}")

    finally:
        # ক্লিনআপ
        cleanup_files = [movie_path, concat_file, final_video] + ad_paths + segments
        for f in cleanup_files:
            try:
                os.remove(f)
            except:
                pass

        user_sessions.pop(user_id, None)

def get_duration(path):
    import json
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
         "json", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    output = json.loads(result.stdout)
    return int(float(output['format']['duration']))

def cut_video(input_file, start, duration, output_file):
    cmd = [
        "ffmpeg", "-ss", str(start), "-i", input_file, "-t", str(duration),
        "-c", "copy", output_file, "-y"
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

if __name__ == "__main__":
    print("Bot is starting...")
    bot.run()
