import os
import asyncio
import subprocess
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request, abort, jsonify # Flask যোগ করা হয়েছে

# এনভায়রনমেন্ট ভেরিয়েবল থেকে API_ID, API_HASH, BOT_TOKEN লোড করা হচ্ছে
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
# Render বা অন্যান্য প্ল্যাটফর্ম থেকে PORT ভেরিয়েবল নিন, না পেলে 5000 ডিফল্ট
PORT = int(os.environ.get("PORT", 5000))
# আপনার বটের পাবলিক URL, এটি Render বা আপনার হোস্টিং প্রোভাইডার থেকে পাবেন
# উদাহরণ: https://your-bot-name.onrender.com/
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") + "/webhook" # /webhook এন্ডপয়েন্ট যোগ করা হয়েছে

# Flask অ্যাপ ইনিশিয়ালাইজ করা হচ্ছে
app = Flask(__name__)

# Pyrogram ক্লায়েন্ট ইনিশিয়ালাইজ করা হচ্ছে
# Webhook মোডে চলার জন্য no_updates=True সেট করা হয়েছে
bot = Client("ad_merge_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, no_updates=True)

# ইউজার সেশন ডেটা সংরক্ষণের জন্য ডিকশনারি
user_sessions = {}

# স্টার্ট কমান্ড হ্যান্ডলার
@bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_sessions[message.from_user.id] = {}
    await message.reply("👋 মুভি ভিডিও ফরওয়ার্ড করুন:")

# ভিডিও হ্যান্ডলার
@bot.on_message(filters.video & filters.private)
async def handle_video(client, message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id, {})

    if "movie" not in session:
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
        ads = session.get("ads", [])
        ads.append(message)
        session["ads"] = ads
        user_sessions[user_id] = session

        if len(ads) == session.get("ad_count", 1):
            await message.reply("🔧 ভিডিও প্রসেস শুরু করছি, একটু অপেক্ষা করুন...")
            # asyncio.create_task ব্যবহার করা হয়েছে যাতে ওয়েবহুক রিকোয়েস্ট দ্রুত শেষ হয়
            asyncio.create_task(process_videos(client, user_id))
        else:
            await message.reply(f"অনুগ্রহ করে আরও {session['ad_count'] - len(ads)}টি বিজ্ঞাপন ভিডিও পাঠান।")

# কলব্যাক কোয়েরি হ্যান্ডলার
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

# ভিডিও প্রসেসিং ফাংশন (আগের মতোই থাকবে)
async def process_videos(client, user_id):
    session = user_sessions.get(user_id)
    movie_msg = session.get("movie")
    ads_msg = session.get("ads", [])

    movie_path = None
    ad_paths = []
    temp_segment_files = []

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
            raise ValueError("ভিডিও সেগমেন্টের সময়কাল খুব কম বা শুন্য। অনুগ্রহ করে দীর্ঘতর মুভি ভিডিও পাঠান।")

        segments = []

        for i in range(len(ad_paths)):
            seg_file = f"segment_{user_id}_{i}.mp4"
            await client.send_message(user_id, f"✂️ মুভি সেগমেন্ট {i+1} কাটা হচ্ছে...")
            cut_video(movie_path, segment_duration * i, segment_duration, seg_file)
            segments.append(seg_file)
            temp_segment_files.append(seg_file)
            segments.append(ad_paths[i])

        last_seg = f"segment_{user_id}_last.mp4"
        await client.send_message(user_id, "✂️ মুভি শেষ সেগমেন্ট কাটা হচ্ছে...")
        cut_video(movie_path, segment_duration * len(ad_paths), duration - (segment_duration * len(ad_paths)), last_seg)
        segments.append(last_seg)
        temp_segment_files.append(last_seg)

        concat_file = f"concat_{user_id}.txt"
        with open(concat_file, "w") as f:
            for seg in segments:
                f.write(f"file '{seg}'\n")
        await client.send_message(user_id, "🔗 ভিডিওগুলো যুক্ত করা হচ্ছে...")

        final_video = f"final_{user_id}.mp4"
        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c:v", "copy",
            "-c:a", "copy",
            "-y", final_video
        ]
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if process.returncode != 0:
            print(f"FFmpeg Concat Error (User ID: {user_id}):\nSTDOUT: {process.stdout}\nSTDERR: {process.stderr}")
            raise RuntimeError(f"ভিডিও যুক্ত করতে ব্যর্থ। ত্রুটি: {process.stderr[:500]}...")
        else:
            print(f"FFmpeg Concat Success (User ID: {user_id}):\nSTDOUT: {process.stdout}")

        await client.send_video(user_id, final_video, caption="✅ বিজ্ঞাপনসহ মুভি প্রস্তুত হয়েছে!")

    except Exception as e:
        print(f"Error processing video for user {user_id}: {e}")
        await client.send_message(user_id, f"❌ ভিডিও প্রসেস করতে সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন বা অন্য ভিডিও পাঠান। \n\nত্রুটি: `{e}`")

    finally:
        cleanup_files = [movie_path, concat_file, final_video] + ad_paths + temp_segment_files
        for f in cleanup_files:
            if f and os.path.exists(f):
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
        "-c:v", "copy",
        "-c:a", "copy",
        output_file, "-y"
    ]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if process.returncode != 0:
        print(f"FFmpeg Cut Error (Input: {input_file}, Output: {output_file}):\nSTDOUT: {process.stdout}\nSTDERR: {process.stderr}")
        raise RuntimeError(f"ভিডিও কাট করতে ব্যর্থ। ত্রুটি: {process.stderr[:500]}...")

