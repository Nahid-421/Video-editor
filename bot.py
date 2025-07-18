import os
import logging
import subprocess
import math
import threading

from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from dotenv import load_dotenv

# .env ফাইল থেকে এনভায়রনমেন্ট ভেরিয়েবল লোড করা
load_dotenv()

# বেসিক কনফিগারেশন
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # আপনার Render/Koyeb URL

# লগিং সেটআপ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# MongoDB ক্লায়েন্ট
client = MongoClient(MONGO_URI)
db = client['video_bot_db']
users_collection = db['users']

# ব্যবহারকারীর বিভিন্ন অবস্থা (States)
STATE_AWAITING_MOVIE = 'awaiting_movie'
STATE_AWAITING_AD = 'awaiting_ad'
STATE_AWAITING_AD_COUNT = 'awaiting_ad_count'
STATE_PROCESSING = 'processing'

# Flask অ্যাপ ইনিশিয়ালাইজ করা
app = Flask(__name__)

# Helper Functions for Database
def get_user_state(user_id):
    user_data = users_collection.find_one({'user_id': user_id})
    return user_data.get('state') if user_data else None

def set_user_state(user_id, state, data=None):
    update_data = {'state': state}
    if data:
        update_data.update(data)
    users_collection.update_one({'user_id': user_id}, {'$set': update_data}, upsert=True)

# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    set_user_state(user.id, STATE_AWAITING_MOVIE)
    await update.message.reply_html(
        f"👋 হ্যালো {user.mention_html()}!\n\nআমি একটি ভিডিও এডিটিং বট। প্রথমে আমাকে আপনার মুভি ফাইলটি পাঠান।",
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_user_state(user_id, None) # স্টেট রিসেট করা
    await update.message.reply_text("প্রক্রিয়া বাতিল করা হয়েছে। নতুন করে শুরু করতে /start চাপুন।")

# Message Handler (মূল লজিক এখানে)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    bot = context.bot

    if state == STATE_AWAITING_MOVIE:
        if update.message.video:
            file_id = update.message.video.file_id
            set_user_state(user_id, STATE_AWAITING_AD, {'movie_file_id': file_id})
            await update.message.reply_text("মুভি পেয়েছি। ✅\n\nএবার বিজ্ঞাপনের ভিডিও ফাইলটি পাঠান।")
        else:
            await update.message.reply_text("দয়া করে একটি ভিডিও ফাইল পাঠান।")

    elif state == STATE_AWAITING_AD:
        if update.message.video:
            file_id = update.message.video.file_id
            set_user_state(user_id, STATE_AWAITING_AD_COUNT, {'ad_file_id': file_id})
            await update.message.reply_text("বিজ্ঞাপন পেয়েছি। ✅\n\nএখন বলুন, মুভির মধ্যে মোট কতবার বিজ্ঞাপনটি বসাতে চান? (শুধু সংখ্যা লিখুন, যেমন: 2 বা 3)")
        else:
            await update.message.reply_text("দয়া করে একটি বিজ্ঞাপনের ভিডিও ফাইল পাঠান।")

    elif state == STATE_AWAITING_AD_COUNT:
        if update.message.text and update.message.text.isdigit():
            count = int(update.message.text)
            if count <= 0:
                await update.message.reply_text("অনুগ্রহ করে 0-এর চেয়ে বড় একটি সংখ্যা দিন।")
                return

            set_user_state(user_id, STATE_PROCESSING, {'ad_count': count})
            await update.message.reply_text(f"ধন্যবাদ। আপনার মুভিতে {count} বার বিজ্ঞাপন যুক্ত করার কাজ শুরু হচ্ছে। এটি শেষ হতে অনেক সময় লাগতে পারে। কাজ শেষ হলে আমি আপনাকে ফাইলটি পাঠিয়ে দেব।")

            # মূল ভিডিও প্রসেসিং ফাংশনকে একটি আলাদা থ্রেডে চালানো হচ্ছে
            # যাতে ওয়েবহুক ব্লক না হয়ে যায়
            threading.Thread(target=process_video, args=(bot, user_id)).start()
        else:
            await update.message.reply_text("দয়া করে শুধু একটি সংখ্যা লিখুন।")
            
# Video Processing Function (The Magic Happens Here)
def process_video(bot, user_id):
    try:
        user_data = users_collection.find_one({'user_id': user_id})
        movie_file_id = user_data['movie_file_id']
        ad_file_id = user_data['ad_file_id']
        ad_count = user_data['ad_count']
        chat_id = user_id # user_id is the chat_id for private chats

        # একটি ইউনিক ফোল্ডার তৈরি করা
        temp_dir = f"temp_{user_id}"
        os.makedirs(temp_dir, exist_ok=True)
        
        bot.send_message(chat_id, "ফাইল ডাউনলোড শুরু হচ্ছে...")

        # ফাইল ডাউনলোড
        movie_file_obj = bot.get_file(movie_file_id)
        movie_path = os.path.join(temp_dir, 'movie.mp4')
        movie_file_obj.download_to_drive(movie_path)

        ad_file_obj = bot.get_file(ad_file_id)
        ad_path = os.path.join(temp_dir, 'ad.mp4')
        ad_file_obj.download_to_drive(ad_path)
        
        bot.send_message(chat_id, "ডাউনলোড সম্পন্ন। ভিডিও প্রসেসিং শুরু হচ্ছে...")

        # FFmpeg দিয়ে মুভির দৈর্ঘ্য (duration) বের করা
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', movie_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        duration = float(result.stdout)

        # মুভিকে সমান অংশে ভাগ করা
        num_splits = ad_count + 1
        split_duration = duration / num_splits
        split_points = [i * split_duration for i in range(1, num_splits)]
        
        # FFmpeg এর জন্য কনক্যাটেনেশন লিস্ট ফাইল তৈরি
        concat_list_path = os.path.join(temp_dir, 'concat_list.txt')
        with open(concat_list_path, 'w') as f:
            start_time = 0
            for i, end_time in enumerate(split_points):
                part_path = os.path.join(temp_dir, f'part_{i}.mp4')
                subprocess.run(['ffmpeg', '-i', movie_path, '-ss', str(start_time), '-to', str(end_time), '-c', 'copy', part_path], check=True)
                f.write(f"file '{os.path.basename(part_path)}'\n")
                f.write(f"file '{os.path.basename(ad_path)}'\n")
                start_time = end_time
            
            # শেষ অংশ যুক্ত করা
            last_part_path = os.path.join(temp_dir, f'part_{num_splits-1}.mp4')
            subprocess.run(['ffmpeg', '-i', movie_path, '-ss', str(start_time), '-c', 'copy', last_part_path], check=True)
            f.write(f"file '{os.path.basename(last_part_path)}'\n")

        # ফাইলগুলোকে জোড়া লাগানো
        output_path = os.path.join(temp_dir, 'final_movie.mp4')
        bot.send_message(chat_id, "ফাইলগুলো জোড়া লাগানো হচ্ছে... এটি সবচেয়ে সময়সাপেক্ষ ধাপ।")
        subprocess.run(
            ['ffmpeg', '-f', 'concat', '-safe', '0', '-i', concat_list_path, '-c', 'copy', output_path],
            check=True
        )

        # ফাইনাল ফাইল পাঠানো
        bot.send_message(chat_id, "প্রসেসিং সম্পন্ন! ✅\nফাইলটি আপলোড করা হচ্ছে...")
        with open(output_path, 'rb') as final_video:
            bot.send_video(chat_id, video=final_video, caption="আপনার এডিট করা মুভি।", timeout=120)

    except Exception as e:
        logger.error(f"Error processing for user {user_id}: {e}")
        bot.send_message(chat_id, f"একটি সমস্যা হয়েছে: {e}\nঅনুগ্রহ করে আবার চেষ্টা করুন /start দিয়ে।")
    finally:
        # ব্যবহারকারীর স্টেট রিসেট করা
        set_user_state(user_id, None)
        # অস্থায়ী ফাইল ও ফোল্ডার ডিলিট করা
        if 'temp_dir' in locals() and os.path.exists(temp_dir):
            for file in os.listdir(temp_dir):
                os.remove(os.path.join(temp_dir, file))
            os.rmdir(temp_dir)

# Webhook route for Flask
@app.route('/webhook', methods=['POST'])
def webhook():
    json_data = request.get_json()
    update = Update.de_json(json_data, bot_application.bot)
    bot_application.update_queue.put(update)
    return "OK", 200

def main():
    global bot_application
    bot_application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register handlers
    bot_application.add_handler(CommandHandler("start", start))
    bot_application.add_handler(CommandHandler("cancel", cancel))
    bot_application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    
    # Set webhook
    bot_application.bot.set_webhook(url=WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")

if __name__ == "__main__":
    main()
    # Flask অ্যাপটি একটি প্রোডাকশন-গ্রেড সার্ভারে চালানো উচিত, যেমন gunicorn
    # উদাহরণ: gunicorn --bind 0.0.0.0:8000 bot:app
    # Render/Koyeb এটি স্বয়ংক্রিয়ভাবে করবে।
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
