import os
import logging
import subprocess
import threading
import shutil
import asyncio
import json # Redis-এ ডেটা রাখার জন্য json ব্যবহার করব

from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import redis # pymongo-এর পরিবর্তে redis
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_URL = os.getenv("REDIS_URL") # MONGO_URI-এর পরিবর্তে REDIS_URL
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Connection (Redis) ---
try:
    if not REDIS_URL:
        raise ValueError("Missing REDIS_URL. Please check your environment variables.")
    # Redis-এর সাথে কানেক্ট করা
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping() # কানেকশন ঠিক আছে কিনা চেক করা
    logger.info("Redis successfully connected.")
except Exception as e:
    logger.error(f"Failed to connect to Redis: {e}")
    redis_client = None

# --- User States ---
STATE_AWAITING_MOVIE = 'awaiting_movie'
STATE_AWAITING_AD = 'awaiting_ad'
STATE_AWAITING_AD_COUNT = 'awaiting_ad_count'

# --- Database Helper Functions (Using Redis) ---
def set_user_data(user_id, state=None, data=None):
    if not redis_client: return
    # প্রথমে ব্যবহারকারীর সব ডেটা পড়া
    user_key = f"user:{user_id}"
    try:
        current_data_str = redis_client.get(user_key)
        current_data = json.loads(current_data_str) if current_data_str else {}
    except (json.JSONDecodeError, TypeError):
        current_data = {}

    # নতুন ডেটা আপডেট করা
    if state is not None:
        current_data['state'] = state
    if data:
        current_data.update(data)
    
    # ডেটাবেসে আবার সেভ করা
    redis_client.set(user_key, json.dumps(current_data))

def get_user_data(user_id):
    if not redis_client: return {}
    user_key = f"user:{user_id}"
    data_str = redis_client.get(user_key)
    if data_str:
        return json.loads(data_str)
    return {}

# --- Video Processing, Telegram Handlers, Flask App (এগুলোতে কোনো পরিবর্তন নেই) ---
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
        if redis_client:
            redis_client.delete(f"user:{user_id}")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    set_user_data(user.id, state=STATE_AWAITING_MOVIE)
    await update.message.reply_html(f"👋 Hello {user.mention_html()}!\n\nFirst, send me the main **movie file**.")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if redis_client:
        redis_client.delete(f"user:{update.effective_user.id}")
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
