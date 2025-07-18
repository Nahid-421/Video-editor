# main.py

import os
import asyncio
import subprocess
import json
import logging
import threading
from flask import Flask

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageNotModified

# --- লগিং এবং এনভায়রনমেন্ট ভেরিয়েবল সেটআপ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
LOGGER = logging.getLogger(__name__)

try:
    API_ID = int(os.environ.get("API_ID"))
    API_HASH = os.environ.get("API_HASH")
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
except (ValueError, TypeError) as e:
    LOGGER.critical(f"প্রয়োজনীয় এনভায়রনমেন্ট ভেরিয়েবল সেট করা নেই: {e}")
    exit()

# --- Flask ওয়েব অ্যাপ সেটআপ ---
app = Flask(__name__)

@app.route('/')
def index():
    """ওয়েব সার্ভারকে সচল রাখতে এবং বট চালু আছে কিনা তা জানাতে এই রুটটি কাজ করবে।"""
    return "বট সফলভাবে চলছে এবং কাজ করার জন্য প্রস্তুত!"

# --- Pyrogram বট সেটআপ ---
bot = Client("ad_merge_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- নির্ভরযোগ্য ফাইল-ভিত্তিক সেশন ম্যানেজমেন্ট ---
SESSIONS_DIR = "user_sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

def get_session_path(user_id):
    return os.path.join(SESSIONS_DIR, f"{user_id}.json")

def save_session(user_id, data):
    try:
        with open(get_session_path(user_id), "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        LOGGER.error(f"সেশন সেভ করতে সমস্যা (User ID: {user_id}): {e}")

def load_session(user_id):
    path = get_session_path(user_id)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    return {}

def clear_session(user_id):
    path = get_session_path(user_id)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            LOGGER.error(f"সেশন ফাইল মুছতে সমস্যা (User ID: {user_id}): {e}")

# --- বট হ্যান্ডলার ---
@bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_id = message.from_user.id
    clear_session(user_id)
    save_session(user_id, {})
    await message.reply("👋 মুভি ভিডিও ফরওয়ার্ড করুন:")

@bot.on_message(filters.video & filters.private)
async def handle_video(client, message):
    user_id = message.from_user.id
    session = load_session(user_id)
    
    if "movie_info" not in session:
        session["movie_info"] = {"chat_id": message.chat.id, "message_id": message.id}
        save_session(user_id, session)
        await message.reply(
            "📢 কতটি বিজ্ঞাপন ভিডিও বসাবেন?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("1", callback_data="ads_1"),
                 InlineKeyboardButton("2", callback_data="ads_2"),
                 InlineKeyboardButton("3", callback_data="ads_3")],
            ])
        )
    else:
        if "ad_count" not in session:
            await message.reply("অনুগ্রহ করে প্রথমে বিজ্ঞাপনের সংখ্যা সিলেক্ট করুন।")
            return

        ads_info = session.get("ads_info", [])
        ads_info.append({"chat_id": message.chat.id, "message_id": message.id})
        session["ads_info"] = ads_info
        save_session(user_id, session)

        if len(ads_info) == session.get("ad_count", 0):
            processing_message = await message.reply("🔧 ভিডিও প্রসেস শুরু করছি, একটু অপেক্ষা করুন...")
            asyncio.create_task(process_videos(client, user_id, processing_message.id))
        else:
            await message.reply(f"অনুগ্রহ করে আরও {session['ad_count'] - len(ads_info)}টি বিজ্ঞাপন ভিডিও পাঠান।")

@bot.on_callback_query()
async def callback_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    session = load_session(user_id)
    if data.startswith("ads_"):
        count = int(data.split("_")[1])
        session["ad_count"] = count
        session["ads_info"] = []
        save_session(user_id, session)
        await callback_query.message.edit_text(f"✅ {count}টি বিজ্ঞাপন ভিডিও পাঠান এখন:")

