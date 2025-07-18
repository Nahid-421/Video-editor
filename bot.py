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

# --- কনফিগারেশন ---
TELEGRAM_TOKEN = "7849157640:AAFyGM8F-Yk7tqH2A_vOfVGqMx6bXPq-pTI"
WEBHOOK_URL = "https://video-editor-4v54.onrender.com/webhook"
DB_NAME = 'bot_data.db'

# --- লগিং সেটআপ ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ডেটাবেস ফাংশন (SQLite) ---
def init_db():
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''
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
        logger.error(f"Failed to initialize database: {e}")

def set_user_data(user_id, state=None, data=None):
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT data FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        current_data = json.loads(row[0]) if row and row[0] else {}
        if data:
            current_data.update(data)
        
        cursor.execute("INSERT OR REPLACE INTO users (user_id, state, data) VALUES (?, ?, ?)", 
                       (user_id, state if state is not None else (get_user_data(user_id).get('state')), json.dumps(current_data)))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to set user data for {user_id}: {e}", exc_info=True)

def get_user_data(user_id):
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT state, data FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            state, data_str = row
            user_data = json.loads(data_str) if data_str else {}
            user_data['state'] = state
            return user_data
    except Exception as e:
        logger.error(f"Failed to get user data for {user_id}: {e}", exc_info=True)
    return {}

def delete_user_data(user_id):
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to delete user data for {user_id}: {e}", exc_info=True)

# --- ব্যবহারকারীর অবস্থা ---
STATE_AWAITING_MOVIE = 'awaiting_movie'
STATE_AWAITING_AD = 'awaiting_ad'
STATE_AWAITING_AD_COUNT = 'awaiting_ad_count'

# --- মূল ভিডিও প্রসেসিং ফাংশন ---
def process_video(user_id, chat_id, context):
    bot = context.bot
    temp_dir = f"temp_{user_id}"
    try:
        user_data = get_user_data(user_id)
        movie_file_id = user_data.get('movie_file_id')
        ad_file_id = user_data.get('ad_file_id')
        ad_count = user_data.get('ad_count')

        if not all([movie_file_id, ad_file_id, ad_count]):
            raise ValueError("Required data not found in the database.")

        os.makedirs(temp_dir, exist_ok=True)
        bot.send_message(chat_id, "Downloading files... 📥")
        
        movie_path = os.path.join(temp_dir, 'movie.mp4')
        (bot.get_file(movie_file_id)).download_to_drive(movie_path)
        ad_path = os.path.join(temp_dir, 'ad.mp4')
        (bot.get_file(ad_file_id)).download_to_drive(ad_path)
        
        bot.send_message(chat_id, "Download complete. Processing video... ⚙️")

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
        bot.send_message(chat_id, "Merging files... This is the longest step. Please be patient.")
        
        ffmpeg_cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_list_path, '-c', 'copy', output_path]
        subprocess.run(ffmpeg_cmd, check=True)

        bot.send_message(chat_id, "Processing complete! ✅\nUploading the file...")
        with open(output_path, 'rb') as final_video:
            bot.send_video(chat_id, video=final_video, caption="Here is your edited movie.", read_timeout=120, write_timeout=120)

    except Exception as e:
        logger.error(f"Error in process_video thread for user {user_id}:", exc_info=True)
        bot.send_message(chat_id, f"A critical error occurred. Press /start to try again.")
    finally:
        delete_user_data(user_id)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

# --- টেলিগ্রাম হ্যান্ডলার ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    set_user_data(user.id, state=STATE_AWAITING_MOVIE, data={})
    await update.message.reply_html(f"👋 Hello {user.mention_html()}!\n\nFirst, send me the main **movie file**.")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    delete_user_data(update.effective_user.id)
    await update.message.reply_text("Process cancelled. Press /start to begin again.")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_data = get_user_data(user_id)
    state = user_data.get('state')

    if state == STATE_AWAITING_MOVIE:
        if update.message.video:
            set_user_data(user_id, state=STATE_AWAITING_AD, data={'movie_file_id': update.message.video.file_id})
            await update.message.reply_text("Movie received. ✅\n\nNow, send me the **advertisement video**.")
        else: await update.message.reply_text("❌ Invalid input. Please send a **video file**.")
    elif state == STATE_AWAITING_AD:
        if update.message.video:
            set_user_data(user_id, state=STATE_AWAITING_AD_COUNT, data={'ad_file_id': update.message.video.file_id})
            await update.message.reply_text("Ad received. ✅\n\nNow, tell me **how many times** you want to place the ad? (e.g., 2)")
        else: await update.message.reply_text("❌ Invalid input. Please send an **advertisement video file**.")
    elif state == STATE_AWAITING_AD_COUNT:
        if update.message.text and update.message.text.isdigit() and int(update.message.text) > 0:
            count = int(update.message.text)
            set_user_data(user_id, state='processing', data={'ad_count': count})
            await update.message.reply_text(f"Information received. Starting the process to add the ad {count} times.")
            threading.Thread(target=process_video, args=(user_id, chat_id, context)).start()
        else: await update.message.reply_text("❌ Invalid input. Please send a **number greater than 0**.")

# --- Flask ওয়েব অ্যাপ এবং বট চালু করা ---
init_db()

# Application object টি এখন এখানে তৈরি করা হচ্ছে
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

# হ্যান্ডলার যোগ করা
bot_app.add_handler(CommandHandler("start", start_command))
bot_app.add_handler(CommandHandler("cancel", cancel_command))
bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

# Flask অ্যাপ
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is alive and running!"

@app.route('/webhook', methods=['POST'])
async def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot_app.bot)
        # <<<<<<< এই লাইনটিই সব সমস্যার সমাধান >>>>>>>
        await bot_app.process_update(update)
    except Exception as e:
        logger.error("!!! CRITICAL ERROR IN WEBHOOK !!!", exc_info=True)
    return 'ok', 200

# অ্যাসিঙ্ক্রোনাস ফাংশনগুলো এখন একসাথে চালানো হবে
async def main():
    # <<<<<<< এই লাইনটিই সব সমস্যার সমাধান >>>>>>>
    await bot_app.initialize()
    await bot_app.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
    logger.info(f"Webhook has been set to {WEBHOOK_URL}")

# Gunicorn সার্ভার শুরু হওয়ার সাথে সাথে main() ফাংশনটি চালানো হবে
if __name__ != '__main__':
    # এটি নিশ্চিত করে যে event loop সঠিকভাবে চলছে
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.create_task(main())
        else:
            asyncio.run(main())
    except RuntimeError:
        asyncio.run(main())
