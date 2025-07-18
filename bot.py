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

load_dotenv()

# --- Configuration ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
DB_NAME = 'bot_data.db' # ডেটাবেস ফাইলের নাম

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Setup (SQLite) ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # ব্যবহারকারীর তথ্য রাখার জন্য একটি টেবিল তৈরি করা
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            state TEXT,
            data TEXT
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("SQLite database initialized.")

# --- Database Helper Functions (Using SQLite) ---
def set_user_data(user_id, state=None, data=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # প্রথমে ব্যবহারকারীর সব ডেটা পড়া
    cursor.execute("SELECT data FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    current_data = json.loads(row[0]) if row and row[0] else {}

    # নতুন ডেটা আপডেট করা
    if data:
        current_data.update(data)
    
    # ডেটাবেসে আবার সেভ করা
    if state is not None:
        cursor.execute("INSERT OR REPLACE INTO users (user_id, state, data) VALUES (?, ?, ?)", 
                       (user_id, state, json.dumps(current_data)))
    else:
        # যদি শুধু ডেটা আপডেট হয় কিন্তু স্টেট না
        cursor.execute("UPDATE users SET data = ? WHERE user_id = ?", (json.dumps(current_data), user_id))

    conn.commit()
    conn.close()

def get_user_data(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT state, data FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        state, data_str = row
        user_data = json.loads(data_str) if data_str else {}
        user_data['state'] = state
        return user_data
    return {}

def delete_user_data(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# --- User States ---
STATE_AWAITING_MOVIE = 'awaiting_movie'
STATE_AWAITING_AD = 'awaiting_ad'
STATE_AWAITING_AD_COUNT = 'awaiting_ad_count'

# --- Video Processing and Other Functions (কোনো পরিবর্তন নেই) ---
# ... (আগের কোডের বাকি অংশ এখানে হুবহু একই থাকবে)
# ... (আমি পুরোটা আবার পেস্ট করছি কনফিউশন এড়ানোর জন্য)

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
        logger.error(f"Error processing for user {user_id}: {e}", exc_info=True)
        bot.send_message(chat_id, f"A critical error occurred. Press /start to try again.")
    finally:
        delete_user_data(user_id)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

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

# --- Flask Web App and Bot Setup ---
init_db() # অ্যাপ শুরু হওয়ার সময় ডেটাবেস ফাইল তৈরি করা

app = Flask(__name__)
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start_command))
bot_app.add_handler(CommandHandler("cancel", cancel_command))
bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

async def setup_webhook():
    if WEBHOOK_URL: await bot_app.bot.set_webhook(url=WEBHOOK_URL)
    logger.info(f"Webhook has been set to {WEBHOOK_URL}")
asyncio.run(setup_webhook())

@app.route('/webhook', methods=['POST'])
async def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    await bot_app.process_update(update)
    return 'ok', 200
