#!/usr/bin/env python3
"""
Telegram Voice-to-Text Bot
Uses faster-whisper for offline speech recognition.

𝘾𝙧𝙚𝙖𝙩𝙚𝙙 𝘽𝙮 | 𝙎𝙖𝙖𝙁𝙚 🖤
"""

import os
import logging
import asyncio
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, constants
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from faster_whisper import WhisperModel

# ──────────────────────────────────────────────
# Environment & Logging
# ──────────────────────────────────────────────

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise EnvironmentError("BOT_TOKEN environment variable is not set.")

# Whisper model size: tiny | base | small | medium | large-v2
# "base" is a good balance between speed and accuracy for a server.
WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "base")

# Device: "cpu" or "cuda" (GPU). Use "cpu" on Railway.app.
WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cpu")

# Compute type: "int8" is fastest on CPU, "float16" for GPU.
WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

# Cache directory — persisted across restarts when using a volume.
CACHE_DIR: Path = Path(os.getenv("CACHE_DIR", "./model_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Temp directory for downloaded voice files.
TEMP_DIR: Path = Path(os.getenv("TEMP_DIR", "./temp_audio"))
TEMP_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("VoiceBot")

# ──────────────────────────────────────────────
# Model — loaded once at startup
# ──────────────────────────────────────────────

whisper_model: WhisperModel | None = None


def load_whisper_model() -> WhisperModel:
    """
    Load faster-whisper model from local cache if available,
    otherwise download and cache it automatically.
    """
    logger.info(
        "Loading Whisper model: size=%s  device=%s  compute=%s  cache=%s",
        WHISPER_MODEL_SIZE,
        WHISPER_DEVICE,
        WHISPER_COMPUTE_TYPE,
        CACHE_DIR,
    )
    # faster-whisper respects the HF_HOME / download_root for caching.
    model = WhisperModel(
        WHISPER_MODEL_SIZE,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
        download_root=str(CACHE_DIR),
    )
    logger.info("Whisper model loaded successfully ✓")
    return model


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

WELCOME_MESSAGE = """
🎙️ *Voice-to-Text Bot*

আমাকে যেকোনো *Voice Message* অথবা *Audio File* পাঠাও।
আমি সেটা শুনে তোমাকে *text* এ লিখে দেব। ✨

*সমর্থিত ফরম্যাট:* OGG, MP3, M4A, WAV, FLAC, MP4

*Model:* `{model}` (offline — তোমার audio কোথাও পাঠানো হবে না 🔒)

——————————————————
𝘾𝙧𝙚𝙖𝙩𝙚𝙙 𝘽𝙮 | 𝙎𝙖𝙖𝙁𝙚 🖤
""".strip()


async def transcribe_audio(file_path: str) -> str:
    """
    Run faster-whisper transcription in a thread pool so the
    async event loop is never blocked.
    """
    loop = asyncio.get_event_loop()

    def _transcribe():
        segments, info = whisper_model.transcribe(
            file_path,
            beam_size=5,
            language=None,       # auto-detect language
            vad_filter=True,     # skip silent parts
            vad_parameters={"min_silence_duration_ms": 500},
        )
        logger.info(
            "Detected language: %s (probability: %.2f)",
            info.language,
            info.language_probability,
        )
        text = " ".join(segment.text.strip() for segment in segments)
        return text.strip()

    return await loop.run_in_executor(None, _transcribe)


async def download_voice_file(file, suffix: str) -> str:
    """
    Download a Telegram file object to a temp path and return the path.
    """
    temp_path = TEMP_DIR / f"{file.file_unique_id}{suffix}"
    await file.download_to_drive(str(temp_path))
    logger.info("Downloaded audio → %s  (%.1f KB)", temp_path, temp_path.stat().st_size / 1024)
    return str(temp_path)


def cleanup(path: str) -> None:
    """Delete a temp file silently."""
    try:
        os.remove(path)
        logger.debug("Cleaned up temp file: %s", path)
    except OSError:
        pass


# ──────────────────────────────────────────────
# Handlers
# ──────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    await update.message.reply_text(
        WELCOME_MESSAGE.format(model=WHISPER_MODEL_SIZE),
        parse_mode=constants.ParseMode.MARKDOWN,
    )
    logger.info("User %s started the bot.", update.effective_user.id)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages (OGG/OPUS from Telegram)."""
    await _process_audio(update, context, update.message.voice, suffix=".ogg")


async def audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming audio file messages (MP3, M4A, WAV, etc.)."""
    audio = update.message.audio
    # Determine the file extension from mime_type or default to .mp3
    mime = audio.mime_type or "audio/mpeg"
    ext_map = {
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
        "audio/webm": ".webm",
    }
    suffix = ext_map.get(mime, ".mp3")
    await _process_audio(update, context, audio, suffix=suffix)


async def _process_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_obj,
    suffix: str,
) -> None:
    """
    Core pipeline:
      1. Send a 'processing' status message.
      2. Download the audio file.
      3. Transcribe with Whisper.
      4. Reply with the resulting text.
      5. Clean up temp file.
    """
    user = update.effective_user
    logger.info("Received audio from user %s (%s)", user.id, user.full_name)

    # Let the user know we're working on it.
    status_msg = await update.message.reply_text(
        "🎧 *Processing your audio…* Please wait a moment.",
        parse_mode=constants.ParseMode.MARKDOWN,
    )

    file_path: str | None = None
    try:
        # ── 1. Download ──────────────────────────────
        tg_file = await context.bot.get_file(file_obj.file_id)
        file_path = await download_voice_file(tg_file, suffix)

        # ── 2. Transcribe ────────────────────────────
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=constants.ChatAction.TYPING,
        )
        transcript = await transcribe_audio(file_path)

        # ── 3. Reply ─────────────────────────────────
        if transcript:
            reply = (
                f"📝 *Transcription:*\n\n{transcript}\n\n"
                f"——————————————————\n"
                f"𝘾𝙧𝙚𝙖𝙩𝙚𝙙 𝘽𝙮 | 𝙎𝙖𝙖𝙁𝙚 🖤"
            )
        else:
            reply = (
                "⚠️ কোনো কথা ডিটেক্ট করা যায়নি।\n"
                "অডিওটি পরিষ্কার এবং নীরবতামুক্ত কিনা নিশ্চিত করো।"
            )

        await status_msg.edit_text(reply, parse_mode=constants.ParseMode.MARKDOWN)
        logger.info("Transcription sent to user %s.", user.id)

    except Exception as exc:
        logger.exception("Error while processing audio for user %s: %s", user.id, exc)
        await status_msg.edit_text(
            "❌ *Error:* অডিও প্রসেস করতে সমস্যা হয়েছে।\n"
            "একটু পরে আবার চেষ্টা করো।",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
    finally:
        if file_path:
            cleanup(file_path)


async def unknown_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Politely reject non-audio messages."""
    await update.message.reply_text(
        "🎙️ আমাকে শুধু *Voice Message* বা *Audio File* পাঠাও।\n"
        "/start দিয়ে সাহায্য দেখো।",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main() -> None:
    global whisper_model

    # Load model before accepting any messages.
    try:
        whisper_model = load_whisper_model()
    except Exception as exc:
        logger.critical("Failed to load Whisper model: %s", exc)
        raise SystemExit(1) from exc

    # Build the bot application.
    app = Application.builder().token(BOT_TOKEN).build()

    # Register handlers.
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.AUDIO, audio_handler))
    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.VOICE & ~filters.AUDIO,
            unknown_handler,
        )
    )

    logger.info("Bot is running. Waiting for messages…")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,   # ignore messages sent while bot was offline
    )


if __name__ == "__main__":
    main()
    
