from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

# Configuration for the bot
API_ID = int(os.environ.get("API_ID", 123456))  # Replace with your API ID
API_HASH = os.environ.get("API_HASH", "your_api_hash")  # Replace with your API HASH
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")  # Replace with your Bot Token

bot = Client("movie_ad_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_sessions = {}

@bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.reply("👋 স্বাগতম! \n\n👉 `/moviewithad` কমান্ড দিয়ে মুভি ও বিজ্ঞাপন যুক্ত করুন।")

@bot.on_message(filters.command("moviewithad") & filters.private)
async def add_movie(client, message):
    user_sessions[message.from_user.id] = {"step": "wait_movie"}
    await message.reply("🎬 এখন মুভি ভিডিও পাঠান (reply না করে)।")

@bot.on_message(filters.private & filters.video)
async def handle_video(client, message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)

    if not session:
        return

    # Step: Upload movie
    if session.get("step") == "wait_movie":
        session["movie_video"] = message.video.file_id
        session["step"] = "choose_ads"
        await message.reply(
            "📢 কতটি বিজ্ঞাপন দিতে চান?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("1 টি Ad", callback_data="ad_1")],
                [InlineKeyboardButton("2 টি Ad", callback_data="ad_2")],
                [InlineKeyboardButton("3 টি Ad", callback_data="ad_3")]
            ])
        )

    # Step: Upload ads
    elif session.get("step") == "wait_ads":
        ads = session.setdefault("ads", [])
        ads.append(message.video.file_id)
        if len(ads) == session["ad_count"]:
            await message.reply("✅ সব কিছু রেডি! মুভি + বিজ্ঞাপন পোস্ট করছি...")
            await post_movie_with_ads(client, message, session)
            del user_sessions[user_id]
        else:
            await message.reply(f"আরও {session['ad_count'] - len(ads)}টি Ad দিন।")

@bot.on_callback_query(filters.regex("ad_"))
async def handle_ad_choice(client, callback):
    user_id = callback.from_user.id
    session = user_sessions.get(user_id)

    if not session:
        return

    ad_count = int(callback.data.split("_")[1])
    session["ad_count"] = ad_count
    session["step"] = "wait_ads"
    session["ads"] = []

    await callback.message.edit_text(f"✅ {ad_count}টি বিজ্ঞাপন নির্বাচন করেছেন। এখন একে একে ভিডিও পাঠান।")

async def post_movie_with_ads(client, message, session):
    await message.reply_video(session["movie_video"], caption="🎬 মুভি শুরু")

    for idx, ad_id in enumerate(session["ads"], 1):
        await message.reply_video(ad_id, caption=f"📢 স্পন্সর বিজ্ঞাপন {idx}")

bot.run()
