import os
import logging
import subprocess
import threading

from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from dotenv import load_dotenv

# .env ফাইল থেকে এনভায়রনমেন্ট ভেরিয়েবল লোড করা (লোকালে টেস্টের জন্য)
load_dotenv()

# --- কনফিগারেশন ---
# এই মানগুলো Render-এর Environment Variables থেকে আসবে
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # যেমন: https://your-bot-name.onrender.com/webhook

# লগিং সেটআপ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ডেটাবেস এবং স্টেট ম্যানেজমেন্ট ---
try:
    client = MongoClient(MONGO_URI)
    db = client.get_default_database() # কানেকশন স্ট্রিং থেকে ডেটাবেস নাম নেয়
    users_collection = db['users']
    logger.info("MongoDB successfully connected.")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    client = None # কানেকশন ব্যর্থ হলে ক্লায়েন্টকে None করে দিন

# ব্যবহারকারীর বিভিন্ন অবস্থা (States)
STATE_AWAITING_MOVIE = 'awaiting_movie'
STATE_AWAITING_AD = 'awaiting_ad'
STATE_AWAITING_AD_COUNT = 'awaiting_ad_count'

# --- Helper Functions for Database ---
def set_user_data(user_id, state=None, data=None):
    if not client: return
    query = {'user_id': user_id}
    update = {'$set': {}}
    if state:
        update['$set']['state'] = state
    if data:
        for key, value in data.items():
            update['$set'][key] = value
    
    if not update['$set']: return
    users_collection.update_one(query, update, upsert=True)

def get_user_data(user_id):
    if not client: return {}
    return users_collection.find_one({'user_id': user_id}) or {}

# --- ভিডিও প্রসেসিং ফাংশন (আলাদা থ্রেডে চলবে) ---
def process_video(user_id, chat_id, context):
    bot = context.bot
    temp_dir = f"temp_{user_id}"
    
    try:
        user_data = get_user_data(user_id)
        movie_file_id = user_data.get('movie_file_id')
        ad_file_id = user_data.get('ad_file_id')
        ad_count = user_data.get('ad_count')

        if not all([movie_file_id, ad_file_id, ad_count]):
            raise ValueError("প্রয়োজনীয় তথ্য ডেটাবেসে পাওয়া যায়নি।")

        # ১. অস্থায়ী ফোল্ডার তৈরি ও ফাইল ডাউনলোড
        os.makedirs(temp_dir, exist_ok=True)
        bot.send_message(chat_id, "ফাইল ডাউনলোড শুরু হচ্ছে... 📥")
        
        movie_path = os.path.join(temp_dir, 'movie.mp4')
        (bot.get_file(movie_file_id)).download_to_drive(movie_path)

        ad_path = os.path.join(temp_dir, 'ad.mp4')
        (bot.get_file(ad_file_id)).download_to_drive(ad_path)
        
        bot.send_message(chat_id, "ডাউনলোড সম্পন্ন। ভিডিও প্রসেসিং শুরু হচ্ছে... ⚙️")

        # ২. মুভির মোট দৈর্ঘ্য বের করা
        ffprobe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', movie_path]
        result = subprocess.run(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        duration = float(result.stdout)

        # ৩. মুভিকে সমান অংশে ভাগ করে বিজ্ঞাপনের সাথে জোড়া লাগানোর জন্য লিস্ট তৈরি
        num_splits = ad_count + 1
        split_duration = duration / num_splits
        
        concat_list_path = os.path.join(temp_dir, 'concat_list.txt')
        with open(concat_list_path, 'w') as f:
            for i in range(ad_count):
                f.write(f"file '{os.path.basename(movie_path)}'\n")
                f.write(f"inpoint {i * split_duration}\n")
                f.write(f"outpoint {(i * split_duration) + split_duration}\n")
                f.write(f"file '{os.path.basename(ad_path)}'\n")
            
            # শেষ অংশ যোগ করা
            f.write(f"file '{os.path.basename(movie_path)}'\n")
            f.write(f"inpoint {ad_count * split_duration}\n")

        # ৪. FFmpeg দিয়ে ফাইলগুলোকে জোড়া লাগানো
        output_path = os.path.join(temp_dir, 'final_movie.mp4')
        bot.send_message(chat_id, "ফাইলগুলো জোড়া লাগানো হচ্ছে... এটি সবচেয়ে সময়সাপেক্ষ ধাপ। অনুগ্রহ করে ধৈর্য ধরুন।")
        
        ffmpeg_cmd = ['ffmpeg', '-f', 'concat', '-safe', '0', '-i', concat_list_path, '-c', 'copy', output_path]
        subprocess.run(ffmpeg_cmd, check=True)

        # ৫. ফাইনাল ফাইল পাঠানো
        bot.send_message(chat_id, "প্রসেসিং সম্পন্ন! ✅\nফাইলটি আপলোড করা হচ্ছে...")
        with open(output_path, 'rb') as final_video:
            bot.send_video(chat_id, video=final_video, caption="আপনার এডিট করা মুভি।", read_timeout=120, write_timeout=120)

    except Exception as e:
        logger.error(f"Error processing for user {user_id}: {e}", exc_info=True)
        bot.send_message(chat_id, f"একটি গুরুতর সমস্যা হয়েছে। সম্ভবত ফাইলটি অনেক বড় অথবা সার্ভার এটি প্রসেস করতে পারেনি।\n\nError: {e}\n\nআবার চেষ্টা করতে /start চাপুন।")
    finally:
        # ৬. ব্যবহারকারীর স্টেট রিসেট করা ও অস্থায়ী ফাইল ডিলিট করা
        set_user_data(user_id, state=None, data={'movie_file_id': None, 'ad_file_id': None, 'ad_count': None})
        if os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir)

