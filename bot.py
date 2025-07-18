# bot.py

import os
import asyncio
import subprocess
import json
import logging

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageNotModified

# লগিং কনফিগার করা হচ্ছে যাতে Render-এর লগে বিস্তারিত তথ্য দেখা যায়
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
LOGGER = logging.getLogger(__name__)

# এনভায়রনমেন্ট ভেরিয়েবল থেকে API_ID, API_HASH, BOT_TOKEN লোড করা হচ্ছে
try:
    API_ID = int(os.environ.get("API_ID"))
    API_HASH = os.environ.get("API_HASH")
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
except (ValueError, TypeError):
    LOGGER.critical("API_ID, API_HASH, বা BOT_TOKEN সঠিকভাবে সেট করা নেই। বট বন্ধ হয়ে যাচ্ছে।")
    exit()

# Pyrogram ক্লায়েন্ট ইনিশিয়ালাইজ করা হচ্ছে
bot = Client("ad_merge_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- নির্ভরযোগ্য ফাইল-ভিত্তিক সেশন ম্যানেজমেন্ট ---
SESSIONS_DIR = "user_sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

def get_session_path(user_id):
    """প্রতিটি ব্যবহারকারীর জন্য আলাদা ফাইল পাথ তৈরি করে।"""
    return os.path.join(SESSIONS_DIR, f"{user_id}.json")

def save_session(user_id, data):
    """ব্যবহারকারীর সেশন ডেটা ফাইলে সেভ করে।"""
    try:
        with open(get_session_path(user_id), "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        LOGGER.error(f"সেশন সেভ করতে সমস্যা (User ID: {user_id}): {e}")

def load_session(user_id):
    """ফাইল থেকে ব্যবহারকারীর সেশন ডেটা লোড করে।"""
    path = get_session_path(user_id)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    return {}

def clear_session(user_id):
    """ব্যবহারকারীর সেশন ফাইল ডিলিট করে দেয়।"""
    path = get_session_path(user_id)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            LOGGER.error(f"সেশন ফাইল মুছতে সমস্যা (User ID: {user_id}): {e}")

# --- আপনার মূল লজিক অনুযায়ী হ্যান্ডলার ---

@bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_id = message.from_user.id
    clear_session(user_id)
    save_session(user_id, {})  # খালি সেশন তৈরি করা
    await message.reply("👋 মুভি ভিডিও ফরওয়ার্ড করুন:")

@bot.on_message(filters.video & filters.private)
async def handle_video(client, message):
    user_id = message.from_user.id
    session = load_session(user_id)

    # যদি কোনো কারণে সেশন না থাকে
    if session is None:
        await message.reply("একটি সমস্যা হয়েছে, অনুগ্রহ করে /start লিখে আবার শুরু করুন।")
        return

    # প্রথম ভিডিওটি মুভি হিসেবে ধরা হবে
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
    # পরের ভিডিওগুলো বিজ্ঞাপন হিসেবে ধরা হবে
    else:
        # বিজ্ঞাপন সংখ্যা সেট করা হয়েছে কিনা তা পরীক্ষা করুন
        if "ad_count" not in session:
            await message.reply("অনুগ্রহ করে প্রথমে বিজ্ঞাপনের সংখ্যা সিলেক্ট করুন।")
            return

        ads_info = session.get("ads_info", [])
        ads_info.append({"chat_id": message.chat.id, "message_id": message.id})
        session["ads_info"] = ads_info
        save_session(user_id, session)

        if len(ads_info) == session.get("ad_count", 0):
            processing_message = await message.reply("🔧 ভিডিও প্রসেস শুরু করছি, একটু অপেক্ষা করুন...")
            # প্রসেসিং একটি ব্যাকগ্রাউন্ড টাস্ক হিসেবে চলবে
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

async def process_videos(client, user_id, processing_message_id):
    session = load_session(user_id)
    
    async def edit_status(text):
        try:
            await client.edit_message_text(user_id, processing_message_id, text)
        except MessageNotModified:
            pass
        except Exception as e:
            LOGGER.warning(f"স্ট্যাটাস এডিট করতে সমস্যা: {e}")

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
        
        # মুভি সেগমেন্ট এবং বিজ্ঞাপন যুক্ত করা
        for i in range(len(ad_paths)):
            seg_file = f"segment_{user_id}_{i}.mp4"
            await edit_status(f"✂️ মুভি সেগমেন্ট {i+1} কাটা হচ্ছে...")
            cut_video(movie_path, segment_duration * i, segment_duration, seg_file)
            segments_for_concat.append(seg_file)
            temp_files.append(seg_file)
            segments_for_concat.append(ad_paths[i])

        # মুভির শেষ অংশ যুক্ত করা
        last_seg_start = segment_duration * len(ad_paths)
        last_seg_duration = duration - last_seg_start
        if last_seg_duration > 1:
            await edit_status("✂️ মুভির শেষ সেগমেন্ট কাটা হচ্ছে...")
            last_seg_file = f"segment_{user_id}_last.mp4"
            cut_video(movie_path, last_seg_start, last_seg_duration, last_seg_file)
            segments_for_concat.append(last_seg_file)
            temp_files.append(last_seg_file)

        concat_file = f"concat_{user_id}.txt"
        with open(concat_file, "w", encoding='utf-8') as f:
            for seg in segments_for_concat:
                f.write(f"file '{os.path.basename(seg)}'\n")
        temp_files.append(concat_file)

        await edit_status("🔗 ভিডিওগুলো যুক্ত করা হচ্ছে...")
        final_video = f"final_{user_id}.mp4"
        cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", "-y", final_video]
        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode != 0:
            LOGGER.error(f"FFMPEG Concat Error: {process.stderr}")
            raise RuntimeError(f"ভিডিও যুক্ত করতে ব্যর্থ।")
        temp_files.append(final_video)
        
        await client.send_video(user_id, final_video, caption="✅ বিজ্ঞাপনসহ মুভি প্রস্তুত হয়েছে!")
        await client.delete_messages(user_id, processing_message_id) # স্ট্যাটাস মেসেজ ডিলিট করা

    except Exception as e:
        LOGGER.error(f"প্রসেসিং ফেইল্ড (User ID: {user_id}): {e}", exc_info=True)
        error_message = f"❌ ভিডিও প্রসেস করতে সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন। \n\nত্রুটি: `{e}`"
        await edit_status(error_message)

    finally:
        for f in temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception as e:
                    LOGGER.error(f"ফাইল মুছতে সমস্যা {f}: {e}")
        clear_session(user_id)

def get_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return int(float(json.loads(result.stdout)['format']['duration']))

def cut_video(input_file, start, duration, output_file):
    cmd = ["ffmpeg", "-ss", str(start), "-i", input_file, "-t", str(duration), "-c", "copy", "-y", output_file]
    subprocess.run(cmd, capture_output=True, text=True, check=True)

async def main():
    """বটকে চালু করে এবং চলতে রাখে"""
    LOGGER.info("বট চালু হচ্ছে...")
    await bot.start()
    me = await bot.get_me()
    LOGGER.info(f"{me.first_name} | @{me.username} বট সফলভাবে চালু হয়েছে।")
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("বট বন্ধ করা হচ্ছে...")
