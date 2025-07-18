import os
import logging
import subprocess
import threading
import shutil
import asyncio

from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Connection ---
try:
    if not MONGO_URI or not (MONGO_URI.startswith("mongodb://") or MONGO_URI.startswith("mongodb+srv://")):
        raise ValueError("Invalid or missing MONGO_URI. Please check your environment variables.")
    client = MongoClient(MONGO_URI)
    db = client.get_default_database()
    users_collection = db['users']
    logger.info("MongoDB successfully connected.")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    client = None

# --- User States ---
STATE_AWAITING_MOVIE = 'awaiting_movie'
STATE_AWAITING_AD = 'awaiting_ad'
STATE_AWAITING_AD_COUNT = 'awaiting_ad_count'

# --- Database Helper Functions ---
def set_user_data(user_id, state=None, data=None):
    if not client: return
    query = {'user_id': user_id}
    update = {'$set': {}}
    if state is not None:
        update['$set']['state'] = state
    if data:
        for key, value in data.items():
            update['$set'][key] = value
    
    if not update['$set']: return
    users_collection.update_one(query, update, upsert=True)

def get_user_data(user_id):
    if not client: return {}
    return users_collection.find_one({'user_id': user_id}) or {}

# --- Video Processing Function ---
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
        bot.send_message(chat_id, f"A critical error occurred. The file might be too large or the server could not process it.\n\nError: {e}\n\nPress /start to try again.")
    finally:
        set_user_data(user_id, state=None, data={'movie_file_id': None, 'ad_file_id': None, 'ad_count': None})
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

# --- Telegram Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    set_user_data(user.id, state=STATE_AWAITING_MOVIE)
    await update.message.reply_html(f"👋 Hello {user.mention_html()}!\n\nI can add ads to your movies.\n\nFirst, send me the main **movie file**.")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_data(update.effective_user.id, state=None)
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
        else:
            await update.message.reply_text("❌ Invalid input. Please send a **video file**.")

    elif state == STATE_AWAITING_AD:
        if update.message.video:
            set_user_data(user_id, state=STATE_AWAITING_AD_COUNT, data={'ad_file_id': update.message.video.file_id})
            await update.message.reply_text("Ad received. ✅\n\nNow, tell me **how many times** you want to place the ad? (Send only a number, e.g., 2 or 3)")
        else:
            await update.message.reply_text("❌ Invalid input. Please send an **advertisement video file**.")

    elif state == STATE_AWAITING_AD_COUNT:
        if update.message.text and update.message.text.isdigit() and int(update.message.text) > 0:
            count = int(update.message.text)
            set_user_data(user_id, state='processing', data={'ad_count': count})
            await update.message.reply_text(f"Information received. Starting the process to add the ad {count} times. This may take from a few minutes to over an hour. I will send you the file when it's done.")
            
            threading.Thread(target=process_video, args=(user_id, chat_id, context)).start()
        else:
            await update.message.reply_text("❌ Invalid input. Please send a **number greater than 0** (e.g., 1, 2, 3).")

# --- Flask Web App and Bot Setup ---
app = Flask(__name__)
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

bot_app.add_handler(CommandHandler("start", start_command))
bot_app.add_handler(CommandHandler("cancel", cancel_command))
bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

async def setup_webhook():
    if WEBHOOK_URL:
        await bot_app.bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook has been set to {WEBHOOK_URL}")
    else:
        logger.warning("WEBHOOK_URL not set. Skipping webhook setup.")

asyncio.run(setup_webhook())

@app.route('/webhook', methods=['POST'])
async def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    await bot_app.process_update(update)
    return 'ok', 200
