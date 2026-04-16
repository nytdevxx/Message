# ============================================================
#  Telegram Downloader Bot
#  Author  : SaaFe
#  Purpose : Download YouTube & TikTok videos/audio via Telegram
#  Stack   : pyTelegramBotAPI + yt-dlp
# ============================================================

import os
import re
import logging
import tempfile
import traceback

import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import yt_dlp

# ─────────────────────────────────────────────
#  Logging — visible in Railway console
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  Bot initialisation
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise EnvironmentError("BOT_TOKEN environment variable is not set.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ─────────────────────────────────────────────
#  Per-user session state
#  { user_id: { "platform": str, "format": str, "step": str } }
# ─────────────────────────────────────────────
sessions: dict[int, dict] = {}

def get_session(uid: int) -> dict:
    if uid not in sessions:
        sessions[uid] = {"platform": None, "format": None, "step": "home"}
    return sessions[uid]

def reset_session(uid: int) -> dict:
    sessions[uid] = {"platform": None, "format": None, "step": "home"}
    return sessions[uid]

# ─────────────────────────────────────────────
#  URL validators
# ─────────────────────────────────────────────
YT_REGEX = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
    r"[\w\-]{11}",
    re.IGNORECASE,
)
TT_REGEX = re.compile(
    r"(https?://)?(www\.|vm\.|vt\.)?"
    r"tiktok\.com/",
    re.IGNORECASE,
)

def is_youtube_url(url: str) -> bool:
    return bool(YT_REGEX.search(url))

def is_tiktok_url(url: str) -> bool:
    return bool(TT_REGEX.search(url))

# ─────────────────────────────────────────────
#  Filename sanitizer
# ─────────────────────────────────────────────
def sanitize_filename(name: str, max_len: int = 60) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = name.strip().replace(" ", "_")
    return name[:max_len] if name else "media"

# ─────────────────────────────────────────────
#  Human-readable duration helper
# ─────────────────────────────────────────────
def fmt_duration(seconds) -> str:
    try:
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    except Exception:
        return "Unknown"

# ─────────────────────────────────────────────
#  Human-readable view count
# ─────────────────────────────────────────────
def fmt_views(views) -> str:
    try:
        return f"{int(views):,} views"
    except Exception:
        return "Unknown views"

# ─────────────────────────────────────────────
#  Caption builder
# ─────────────────────────────────────────────
def build_caption(title: str, views: str, url: str, duration: str, platform: str) -> str:
    icon   = "🎵" if platform == "youtube" else "🎬"
    label  = "Watch On YouTube" if platform == "youtube" else "Watch On TikTok"
    sep    = "━━━━━━━━━━━━━━━━━━━"
    credit = '𝘾𝙧𝙚𝙖𝙩𝙚𝙙 𝘽𝙮 | <a href="[t.me](https://t.me/Sefuax)">𝙎𝙖𝙖𝙁𝙚</a> 🖤'

    return (
        f"{icon} <b>Title :</b> {title}\n"
        f"{sep}\n"
        f"👁️‍🗨️ <b>Views :</b> {views}\n"
        f'🔗 <b>Url :</b> <a href="{url}">{label}</a>\n'
        f"⏱ <b>Duration :</b> {duration}\n"
        f"{sep}\n"
        f"{credit}"
    )

# ─────────────────────────────────────────────
#  Keyboards
# ─────────────────────────────────────────────
def kb_home() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("▶️  YT Video Download"),
        KeyboardButton("🎵  TT Video Download"),
    )
    return kb

def kb_format() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("🎧  Mp3 ( Audio-only )"),
        KeyboardButton("🎬  Mp4 ( Video )"),
    )
    kb.add(KeyboardButton("🔙  Back"))
    return kb

def kb_back() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add(KeyboardButton("🔙  Back"))
    return kb

# ─────────────────────────────────────────────
#  /start  —  welcome + reset session
# ─────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid = msg.from_user.id
    reset_session(uid)
    log.info(f"[START] user={uid}")
    bot.send_message(
        msg.chat.id,
        "👋 <b>Welcome to the Media Downloader Bot!</b>\n\n"
        "I can download videos and audio from:\n"
        "• <b>YouTube</b> — videos & audio\n"
        "• <b>TikTok</b> — videos & audio\n\n"
        "Choose a platform below to get started. ⬇️",
        reply_markup=kb_home(),
    )

