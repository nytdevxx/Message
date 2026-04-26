"""
YouTube Downloader Telegram Bot
Created By | SaaFe 🖤
"""

import os
import re
import time
import logging
import asyncio
from pathlib import Path
from collections import defaultdict

import yt_dlp
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

import database as db

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

CAPTION = "𝘾𝙧𝙚𝙖𝙩𝙚𝙙 𝘽𝙮 | 𝙎𝙖𝙖𝙁𝙚 🖤"
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Rate limiting: max downloads per user within a time window
RATE_LIMIT_MAX = 3          # max downloads
RATE_LIMIT_WINDOW = 60      # seconds
_user_download_times: dict[int, list[float]] = defaultdict(list)

# Awaiting-link state: maps user_id → download type
_awaiting_link: dict[int, str] = {}

# YouTube URL pattern
YT_REGEX = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?.*v=|shorts/|embed/)|youtu\.be/)[\w\-]+"
)

TELEGRAM_MAX_BYTES = 50 * 1024 * 1024   # 50 MB for bots (audio/video via API)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔘 YouTube", callback_data="youtube"),
            InlineKeyboardButton("Admin 🎫",   callback_data="admin"),
        ]
    ])


def download_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Video 📷",      callback_data="dl_video"),
            InlineKeyboardButton("Audio / mp3",   callback_data="dl_audio"),
            InlineKeyboardButton("Thumbnail",      callback_data="dl_thumb"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")]
    ])


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")],
    ])


def is_valid_youtube_url(url: str) -> bool:
    return bool(YT_REGEX.search(url))


def is_rate_limited(user_id: int) -> bool:
    """Return True if user has exceeded the download rate limit."""
    now = time.time()
    times = _user_download_times[user_id]
    # Purge old entries outside the window
    _user_download_times[user_id] = [t for t in times if now - t < RATE_LIMIT_WINDOW]
    return len(_user_download_times[user_id]) >= RATE_LIMIT_MAX


def record_download_time(user_id: int):
    _user_download_times[user_id].append(time.time())


def cleanup_file(path: Path):
    try:
        if path.exists():
            path.unlink()
    except OSError as e:
        logger.warning(f"Could not delete {path}: {e}")


