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

    movie_path = None
    ad_paths = []
    temp_segment_files = [] # ক্লিনআপের জন্য ফাইল লিস্ট

    try:
        await client.send_message(user_id, "🎬 মুভি ভিডিও ডাউনলোড হচ্ছে...")
        movie_path = await movie_msg.download(file_name=f"movie_{user_id}.mp4")
        await client.send_message(user_id, "📢 বিজ্ঞাপন ভিডিও ডাউনলোড হচ্ছে...")
        for idx, ad in enumerate(ads_msg):
            path = await ad.download(file_name=f"ad_{user_id}_{idx}.mp4")
            ad_paths.append(path)
        await client.send_message(user_id, "🔧 ভিডিও প্রসেসিং শুরু করছি...")

        duration = get_duration(movie_path)
        if duration is None or duration == 0:
            raise ValueError("মুভি ভিডিওর সময়কাল নির্ণয় করা যায়নি বা এটি অবৈধ।")

        segment_duration = duration // (len(ad_paths) + 1)
        if segment_duration <= 0:
            # এটি ঘটে যখন মুভি বিজ্ঞাপনের থেকে ছোট হয়
            raise ValueError("ভিডিও সেগমেন্টের সময়কাল খুব কম বা শুন্য। অনুগ্রহ করে দীর্ঘতর মুভি ভিডিও পাঠান।")


        segments = []

        # movie segments + ads interleaved
        for i in range(len(ad_paths)):
            seg_file = f"segment_{user_id}_{i}.mp4"
            await client.send_message(user_id, f"✂️ মুভি সেগমেন্ট {i+1} কাটা হচ্ছে...")
            cut_video(movie_path, segment_duration * i, segment_duration, seg_file)
            segments.append(seg_file)
            temp_segment_files.append(seg_file)
            segments.append(ad_paths[i])

        # শেষ সেগমেন্ট
        last_seg = f"segment_{user_id}_last.mp4"
        await client.send_message(user_id, "✂️ মুভি শেষ সেগমেন্ট কাটা হচ্ছে...")
        cut_video(movie_path, segment_duration * len(ad_paths), duration - (segment_duration * len(ad_paths)), last_seg)
        segments.append(last_seg)
        temp_segment_files.append(last_seg)

        # concat ফাইল তৈরি
        concat_file = f"concat_{user_id}.txt"
        with open(concat_file, "w") as f:
            for seg in segments:
                f.write(f"file '{seg}'\n")
        await client.send_message(user_id, "🔗 ভিডিওগুলো যুক্ত করা হচ্ছে...")

        # ফাইনাল মিক্সড ভিডিও - অডিও-ভিডিও স্ট্রিম সরাসরি কপি করা হবে
        final_video = f"final_{user_id}.mp4"
        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c:v", "copy",  # ভিডিও স্ট্রিম কপি
            "-c:a", "copy",  # অডিও স্ট্রিম কপি
            "-y", final_video
        ]
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if process.returncode != 0:
            print(f"FFmpeg Concat Error (User ID: {user_id}):\nSTDOUT: {process.stdout}\nSTDERR: {process.stderr}")
            raise RuntimeError(f"ভিডিও যুক্ত করতে ব্যর্থ। ত্রুটি: {process.stderr[:500]}...")
        else:
            print(f"FFmpeg Concat Success (User ID: {user_id}):\nSTDOUT: {process.stdout}")


        # ভিডিও পাঠান ইউজারকে
        await client.send_video(user_id, final_video, caption="✅ বিজ্ঞাপনসহ মুভি প্রস্তুত হয়েছে!")

    except Exception as e:
        # বিস্তারিত ত্রুটি বার্তা ইউজারকে পাঠানো হচ্ছে
        print(f"Error processing video for user {user_id}: {e}") # সার্ভার লগ
        await client.send_message(user_id, f"❌ ভিডিও প্রসেস করতে সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন বা অন্য ভিডিও পাঠান। \n\nত্রুটি: `{e}`")

    finally:
        # ক্লিনআপ
        cleanup_files = [movie_path, concat_file, final_video] + ad_paths + temp_segment_files
        for f in cleanup_files:
            if f and os.path.exists(f): # ফাইলটি আছে কিনা পরীক্ষা করুন
                try:
                    os.remove(f)
                    print(f"Cleaned up: {f}")
                except Exception as e:
                    print(f"Error cleaning up file {f}: {e}")

        user_sessions.pop(user_id, None)

def get_duration(path):
    import json
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
             "json", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if result.returncode != 0:
            print(f"FFprobe Error (Path: {path}):\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
            return None

        output = json.loads(result.stdout)
        return int(float(output['format']['duration']))
    except Exception as e:
        print(f"Exception in get_duration for {path}: {e}")
        return None


def cut_video(input_file, start, duration, output_file):
    cmd = [
        "ffmpeg", "-ss", str(start), "-i", input_file, "-t", str(duration),
        "-c:v", "copy",  # ভিডিও স্ট্রিম কপি
        "-c:a", "copy",  # অডিও স্ট্রিম কপি
        output_file, "-y"
    ]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if process.returncode != 0:
        print(f"FFmpeg Cut Error (Input: {input_file}, Output: {output_file}):\nSTDOUT: {process.stdout}\nSTDERR: {process.stderr}")
        raise RuntimeError(f"ভিডিও কাট করতে ব্যর্থ। ত্রুটি: {process.stderr[:500]}...")


if __name__ == "__main__":
    print("Bot is starting...")
    bot.run()
