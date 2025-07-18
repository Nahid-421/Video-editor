import os
import asyncio
import subprocess
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# এনভায়রনমেন্ট ভেরিয়েবল থেকে API_ID, API_HASH, BOT_TOKEN লোড করা হচ্ছে
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Pyrogram ক্লায়েন্ট ইনিশিয়ালাইজ করা হচ্ছে
bot = Client("ad_merge_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ইউজার সেশন ডেটা সংরক্ষণের জন্য ডিকশনারি
user_sessions = {}

# স্টার্ট কমান্ড হ্যান্ডলার
@bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_sessions[message.from_user.id] = {} # নতুন সেশন শুরু
    await message.reply("👋 মুভি ভিডিও ফরওয়ার্ড করুন:")

# ভিডিও হ্যান্ডলার
@bot.on_message(filters.video & filters.private)
async def handle_video(client, message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id, {})

    if "movie" not in session:
        # যদি প্রথম ভিডিও হয়, সেটাকে মুভি হিসেবে সেভ করা হবে
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
        # পরের ভিডিওগুলো বিজ্ঞাপন হিসেবে যোগ করা হবে
        ads = session.get("ads", [])
        ads.append(message)
        session["ads"] = ads
        user_sessions[user_id] = session

        # যদি প্রয়োজনীয় সংখ্যক বিজ্ঞাপন ভিডিও পেয়ে যায়, প্রসেসিং শুরু হবে
        if len(ads) == session.get("ad_count", 1):
            await message.reply("🔧 ভিডিও প্রসেস শুরু করছি, একটু অপেক্ষা করুন...")
            await process_videos(client, user_id)
        else:
            # আরও বিজ্ঞাপন ভিডিওর জন্য অনুরোধ
            await message.reply(f"অনুগ্রহ করে আরও {session['ad_count'] - len(ads)}টি বিজ্ঞাপন ভিডিও পাঠান।")

# কলব্যাক কোয়েরি হ্যান্ডলার
@bot.on_callback_query()
async def callback_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    session = user_sessions.get(user_id, {})

    if data.startswith("ads_"):
        count = int(data.split("_")[1]) # বিজ্ঞাপনের সংখ্যা নির্ধারণ
        session["ad_count"] = count
        session["ads"] = [] # বিজ্ঞাপন ভিডিও তালিকা রিসেট
        user_sessions[user_id] = session
        await callback_query.message.edit_text(f"✅ {count}টি বিজ্ঞাপন ভিডিও পাঠান এখন:")

# ভিডিও প্রসেসিং ফাংশন
async def process_videos(client, user_id):
    session = user_sessions.get(user_id)
    movie_msg = session.get("movie")
    ads_msg = session.get("ads", [])

    # ভিডিও ডাউনলোড
    movie_path = await movie_msg.download(file_name=f"movie_{user_id}.mp4")
    ad_paths = []
    for idx, ad in enumerate(ads_msg):
        path = await ad.download(file_name=f"ad_{user_id}_{idx}.mp4")
        ad_paths.append(path)

    # ভিডিও মিক্সিং লজিক
    try:
        duration = get_duration(movie_path)
        segment_duration = duration // (len(ad_paths) + 1)

        segments = []

        # মুভি সেগমেন্ট এবং বিজ্ঞাপন ভিডিও ইন্টারলিভ করা
        for i in range(len(ad_paths)):
            seg_file = f"segment_{user_id}_{i}.mp4"
            cut_video(movie_path, segment_duration * i, segment_duration, seg_file)
            segments.append(seg_file)
            segments.append(ad_paths[i])

        # মুভি শেষ সেগমেন্ট
        last_seg = f"segment_{user_id}_last.mp4"
        cut_video(movie_path, segment_duration * len(ad_paths), duration - (segment_duration * len(ad_paths)), last_seg)
        segments.append(last_seg)

        # concat ফাইল তৈরি করা
        concat_file = f"concat_{user_id}.txt"
        with open(concat_file, "w") as f:
            for seg in segments:
                f.write(f"file '{seg}'\n")

        # ফাইনাল মিক্সড ভিডিও তৈরি
        final_video = f"final_{user_id}.mp4"
        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c", "copy", final_video, "-y"
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # ইউজারকে ভিডিও পাঠানো
        await client.send_video(user_id, final_video, caption="✅ বিজ্ঞাপনসহ মুভি প্রস্তুত হয়েছে!")

    except Exception as e:
        # ত্রুটি হলে ইউজারকে জানানো
        await client.send_message(user_id, f"❌ কিছু সমস্যা হয়েছে: {e}")

    finally:
        # ফাইল ক্লিনআপ
        cleanup_files = [movie_path, concat_file, final_video] + ad_paths + segments
        for f in cleanup_files:
            try:
                os.remove(f)
            except:
                pass

        user_sessions.pop(user_id, None) # সেশন ডেটা মুছে ফেলা

# ভিডিওর ডিউরেশন (সময়কাল) পাওয়ার ফাংশন
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

# ভিডিও কাট করার ফাংশন
def cut_video(input_file, start, duration, output_file):
    cmd = [
        "ffmpeg", "-ss", str(start), "-i", input_file, "-t", str(duration),
        "-c", "copy", output_file, "-y"
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# বট শুরু করা হচ্ছে
if __name__ == "__main__":
    print("Bot is starting...")
    bot.run()
