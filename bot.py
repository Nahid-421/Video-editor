import os
import logging
import subprocess
import threading
import shutil
import asyncio
import sqlite3
import json
import time as a_time

from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- কনফিগারেশন ---
TELEGRAM_TOKEN = "7849157640:AAFyGM8F-Yk7tqH2A_vOfVGqMx6bXPq-pTI"
WEBHOOK_URL = "https://video-editor-4v54.onrender.com/webhook"
DB_NAME = 'bot_data.db'

# --- লগিং সেটআপ ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ডেটাবেস ফাংশন (SQLite) ---
def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = get_db_connection()
        conn.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, state TEXT, data TEXT)')
        conn.commit()
        conn.close()
        logger.info("SQLite database initialized.")
    except Exception as e:
        logger.error(f"DB init failed: {e}", exc_info=True)

def set_user_data(user_id, state=None, data_to_add=None):
    try:
        current_data = get_user_data(user_id).get('data', {})
        if data_to_add: current_data.update(data_to_add)
        current_state = state if state is not None else get_user_data(user_id).get('state')
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO users (user_id, state, data) VALUES (?, ?, ?)", (user_id, current_state, json.dumps(current_data)))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Set user data failed for {user_id}: {e}", exc_info=True)

def get_user_data(user_id):
    try:
        conn = get_db_connection()
        user_row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        if user_row: return {'user_id': user_row['user_id'], 'state': user_row['state'], 'data': json.loads(user_row['data'] or '{}')}
    except Exception as e:
        logger.error(f"Get user data failed for {user_id}: {e}", exc_info=True)
    return {}

def delete_user_data(user_id):
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Delete user data failed for {user_id}: {e}", exc_info=True)

# --- ব্যবহারকারীর অবস্থা ---
STATE_AWAITING_MOVIE = 'awaiting_movie'
STATE_AWAITING_AD = 'awaiting_ad'
STATE_AWAITING_AD_COUNT = 'awaiting_ad_count'
STATE_PROCESSING = 'processing'

# --- মূল ভিডিও প্রসেসিং ফাংশন ---
def process_video_thread(user_id, chat_id, token, progress_message_id):
    bot = Bot(token=token)
    temp_dir = f"temp_{user_id}"

    def run_async(coro):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try: return loop.run_until_complete(coro)
        finally: loop.close()

    try:
        user_data = get_user_data(user_id).get('data', {})
        movie_file_id, ad_file_id, ad_count = user_data.get('movie_file_id'), user_data.get('ad_file_id'), user_data.get('ad_count')
        if not all([movie_file_id, ad_file_id, ad_count]): raise ValueError("Required data missing.")
        os.makedirs(temp_dir, exist_ok=True)
        
        movie_path, ad_path = os.path.join(temp_dir, 'movie.mp4'), os.path.join(temp_dir, 'ad.mp4')
        run_async(bot.edit_message_text("Downloading files...", chat_id=chat_id, message_id=progress_message_id))
        run_async(bot.get_file(movie_file_id).download_to_drive(movie_path))
        run_async(bot.get_file(ad_file_id).download_to_drive(ad_path))
        
        ffprobe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', movie_path]
        result = subprocess.run(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        total_duration = float(result.stdout)
        split_duration = total_duration / (ad_count + 1)
        
        concat_list_path = os.path.join(temp_dir, 'concat_list.txt')
        with open(concat_list_path, 'w') as f:
            for i in range(ad_count):
                f.write(f"file '{os.path.basename(movie_path)}'\ninpoint {i * split_duration}\noutpoint {(i + 1) * split_duration}\n")
                f.write(f"file '{os.path.basename(ad_path)}'\n")
            f.write(f"file '{os.path.basename(movie_path)}'\ninpoint {ad_count * split_duration}\n")

        output_path = os.path.join(temp_dir, 'final_movie.mp4')
        ffmpeg_cmd = ['ffmpeg', '-y', '-progress', 'pipe:1', '-nostats', '-f', 'concat', '-safe', '0', '-i', concat_list_path, '-c', 'copy', output_path]
        
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, encoding="utf-8")
        
        last_update_time, last_percentage = a_time.time(), -1
        for line in process.stdout:
            if "out_time_ms" in line:
                percentage = int((int(line.strip().split("=")[1]) / 1_000_000) / total_duration * 100)
                if percentage > last_percentage and percentage % 5 == 0 and a_time.time() - last_update_time > 3:
                    try:
                        run_async(bot.edit_message_text(f"Processing video... {percentage}% ⚙️", chat_id=chat_id, message_id=progress_message_id))
                        last_percentage, last_update_time = percentage, a_time.time()
                    except Exception as e: logger.warning(f"Could not edit progress: {e}")
        process.wait()

        run_async(bot.edit_message_text("Processing complete! ✅\nUploading...", chat_id=chat_id, message_id=progress_message_id))
        with open(output_path, 'rb') as final_video:
            run_async(bot.send_video(chat_id, video=final_video, caption="Here is your edited movie."))
    except Exception as e:
        logger.error(f"Error in process_video thread:", exc_info=True)
        run_async(bot.edit_message_text("A critical error occurred. Please /start again.", chat_id=chat_id, message_id=progress_message_id))
    finally:
        delete_user_data(user_id)
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir)