# ─────────────────────────────────────────────
#  /help
# ─────────────────────────────────────────────
@bot.message_handler(commands=["help"])
def cmd_help(msg):
    bot.send_message(
        msg.chat.id,
        "ℹ️ <b>How to use this bot:</b>\n\n"
        "1️⃣  Tap <b>YT Video Download</b> or <b>TT Video Download</b>\n"
        "2️⃣  Choose <b>Mp3</b> (audio) or <b>Mp4</b> (video)\n"
        "3️⃣  Send the video link\n"
        "4️⃣  Receive your file 🎉\n\n"
        "Use /start at any time to return to the main menu.",
        reply_markup=kb_home(),
    )

# ─────────────────────────────────────────────
#  Main text router
# ─────────────────────────────────────────────
@bot.message_handler(content_types=["text"])
def route_text(msg):
    uid  = msg.from_user.id
    text = msg.text.strip()
    sess = get_session(uid)

    log.info(f"[MSG] user={uid} step={sess['step']} text={text!r}")

    # ── Back button ──────────────────────────
    if text == "🔙  Back":
        handle_back(msg, sess)
        return

    # ── Home step ───────────────────────────
    if sess["step"] == "home":
        if "YT" in text:
            sess["platform"] = "youtube"
            sess["step"]     = "select_format"
            bot.send_message(
                msg.chat.id,
                "🎬 <b>YouTube</b> selected.\n\nChoose your format:",
                reply_markup=kb_format(),
            )
        elif "TT" in text:
            sess["platform"] = "tiktok"
            sess["step"]     = "select_format"
            bot.send_message(
                msg.chat.id,
                "🎵 <b>TikTok</b> selected.\n\nChoose your format:",
                reply_markup=kb_format(),
            )
        else:
            bot.send_message(
                msg.chat.id,
                "Please use the buttons below to get started. ⬇️",
                reply_markup=kb_home(),
            )
        return

    # ── Format selection step ────────────────
    if sess["step"] == "select_format":
        if "Mp3" in text:
            sess["format"] = "mp3"
            sess["step"]   = "awaiting_link"
            platform_name  = "YouTube" if sess["platform"] == "youtube" else "TikTok"
            bot.send_message(
                msg.chat.id,
                f"🎧 <b>Audio (MP3)</b> mode selected for <b>{platform_name}</b>.\n\n"
                "Please send your video link now. 🔗",
                reply_markup=kb_back(),
            )
        elif "Mp4" in text:
            sess["format"] = "mp4"
            sess["step"]   = "awaiting_link"
            platform_name  = "YouTube" if sess["platform"] == "youtube" else "TikTok"
            bot.send_message(
                msg.chat.id,
                f"🎬 <b>Video (MP4)</b> mode selected for <b>{platform_name}</b>.\n\n"
                "Please send your video link now. 🔗",
                reply_markup=kb_back(),
            )
        else:
            bot.send_message(
                msg.chat.id,
                "Please choose a format using the buttons below. ⬇️",
                reply_markup=kb_format(),
            )
        return

    # ── Awaiting link step ───────────────────
    if sess["step"] == "awaiting_link":
        handle_link(msg, sess)
        return

    # ── Fallback ─────────────────────────────
    bot.send_message(
        msg.chat.id,
        "Use /start to return to the main menu.",
        reply_markup=kb_home(),
    )

# ─────────────────────────────────────────────
#  Back handler
# ─────────────────────────────────────────────
def handle_back(msg, sess: dict):
    uid = msg.from_user.id

    if sess["step"] in ("select_format",):
        sess["platform"] = None
        sess["step"]     = "home"
        bot.send_message(
            msg.chat.id,
            "🏠 Main Menu — Choose a platform:",
            reply_markup=kb_home(),
        )

    elif sess["step"] == "awaiting_link":
        sess["format"] = None
        sess["step"]   = "select_format"
        bot.send_message(
            msg.chat.id,
            "↩️ Back to format selection.\n\nChoose your format:",
            reply_markup=kb_format(),
        )

    else:
        reset_session(uid)
        bot.send_message(
            msg.chat.id,
            "🏠 Main Menu — Choose a platform:",
            reply_markup=kb_home(),
        )