# Flask ওয়েবহুক এন্ডপয়েন্ট
@app.route("/webhook", methods=["POST"])
async def telegram_webhook():
    if not request.json:
        abort(400) # যদি JSON ডেটা না থাকে, 400 Bad Request রিটার্ন করুন

    # Pyrogram-এর process_update ব্যবহার করে ইনকামিং আপডেট হ্যান্ডেল করুন
    # এটি Pyrogram-এর মেসেজ হ্যান্ডলারগুলোকে ট্রিগার করবে
    await bot.process_update(request.json)
    return jsonify({"status": "ok"}) # টেলিগ্রামকে সফল প্রতিক্রিয়া পাঠান

# বট শুরু করার আগে Webhook সেট করুন
async def set_webhook():
    if WEBHOOK_URL:
        try:
            print(f"Setting webhook to: {WEBHOOK_URL}")
            await bot.start() # বট ক্লায়েন্ট শুরু করুন
            await bot.set_webhook(WEBHOOK_URL)
            print("Webhook set successfully!")
        except Exception as e:
            print(f"Failed to set webhook: {e}")
    else:
        print("WEBHOOK_URL environment variable is not set. Cannot set webhook.")
        print("Please set WEBHOOK_URL to your public bot URL (e.g., https://your-app-name.onrender.com/).")

# অ্যাপ এবং ওয়েবহুক শুরু করুন
if __name__ == "__main__":
    # Pyrogram ক্লায়েন্ট শুরু করুন এবং ওয়েবহুক সেট করুন
    # এটি একটি পৃথক অ্যাসিনক্রোনাস টাস্ক হিসাবে চালানো হয়
    asyncio.get_event_loop().run_until_complete(set_webhook())

    print(f"Flask app starting on port {PORT}...")
    # Flask অ্যাপ চালান
    # debug=True প্রোডাকশনের জন্য ব্যবহার করা উচিত নয়
    # host='0.0.0.0' সেট করা হয়েছে যাতে এটি যেকোনো ইন্টারফেস থেকে অ্যাক্সেসযোগ্য হয়
    app.run(host='0.0.0.0', port=PORT, debug=False)
