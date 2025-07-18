import os
import logging
import subprocess
import threading
import shutil
import asyncio
import sqlite3
import json

from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# --- প্রাথমিক সেটআপ ---
load_dotenv()

# --- কনফিগারেশন (আপনার তথ্য সরাসরি বসানো হয়েছে) ---
TELEGRAM_TOKEN = "7849157640:AAFyGM8F-Yk7tqH2A_vOfVGqMx6bXPq-pTI"
WEBHOOK_URL = "https://video-editor-4v54.onrender.com/webhook"
DB_NAME = 'bot_data.db'

# --- লগিং সেটআপ ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ডেটাবেস ফাংশন (SQLite - নির্ভরযোগ্য এবং থ্রেড-সেফ) ---
def get_db_connection():
    """ডেটাবেসের সাথে একটি নতুন থ্রেড-সেফ কানেকশন তৈরি করে"""
    conn = sqlite3.connect(DB_NAME, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """অ্যাপ্লিকেশন শুরু হওয়ার সময় ডেটাবেস এবং টেবিল তৈরি করে"""
    try:
        conn = get_db_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                state TEXT,
                data TEXT
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("SQLite database initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)

def set_user_data(user_id, state=None, data_to_add=None):
    """ব্যবহারকারীর ডেটা সেভ বা আপডেট করে"""
    try:
        current_data = get_user_data(user_id).get('data', {})
        if data_to_add:
            current_data.update(data_to_add)

        current_state = state if state is not None else get_user_data(user_id).get('state')
        
        conn = get_db_connection()
        conn.execute(
            "INSERT OR REPLACE INTO users (user_id, state, data) VALUES (?, ?, ?)",
            (user_id, current_state, json.dumps(current_data))
        )
        conn.commit()
        conn.close()
        logger.info(f"User {user_id}: State set to '{current_state}'.")
    except Exception as e:
        logger.error(f"Failed to set user data for {user_id}: {e}", exc_info=True)


def get_user_data(user_id):
    """ব্যবহারকারীর ডেটা নিয়ে আসে"""
    try:
        conn = get_db_connection()
        user_row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        if user_row:
            return {
                'user_id': user_row['user_id'],
                'state': user_row['state'],
                'data': json.loads(user_row['data'] or '{}')
            }
    except Exception as e:
        logger.error(f"Failed to get user data for {user_id}: {e}", exc_info=True)
    return {}

def delete_user_data(user_id):
    """প্রসেস শেষে ব্যবহারকারীর ডেটা মুছে ফেলে"""
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        logger.info(f"Data for user {user_id} deleted.")
    except Exception as e:
        logger.error(f"Failed to delete user data for {user_id}: {e}", exc_info=True)

# --- ব্যবহারকারীর অবস্থা ---
STATE_AWAITING_MOVIE = 'awaiting_movie'
STATE_AWAITING_AD = 'awaiting_ad'
STATE_AWAITING_AD_COUNT = 'awaiting_ad_count'
STATE_PROCESSING = 'processing'

# --- মূল ভিডিও প্রসেসিং ফাংশন ---
def process_video(user_id, chat_id, context):
    bot = context.bot
    temp_dir = f"temp_{user_id}"
    try:
        user_data = get_user_data(user_id).get('data', {})
        movie_file_id = user_data.get('movie_file_id')
        ad_file_id = user_data.get('ad_file_id')
        ad_count = user_data.get('ad_count')

        if not all([movie_file_id, ad_file_id, ad_count]):
            raise ValueError("Required data not found for processing.")

        os.makedirs(temp_dir, exist_ok=True)
        bot.send_message(chat_id, "Downloading files... 📥 This may take a while.")
        
        movie_path = os.path.join(temp_dir, 'movie.mp4')
        (bot.get_file(movie_file_id)).download_to_drive(movie_path)
        ad_path = os.path.join(temp_dir, 'ad.mp4')
        (bot.get_file(ad_file_id)).download_to_drive(ad_path)
        
        bot.send_message(chat_id, "Download complete. Processing video... ⚙️ This is the longest step.")

        ffprobe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', movie_path]
        result = subprocess.run(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        duration = float(result.stdout)

        num_splits = ad_count + 1
        split_duration = duration / num_splits
        
        concat_list_path = os.path.join(temp_dir, 'concat_list.txt')
        with open(concat_list_path, 'w') as f:
            for i in range(ad_count):
                f.write(f"file '{os.path.basename(movie_path)}'\n")
                f.write(f"inpoint {i * split_duration}\n")
                f.write(f"outpoint {(i * split_duration) + split_duration}\n")
                f.write(f"file '{os.path.basename(ad_path)}'\n")
            
            f.write(f"file '{os.path.basename(movie_path)}'\n")
            f.write(f"inpoint {ad_count * split_duration}\n")

        output_path = os.path.join(temp_dir, 'final_movie.mp4')
        bot.send_message(chat_id, "Merging files... Please be patient.")
        
        ffmpeg_cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_list_path, '-c', 'copy', output_path]
        subprocess.run(ffmpeg_cmd, check=True)

        bot.send_message(chat_id, "Processing complete! ✅\nUploading the file...")
        with open(output_path, 'rb') as final_video:
            bot.send_video(chat_id, video=final_video, caption="Here is your edited movie.", read_timeout=120, write_timeout=120)

    except Exception as e:
        logger.error(f"Error in process_video thread for user {user_id}:", exc_info=True)
        bot.send_message(chat_id, f"A critical error occurred during processing. Please try again by sending /start.")
    finally:
        delete_user_data(user_id)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

# --- টেলিগ্রাম হ্যান্ডলার ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"User {user.id} started the bot.")
    set_user_data(user.id, state=STATE_AWAITING_MOVIE, data_to_add={'user_id': user.id})
    await update.message.reply_html(f"👋 Hello {user.mention_html()}!\n\nI can add ads to your movies.\nFirst, send me the main **movie file**.")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    delete_user_data(user_id)
    await update.message.reply_text("Process cancelled. You can start a new one by sending /start.")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    state = user_data.get('state')
    
    logger.info(f"Message received from user {user_id}. Current state: {state}")

    if state == STATE_AWAITING_MOVIE:
        if update.message.video:
            set_user_data(user_id, state=STATE_AWAITING_AD, data_to_add={'movie_file_id': update.message.video.file_id})
            await update.message.reply_text("Movie received. ✅\n\nNow, send me the **advertisement video**.")
        else:
            await update.message.reply_text("I'm waiting for a movie file. Please send a video to start.")
            
    elif state == STATE_AWAITING_AD:
        if update.message.video:
            set_user_data(user_id, state=STATE_AWAITING_AD_COUNT, data_to_add={'ad_file_id': update.message.video.file_id})
            await update.message.reply_text("Ad received. ✅\n\nNow, tell me **how many times** you want to place the ad? (e.g., 2)")
        else:
            await update.message.reply_text("I'm waiting for an ad file. Please send an **advertisement video file**.")

    elif state == STATE_AWAITING_AD_COUNT:
        if update.message.text and update.message.text.isdigit() and int(update.message.text) > 0:
            count = int(update.message.text)
            set_user_data(user_id, state=STATE_PROCESSING, data_to_add={'ad_count': count})
            await update.message.reply_text(f"Information received. Starting the process to add the ad {count} times. You will be notified when it's done.")
            threading.Thread(target=process_video, args=(user_id, update.effective_chat.id, context)).start()
        else:
            await update.message.reply_text("❌ Invalid input. Please send a **number greater than 0** (e.g., 1, 2, 3).")
    
    elif state == STATE_PROCESSING:
        await update.message.reply_text("I am currently processing your video. Please wait until it is complete.")
    
    else:
        await update.message.reply_text("Something went wrong or the process was completed. Please start over by sending /start.")


# --- Flask ওয়েব অ্যাপ এবং বট চালু করা ---
init_db()

bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start_command))
bot_app.add_handler(CommandHandler("cancel", cancel_command))
bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is alive and running!"

@app.route('/webhook', methods=['POST'])
async def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot_app.bot)
        await bot_app.process_update(update)
    except Exception as e:
        logger.error("!!! CRITICAL ERROR IN WEBHOOK !!!", exc_info=True)
    return 'ok', 200

async def main():
    await bot_app.initialize()
    await bot_app.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    logger.info(f"Webhook has been set to {WEBHOOK_URL}")

if __name__ != '__main__':
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running(): loop.create_task(main())
        else: asyncio.run(main())
    except RuntimeError: asyncio.run(main())