# ──────────────────────────────────────────────
# Command Handlers
# ──────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username, user.first_name)

    text = (
        f"👋 Welcome, <b>{user.first_name}</b>!\n\n"
        "I'm your professional <b>YouTube Downloader Bot</b>.\n\n"
        "📥 <b>What I can do:</b>\n"
        "  • Download YouTube videos (up to 1080p)\n"
        "  • Extract audio as MP3\n"
        "  • Grab high-resolution thumbnails\n\n"
        "Choose an option below to get started 👇"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard()
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.add_user(
        update.effective_user.id,
        update.effective_user.username,
        update.effective_user.first_name,
    )
    text = (
        "📖 <b>How to use this bot:</b>\n\n"
        "1️⃣ Tap <b>🔘 YouTube</b>\n"
        "2️⃣ Choose <b>Video</b>, <b>Audio / mp3</b>, or <b>Thumbnail</b>\n"
        "3️⃣ Send a YouTube link\n"
        "4️⃣ Receive your file instantly!\n\n"
        "<b>Supported links:</b>\n"
        "  • youtube.com/watch?v=…\n"
        "  • youtu.be/…\n"
        "  • YouTube Shorts (youtube.com/shorts/…)\n\n"
        f"<b>Rate limit:</b> {RATE_LIMIT_MAX} downloads per {RATE_LIMIT_WINDOW}s\n\n"
        "Created By | SaaFe 🖤"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard()
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only shortcut for statistics."""
    user = update.effective_user
    db.add_user(user.id, user.username, user.first_name)

    if not db.is_admin(user.id):
        await update.message.reply_text(
            "🔒 This command is restricted to admins.",
            reply_markup=back_keyboard(),
        )
        return

    text = build_stats_text()
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard()
    )


def build_stats_text() -> str:
    return (
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total users:          <b>{db.get_total_users()}</b>\n"
        f"📥 Total downloads:      <b>{db.get_total_downloads()}</b>\n"
        f"📆 Downloads today:      <b>{db.get_today_downloads()}</b>\n\n"
        "Created By | SaaFe 🖤"
    )


# ──────────────────────────────────────────────
# Callback Query Handlers
# ──────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user  = query.from_user
    data  = query.data

    await query.answer()
    db.add_user(user.id, user.username, user.first_name)

    # ── Main menu ──────────────────────────────
    if data == "back_main":
        _awaiting_link.pop(user.id, None)
        await query.edit_message_text(
            "🏠 <b>Main Menu</b>\n\nChoose an option below:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )

    # ── YouTube section ─────────────────────────
    elif data == "youtube":
        await query.edit_message_text(
            "🎬 <b>Select download type:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=download_type_keyboard(),
        )

    # ── Download type selection ─────────────────
    elif data in ("dl_video", "dl_audio", "dl_thumb"):
        labels = {
            "dl_video": ("🎬 Video", "📷 Send the YouTube video link:"),
            "dl_audio": ("🎵 Audio", "🎵 Send the YouTube video link to extract audio:"),
            "dl_thumb": ("🖼️ Thumbnail", "🖼️ Send the YouTube video link to get its thumbnail:"),
        }
        _, prompt = labels[data]
        _awaiting_link[user.id] = data
        await query.edit_message_text(
            prompt,
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )

    # ── Admin panel ─────────────────────────────
    elif data == "admin":
        if db.is_admin(user.id):
            await query.edit_message_text(
                "🎫 <b>Admin Panel</b>\n\nWelcome back, admin!",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_panel_keyboard(),
            )
        else:
            await query.edit_message_text(
                "🔒 <b>Restricted area.</b>\n\nThis section is for admins only.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_keyboard(),
            )

    # ── Admin statistics ────────────────────────
    elif data == "admin_stats":
        if db.is_admin(user.id):
            await query.edit_message_text(
                build_stats_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_panel_keyboard(),
            )
        else:
            await query.edit_message_text(
                "🔒 <b>Restricted area.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=back_keyboard(),
            )


# ──────────────────────────────────────────────
# Message Handler (link processing)
# ──────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    message = update.message
    text    = message.text.strip() if message.text else ""

    db.add_user(user.id, user.username, user.first_name)

    # Not awaiting a link from this user
    if user.id not in _awaiting_link:
        await message.reply_text(
            "👇 Use the menu to get started:",
            reply_markup=main_menu_keyboard(),
        )
        return

    dl_type = _awaiting_link[user.id]

    # Validate URL
    if not is_valid_youtube_url(text):
        await message.reply_text(
            "❌ <b>Invalid YouTube URL.</b>\n\nPlease send a valid YouTube link.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    # Rate limiting
    if is_rate_limited(user.id):
        await message.reply_text(
            f"⏳ <b>Slow down!</b>\n\nYou can make {RATE_LIMIT_MAX} downloads "
            f"every {RATE_LIMIT_WINDOW} seconds. Try again shortly.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    # Remove from awaiting state immediately
    _awaiting_link.pop(user.id, None)
    record_download_time(user.id)

    # Show processing message
    status_msg = await message.reply_text("⏳ Processing your request… Please wait.")

    try:
        if dl_type == "dl_video":
            await process_video(update, context, text, status_msg)
        elif dl_type == "dl_audio":
            await process_audio(update, context, text, status_msg)
        elif dl_type == "dl_thumb":
            await process_thumbnail(update, context, text, status_msg)

        db.log_download(user.id, user.username, text, dl_type)

    except FileTooLargeError:
        await status_msg.edit_text(
            "⚠️ <b>File too large for Telegram.</b>\n\n"
            "This video exceeds the upload limit. Try a shorter video.",
            parse_mode=ParseMode.HTML,
        )
        await message.reply_text("👇 Back to menu:", reply_markup=main_menu_keyboard())

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp DownloadError for {user.id}: {e}")
        await status_msg.edit_text(
            "❌ <b>Download failed.</b>\n\n"
            "The video may be private, age-restricted, or unavailable.",
            parse_mode=ParseMode.HTML,
        )
        await message.reply_text("👇 Back to menu:", reply_markup=main_menu_keyboard())

    except TelegramError as e:
        logger.error(f"TelegramError for {user.id}: {e}")
        await status_msg.edit_text(
            "❌ <b>Failed to send the file.</b>\n\nIt may be too large for Telegram.",
            parse_mode=ParseMode.HTML,
        )
        await message.reply_text("👇 Back to menu:", reply_markup=main_menu_keyboard())

    except Exception as e:
        logger.exception(f"Unexpected error for user {user.id}: {e}")
        await status_msg.edit_text(
            "❌ <b>Something went wrong.</b>\n\nPlease try again later.",
            parse_mode=ParseMode.HTML,
        )
        await message.reply_text("👇 Back to menu:", reply_markup=main_menu_keyboard())


# ──────────────────────────────────────────────
# Download Functions
# ──────────────────────────────────────────────

class FileTooLargeError(Exception):
    pass


async def process_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    status_msg,
):
    """Download and send a video file."""
    await status_msg.edit_text("⏳ Downloading video… (this may take a moment)")

    output_template = str(DOWNLOAD_DIR / "%(id)s_video.%(ext)s")

    # Try 1080p first, fall back to 720p
    for height in (1080, 720):
        ydl_opts = {
            "format": f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]"
                      f"/bestvideo[height<={height}]+bestaudio/best[height<={height}]",
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = Path(ydl.prepare_filename(info)).with_suffix(".mp4")

        if filepath.exists():
            size = filepath.stat().st_size
            if size <= TELEGRAM_MAX_BYTES:
                break
            cleanup_file(filepath)
            if height == 720:
                raise FileTooLargeError

    if not filepath.exists():
        raise FileTooLargeError

    await status_msg.edit_text("📤 Uploading video…")
    with open(filepath, "rb") as f:
        await update.message.reply_video(
            video=f,
            caption=CAPTION,
            supports_streaming=True,
        )
    cleanup_file(filepath)

    await update.message.reply_text(
        "✅ <b>Video sent!</b>\n\n👇 What's next?",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


async def process_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    status_msg,
):
    """Download audio and send as MP3."""
    await status_msg.edit_text("⏳ Extracting audio…")

    output_template = str(DOWNLOAD_DIR / "%(id)s_audio.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info     = ydl.extract_info(url, download=True)
        raw_path = Path(ydl.prepare_filename(info))
        filepath = raw_path.with_suffix(".mp3")

    if not filepath.exists():
        raise FileNotFoundError(f"MP3 not found at {filepath}")

    if filepath.stat().st_size > TELEGRAM_MAX_BYTES:
        cleanup_file(filepath)
        raise FileTooLargeError

    title    = info.get("title", "audio")
    duration = info.get("duration", 0)

    await status_msg.edit_text("📤 Uploading audio…")
    with open(filepath, "rb") as f:
        await update.message.reply_audio(
            audio=f,
            caption=CAPTION,
            title=title,
            duration=duration,
        )
    cleanup_file(filepath)

    await update.message.reply_text(
        "✅ <b>Audio sent!</b>\n\n👇 What's next?",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


async def process_thumbnail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    status_msg,
):
    """Fetch and send the highest-quality thumbnail."""
    import urllib.request

    await status_msg.edit_text("⏳ Fetching thumbnail…")

    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    video_id = info.get("id", "")
    title    = info.get("title", "thumbnail")

    # Try resolutions from best to fallback
    thumb_urls = [
        f"[img.youtube.com](https://img.youtube.com/vi/{video_id}/maxresdefault.jpg)",
        f"[img.youtube.com](https://img.youtube.com/vi/{video_id}/sddefault.jpg)",
        f"[img.youtube.com](https://img.youtube.com/vi/{video_id}/hqdefault.jpg)",
    ]

    thumb_path = DOWNLOAD_DIR / f"{video_id}_thumb.jpg"
    downloaded = False

    for thumb_url in thumb_urls:
        try:
            urllib.request.urlretrieve(thumb_url, thumb_path)
            # YouTube returns a placeholder (120×90) for missing resolutions
            if thumb_path.stat().st_size > 5000:
                downloaded = True
                break
            thumb_path.unlink(missing_ok=True)
        except Exception:
            continue

    if not downloaded:
        raise Exception("Could not fetch a valid thumbnail.")

    await status_msg.edit_text("📤 Sending thumbnail…")
    with open(thumb_path, "rb") as f:
        await update.message.reply_photo(
            photo=f,
            caption=CAPTION,
        )
    cleanup_file(thumb_path)

    await update.message.reply_text(
        "✅ <b>Thumbnail sent!</b>\n\n👇 What's next?",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set in the .env file.")

    db.initialize_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Text messages (link input)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is running… (polling)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