# ─────────────────────────────────────────────
#  Link handler — validation + download trigger
# ─────────────────────────────────────────────
def handle_link(msg, sess: dict):
    url      = msg.text.strip()
    uid      = msg.from_user.id
    platform = sess["platform"]
    fmt      = sess["format"]

    # Validate URL matches the chosen platform
    if platform == "youtube" and not is_youtube_url(url):
        bot.send_message(
            msg.chat.id,
            "❌ <b>Invalid link.</b>\n\n"
            "Please send a valid <b>YouTube</b> URL.\n"
            "Example: <code>[youtu.be](https://youtu.be/dQw4w9WgXcQ)</code>",
            reply_markup=kb_back(),
        )
        return

    if platform == "tiktok" and not is_tiktok_url(url):
        bot.send_message(
            msg.chat.id,
            "❌ <b>Invalid link.</b>\n\n"
            "Please send a valid <b>TikTok</b> URL.\n"
            "Example: <code>[vm.tiktok.com](https://vm.tiktok.com/xxxxxxx/)</code>",
            reply_markup=kb_back(),
        )
        return

    # Acknowledge and start download
    wait_msg = bot.send_message(
        msg.chat.id,
        "⏳ <b>Downloading your media, please wait...</b>\n\n"
        "This may take a moment depending on file size.",
    )

    log.info(f"[DOWNLOAD] user={uid} platform={platform} format={fmt} url={url}")

    try:
        download_and_send(msg, url, platform, fmt, wait_msg)
    except Exception as exc:
        log.error(f"[ERROR] user={uid} — {exc}\n{traceback.format_exc()}")
        try:
            bot.delete_message(msg.chat.id, wait_msg.message_id)
        except Exception:
            pass
        bot.send_message(
            msg.chat.id,
            "❌ <b>Something went wrong.</b>\n\n"
            "The download failed unexpectedly. Please try again later.",
            reply_markup=kb_back(),
        )

