import os
import logging
import sqlite3
import time
import hashlib
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ==============================
# LOAD CONFIG
# ==============================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ==============================
# LOGGING
# ==============================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==============================
# DATABASE SETUP
# ==============================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    group_id INTEGER,
    user_id INTEGER,
    username TEXT,
    messages INTEGER DEFAULT 0,
    points INTEGER DEFAULT 0,
    last_msg_time REAL,
    last_msg_hash TEXT,
    PRIMARY KEY (group_id, user_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS groups (
    group_id INTEGER PRIMARY KEY,
    title TEXT
)
""")

conn.commit()

# ==============================
# CONFIG
# ==============================
POINTS_PER_MESSAGE = 4
COOLDOWN_SECONDS = 5

# ==============================
# UTIL FUNCTIONS
# ==============================
def hash_text(text: str):
    return hashlib.md5(text.encode()).hexdigest()

def is_valid_message(update: Update):
    msg = update.message

    if not msg or not msg.text:
        return False

    # Ignore bots
    if msg.from_user.is_bot:
        return False

    # Ignore forwards
    if msg.forward_date:
        return False

    text = msg.text.strip()

    # Ignore very short messages
    if len(text) < 2:
        return False

    # Ignore link-only spam
    if text.startswith("http"):
        return False

    return True

def update_user(group_id, user_id, username, text):
    now = time.time()
    text_hash = hash_text(text)

    cursor.execute("""
    SELECT messages, points, last_msg_time, last_msg_hash
    FROM users
    WHERE group_id=? AND user_id=?
    """, (group_id, user_id))

    row = cursor.fetchone()

    if row:
        messages, points, last_time, last_hash = row

        # Cooldown check
        if last_time and now - last_time < COOLDOWN_SECONDS:
            return

        # Duplicate check
        if last_hash == text_hash:
            return

        messages += 1
        points += POINTS_PER_MESSAGE

        cursor.execute("""
        UPDATE users
        SET messages=?, points=?, last_msg_time=?, last_msg_hash=?, username=?
        WHERE group_id=? AND user_id=?
        """, (messages, points, now, text_hash, username, group_id, user_id))

    else:
        cursor.execute("""
        INSERT INTO users (group_id, user_id, username, messages, points, last_msg_time, last_msg_hash)
        VALUES (?, ?, ?, 1, ?, ?, ?)
        """, (group_id, user_id, username, POINTS_PER_MESSAGE, now, text_hash))

    conn.commit()

# ==============================
# COMMAND: START (PRIVATE)
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return

    bot_username = context.bot.username
    add_link = f"https://t.me/{bot_username}?startgroup=true"

    text = (
        "👋 *Welcome to the Engagement Bot!*\n\n"
        "Boost your group activity and track member participation easily.\n\n"
        "💬 Earn *4 points* for each valid message\n"
        "🏆 View group leaderboard\n"
        "📊 Check your personal ranking\n\n"
        "⚠️ *Note:*\n"
        "- Make sure the bot has proper permissions\n"
        "- Works best when privacy mode is disabled\n\n"
        "👇 Add the bot to your group to get started!"
    )

    keyboard = [
        [InlineKeyboardButton("➕ Add Me To Your Group", url=add_link)]
    ]

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==============================
# MESSAGE HANDLER (GROUP)
# ==============================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type not in ["group", "supergroup"]:
        return

    if not is_valid_message(update):
        return

    group_id = update.message.chat.id
    group_title = update.message.chat.title
    user = update.message.from_user

    username = user.full_name

    # Save group
    cursor.execute("""
    INSERT OR IGNORE INTO groups (group_id, title)
    VALUES (?, ?)
    """, (group_id, group_title))

    update_user(group_id, user.id, username, update.message.text)

# ==============================
# LEADERBOARD
# ==============================
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type not in ["group", "supergroup"]:
        return

    group_id = update.message.chat.id

    cursor.execute("""
    SELECT username, points
    FROM users
    WHERE group_id=?
    ORDER BY points DESC
    LIMIT 15
    """, (group_id,))

    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("No data yet.")
        return

    text = "🏆 *Group Leaderboard*\n\n"

    medals = ["🥇", "🥈", "🥉"]

    for i, (username, points) in enumerate(rows, start=1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        text += f"{medal} {username} — *{points} pts*\n"

    keyboard = [
        [InlineKeyboardButton("📊 Find My Place", callback_data=f"rank_{group_id}")]
    ]

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==============================
# CALLBACK: FIND MY PLACE
# ==============================
async def find_my_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    group_id = int(query.data.split("_")[1])

    cursor.execute("""
    SELECT user_id, username, points, messages
    FROM users
    WHERE group_id=?
    ORDER BY points DESC
    """, (group_id,))

    rows = cursor.fetchall()

    rank = None
    user_data = None

    for i, row in enumerate(rows, start=1):
        if row[0] == user_id:
            rank = i
            user_data = row
            break

    if not user_data:
        await query.answer("You have no stats yet.", show_alert=True)
        return

    _, username, points, messages = user_data

    text = (
        f"📊 *Your Stats*\n\n"
        f"👤 {username}\n"
        f"🏅 Rank: {rank}\n"
        f"💬 Messages: {messages}\n"
        f"⭐ Points: {points}"
    )

    await query.answer(text, show_alert=True)

# ==============================
# MAIN
# ==============================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CallbackQueryHandler(find_my_place, pattern="^rank_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