# --- Telegram Command and Message Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    set_user_data(user.id, state=STATE_AWAITING_MOVIE)
    await update.message.reply_html(f"👋 হ্যালো {user.mention_html()}!\n\nআমি আপনার মুভিতে বিজ্ঞাপন যুক্ত করতে পারি।\n\nপ্রথমে আমাকে আপনার **মূল মুভি ফাইলটি** পাঠান।")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_user_data(update.effective_user.id, state=None)
    await update.message.reply_text("প্রক্রিয়া বাতিল করা হয়েছে। নতুন করে শুরু করতে /start চাপুন।")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_data = get_user_data(user_id)
    state = user_data.get('state')

    if state == STATE_AWAITING_MOVIE:
        if update.message.video:
            set_user_data(user_id, state=STATE_AWAITING_AD, data={'movie_file_id': update.message.video.file_id})
            await update.message.reply_text("মুভি পেয়েছি। ✅\n\nএবার **বিজ্ঞাপনের ভিডিও ফাইলটি** পাঠান।")
        else:
            await update.message.reply_text("❌ ভুল ইনপুট। দয়া করে একটি **ভিডিও ফাইল** পাঠান।")

    elif state == STATE_AWAITING_AD:
        if update.message.video:
            set_user_data(user_id, state=STATE_AWAITING_AD_COUNT, data={'ad_file_id': update.message.video.file_id})
            await update.message.reply_text("বিজ্ঞাপন পেয়েছি। ✅\n\nএখন বলুন, মুভির মধ্যে মোট **কতবার** বিজ্ঞাপনটি বসাতে চান? (শুধুমাত্র একটি সংখ্যা লিখুন, যেমন: 2 বা 3)")
        else:
            await update.message.reply_text("❌ ভুল ইনপুট। দয়া করে একটি **বিজ্ঞাপনের ভিডিও ফাইল** পাঠান।")

    elif state == STATE_AWAITING_AD_COUNT:
        if update.message.text and update.message.text.isdigit() and int(update.message.text) > 0:
            count = int(update.message.text)
            set_user_data(user_id, state='processing', data={'ad_count': count})
            await update.message.reply_text(f"তথ্য গ্রহণ সম্পন্ন। আপনার মুভিতে {count} বার বিজ্ঞাপন যুক্ত করার কাজ শুরু হচ্ছে। এটি শেষ হতে কয়েক মিনিট থেকে শুরু করে এক ঘণ্টা বা তার বেশিও সময় লাগতে পারে। কাজ শেষ হলে আমি আপনাকে ফাইলটি পাঠিয়ে দেব।")
            
            # মূল প্রসেসিং ফাংশনকে একটি আলাদা থ্রেডে চালানো হচ্ছে
            threading.Thread(target=process_video, args=(user_id, chat_id, context)).start()
        else:
            await update.message.reply_text("❌ ভুল ইনপুট। দয়া করে 0-এর চেয়ে বড় একটি **সংখ্যা** লিখুন (যেমন: 1, 2, 3)।")

# --- Flask Web App for Webhook ---
app = Flask(__name__)
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    bot_app.update_queue.put(update)
    return 'ok'

def main():
    # Register handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("cancel", cancel_command))
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    
    # Set webhook
    bot_app.bot.set_webhook(url=WEBHOOK_URL)
    logger.info(f"Webhook has been set to {WEBHOOK_URL}")

if __name__ == "__main__":
    main()
    # Gunicorn এই অ্যাপটি চালাবে, তাই নিচের app.run() প্রোডাকশনে ব্যবহৃত হবে না।
    # For local testing, you might run this file directly.
    # HOST = '0.0.0.0'
    # PORT = 8080
    # app.run(host=HOST, port=PORT)