# ─────────────────────────────────────────────
#  Core download + upload function
# ─────────────────────────────────────────────
def download_and_send(msg, url: str, platform: str, fmt: str, wait_msg):
    uid     = msg.from_user.id
    chat_id = msg.chat.id

    with tempfile.TemporaryDirectory() as tmpdir:
        # ── yt-dlp options ───────────────────
        common_opts = {
            "outtmpl"         : os.path.join(tmpdir, "%(title).60s.%(ext)s"),
            "noplaylist"      : True,
            "quiet"           : True,
            "no_warnings"     : True,
            "socket_timeout"  : 30,
        }

        if fmt == "mp3":
            ydl_opts = {
                **common_opts,
                "format"          : "bestaudio/best",
                "postprocessors"  : [{
                    "key"              : "FFmpegExtractAudio",
                    "preferredcodec"   : "mp3",
                    "preferredquality" : "192",
                }],
            }
        else:  # mp4
            ydl_opts = {
                **common_opts,
                # Best video + audio merged, capped at 1080 p for size safety
                "format"         : "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[ext=mp4]/best",
                "merge_output_format" : "mp4",
            }

        # ── Run yt-dlp ───────────────────────
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as de:
            err = str(de).lower()
            log.warning(f"[yt-dlp ERROR] user={uid} — {de}")
            try:
                bot.delete_message(chat_id, wait_msg.message_id)
            except Exception:
                pass

            if "private" in err or "unavailable" in err or "members-only" in err:
                bot.send_message(
                    chat_id,
                    "🔒 <b>This video is private or unavailable.</b>\n\n"
                    "Please try a different link.",
                    reply_markup=kb_back(),
                )
            elif "geo" in err or "not available in your country" in err:
                bot.send_message(
                    chat_id,
                    "🌍 <b>This video is geo-restricted</b> and cannot be downloaded.",
                    reply_markup=kb_back(),
                )
            else:
                bot.send_message(
                    chat_id,
                    "❌ <b>Download failed.</b>\n\n"
                    "The link may be invalid, unsupported, or the video is restricted.\n"
                    "Please check the link and try again.",
                    reply_markup=kb_back(),
                )
            return

        # ── Extract metadata ─────────────────
        title    = info.get("title")    or "Unknown Title"
        duration = fmt_duration(info.get("duration"))
        views    = fmt_views(info.get("view_count"))
        src_url  = info.get("webpage_url") or url

        # Clean title for display (strip excessive hashtags / newlines)
        title = title.replace("\n", " ").strip()

        # ── Find the downloaded file ─────────
        downloaded_file = None
        ext_priority    = ["mp3"] if fmt == "mp3" else ["mp4", "mkv", "webm"]

        for root, _, files in os.walk(tmpdir):
            for f in files:
                fp = os.path.join(root, f)
                if any(f.lower().endswith(f".{e}") for e in ext_priority):
                    downloaded_file = fp
                    break
            if downloaded_file:
                break

        # Fallback — take any file in the temp dir
        if not downloaded_file:
            all_files = [
                os.path.join(r, f)
                for r, _, fs in os.walk(tmpdir)
                for f in fs
            ]
            if all_files:
                downloaded_file = all_files[0]

        if not downloaded_file or not os.path.exists(downloaded_file):
            try:
                bot.delete_message(chat_id, wait_msg.message_id)
            except Exception:
                pass
            bot.send_message(
                chat_id,
                "❌ <b>Download failed.</b>\n\n"
                "Could not locate the downloaded file. Please try again.",
                reply_markup=kb_back(),
            )
            return

        # ── File size check (Telegram limit: 50 MB) ──
        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        log.info(f"[FILE] user={uid} size={file_size_mb:.2f} MB path={downloaded_file}")

        if file_size_mb > 49:
            try:
                bot.delete_message(chat_id, wait_msg.message_id)
            except Exception:
                pass
            bot.send_message(
                chat_id,
                f"⚠️ <b>File too large to upload.</b>\n\n"
                f"The file is <b>{file_size_mb:.1f} MB</b>. "
                "Telegram bots support a maximum of <b>50 MB</b>.\n\n"
                "Try a shorter video or choose MP3 audio instead.",
                reply_markup=kb_back(),
            )
            return

        # ── Build caption ────────────────────
        caption = build_caption(title, views, src_url, duration, platform)

        # ── Upload to Telegram ───────────────
        try:
            bot.delete_message(chat_id, wait_msg.message_id)
        except Exception:
            pass

        upload_status = bot.send_message(chat_id, "📤 <b>Uploading your file...</b>")

        try:
            with open(downloaded_file, "rb") as media_file:
                if fmt == "mp3":
                    bot.send_audio(
                        chat_id,
                        audio   = media_file,
                        caption = caption,
                        title   = title[:64],
                        timeout = 120,
                    )
                else:
                    bot.send_video(
                        chat_id,
                        video            = media_file,
                        caption          = caption,
                        supports_streaming = True,
                        timeout          = 120,
                    )

            log.info(f"[SENT] user={uid} title={title!r} format={fmt}")

        except telebot.apihelper.ApiTelegramException as api_err:
            log.error(f"[UPLOAD ERROR] user={uid} — {api_err}")
            bot.send_message(
                chat_id,
                "❌ <b>Upload to Telegram failed.</b>\n\n"
                "The file may be too large or in an unsupported format.\n"
                "Please try again or choose a different format.",
                reply_markup=kb_back(),
            )
            return

        finally:
            try:
                bot.delete_message(chat_id, upload_status.message_id)
            except Exception:
                pass

        # ── Post-download menu ───────────────
        bot.send_message(
            chat_id,
            "✅ <b>Done!</b> Your file has been sent.\n\n"
            "What would you like to do next?",
            reply_markup=kb_home(),
        )

        # Reset to home for clean next interaction
        reset_session(uid)

# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Bot is starting... 🚀")
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