# --- টেলিগ্রাম হ্যান্ডলার ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_data(update.effective_user.id, state=STATE_AWAITING_MOVIE)
    await update.message.reply_html(f"👋 Hello {update.effective_user.mention_html()}!\n\nFirst, send the main **movie file**.")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    delete_user_data(update.effective_user.id)
    await update.message.reply_text("Process cancelled. /start again.")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, state = update.effective_user.id, get_user_data(update.effective_user.id).get('state')
    if state == STATE_AWAITING_MOVIE:
        set_user_data(user_id, state=STATE_AWAITING_AD, data_to_add={'movie_file_id': update.message.video.file_id})
        await update.message.reply_text("Movie received. ✅\nNow, send the **advertisement video**.")
    elif state == STATE_AWAITING_AD:
        set_user_data(user_id, state=STATE_AWAITING_AD_COUNT, data_to_add={'ad_file_id': update.message.video.file_id})
        await update.message.reply_text("Ad received. ✅\nNow, tell me **how many times** to show the ad? (e.g., 2)")
    else: await update.message.reply_text("Not expecting a video now. /start to begin.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, state = update.effective_user.id, get_user_data(update.effective_user.id).get('state')
    if state == STATE_AWAITING_AD_COUNT:
        if update.message.text and update.message.text.isdigit() and int(update.message.text) > 0:
            count = int(update.message.text)
            set_user_data(user_id, state=STATE_PROCESSING, data_to_add={'ad_count': count})
            progress_message = await update.message.reply_text(f"Info received. Starting process...")
            threading.Thread(target=process_video_thread, args=(user_id, update.effective_chat.id, TELEGRAM_TOKEN, progress_message.message_id)).start()
        else: await update.message.reply_text("❌ Invalid. Send a **number greater than 0**.")
    elif state == STATE_PROCESSING: await update.message.reply_text("Processing your video. Please wait.")
    else: await update.message.reply_text("Not expecting text now. /start to begin.")

# --- Flask ওয়েব অ্যাপ এবং বট চালু করা ---
init_db()
ptb_app = Application.builder().token(TELEGRAM_TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start_command))
ptb_app.add_handler(CommandHandler("cancel", cancel_command))
ptb_app.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, handle_video))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

app = Flask(__name__) # Gunicorn এই 'app' ভেরিয়েবলটি খুঁজবে

@app.route('/')
def index():
    return "Bot is alive and running!"

@app.route('/webhook', methods=['POST'])
async def webhook():
    try:
        await ptb_app.update_queue.put(Update.de_json(request.get_json(force=True), ptb_app.bot))
    except Exception as e:
        logger.error(f"Error in webhook: {e}", exc_info=True)
    return 'ok'

async def bot_setup():
    await ptb_app.initialize()
    await ptb_app.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    logger.info("Bot started and webhook is set.")

# Gunicorn সার্ভার শুরু হওয়ার সাথে সাথে ওয়েবহুক সেট করা
asyncio.run(bot_setup())