# --- ভিডিও প্রসেসিং ফাংশন ---
async def process_videos(client, user_id, processing_message_id):
    session = load_session(user_id)
    
    async def edit_status(text):
        try:
            await client.edit_message_text(user_id, processing_message_id, text)
        except MessageNotModified: pass
        except Exception as e: LOGGER.warning(f"স্ট্যাটাস এডিট করতে সমস্যা: {e}")

    movie_info = session.get("movie_info")
    ads_info = session.get("ads_info", [])
    temp_files = []

    try:
        await edit_status("🎬 মুভি ভিডিও ডাউনলোড হচ্ছে...")
        movie_msg = await client.get_messages(movie_info["chat_id"], movie_info["message_id"])
        movie_path = await movie_msg.download(file_name=f"movie_{user_id}.mp4")
        temp_files.append(movie_path)

        ad_paths = []
        await edit_status("📢 বিজ্ঞাপন ভিডিও ডাউনলোড হচ্ছে...")
        for idx, ad_info in enumerate(ads_info):
            ad_msg = await client.get_messages(ad_info["chat_id"], ad_info["message_id"])
            path = await ad_msg.download(file_name=f"ad_{user_id}_{idx}.mp4")
            ad_paths.append(path)
            temp_files.append(path)

        await edit_status("🔧 ভিডিও প্রসেসিং শুরু করছি...")
        duration = get_duration(movie_path)
        segment_duration = duration // (len(ad_paths) + 1)
        
        segments_for_concat = []
        for i in range(len(ad_paths)):
            seg_file = f"segment_{user_id}_{i}.mp4"
            cut_video(movie_path, segment_duration * i, segment_duration, seg_file)
            segments_for_concat.append(seg_file)
            temp_files.append(seg_file)

        last_seg_start = segment_duration * len(ad_paths)
        last_seg_duration = duration - last_seg_start
        if last_seg_duration > 1:
            last_seg_file = f"segment_{user_id}_last.mp4"
            cut_video(movie_path, last_seg_start, last_seg_duration, last_seg_file)
            segments_for_concat.append(last_seg_file)
            temp_files.append(last_seg_file)
        
        # ইন্টারলিভ করে কনক্যাট লিস্ট তৈরি করা
        final_concat_list = []
        ad_iterator = iter(ad_paths)
        for seg in segments_for_concat:
            final_concat_list.append(seg)
            try:
                final_concat_list.append(next(ad_iterator))
            except StopIteration:
                pass

        concat_file = f"concat_{user_id}.txt"
        with open(concat_file, "w", encoding='utf-8') as f:
            for seg in final_concat_list:
                f.write(f"file '{os.path.basename(seg)}'\n")
        temp_files.append(concat_file)

        await edit_status("🔗 ভিডিওগুলো যুক্ত করা হচ্ছে...")
        final_video = f"final_{user_id}.mp4"
        cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", "-y", final_video]
        subprocess.run(cmd, check=True, capture_output=True)
        temp_files.append(final_video)
        
        await client.send_video(user_id, final_video, caption="✅ বিজ্ঞাপনসহ মুভি প্রস্তুত হয়েছে!")
        await client.delete_messages(user_id, processing_message_id)

    except Exception as e:
        LOGGER.error(f"প্রসেসিং ফেইল্ড (User ID: {user_id}): {e}", exc_info=True)
        await edit_status(f"❌ ভিডিও প্রসেস করতে সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন। \n\nত্রুটি: `{e}`")

    finally:
        for f in temp_files:
            if os.path.exists(f):
                try: os.remove(f)
                except Exception: pass
        clear_session(user_id)

def get_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return int(float(json.loads(result.stdout)['format']['duration']))

def cut_video(input_file, start, duration, output_file):
    cmd = ["ffmpeg", "-ss", str(start), "-i", input_file, "-t", str(duration), "-c", "copy", "-y", output_file]
    subprocess.run(cmd, capture_output=True, text=True, check=True)

# --- বট এবং ওয়েব সার্ভার একসাথে চালানোর জন্য ফাংশন ---
def run_bot():
    """Pyrogram বটকে একটি আলাদা থ্রেডে চালায়।"""
    LOGGER.info("Pyrogram বট থ্রেড চালু হচ্ছে...")
    asyncio.run(bot.run())

if __name__ == "__main__":
    # বটকে একটি ব্যাকগ্রাউন্ড থ্রেডে শুরু করুন
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Flask ওয়েব সার্ভার শুরু করুন
    port = int(os.environ.get("PORT", 8080))
    LOGGER.info(f"Flask ওয়েব সার্ভার http://0.0.0.0:{port} এ চালু হচ্ছে...")
    app.run(host='0.0.0.0', port=port, threaded=True)
