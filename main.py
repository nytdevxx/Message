# main.py

import os
import requests
import subprocess
import telebot
from faster_whisper import WhisperModel

# ===============================
# ENV TOKEN
# ===============================
TOKEN = os.getenv("BOT_TOKEN")

bot = telebot.TeleBot(TOKEN)

# ===============================
# LOAD MODEL
# ===============================
model = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8"
)

print("✅ Whisper Loaded")
print("✅ Bot Running")

# ===============================
# START
# ===============================
@bot.message_handler(commands=['start'])
def start(msg):
    bot.reply_to(
        msg,
        "🎤 Send Voice / Audio\n🌍 All Languages Supported\n📝 I convert speech to text."
    )

# ===============================
# VOICE + AUDIO
# ===============================
@bot.message_handler(content_types=['voice', 'audio'])
def transcribe(msg):
    try:
        wait = bot.reply_to(msg, "⏳ Processing...")

        # -----------------------
        # GET FILE ID
        # -----------------------
        file_id = None

        if msg.voice:
            file_id = msg.voice.file_id

        elif msg.audio:
            file_id = msg.audio.file_id

        file_info = bot.get_file(file_id)

        url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"

        # -----------------------
        # DOWNLOAD
        # -----------------------
        r = requests.get(url)

        with open("input_file", "wb") as f:
            f.write(r.content)

        # -----------------------
        # CONVERT TO WAV
        # -----------------------
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                "input_file",
                "voice.wav"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # -----------------------
        # TRANSCRIBE
        # -----------------------
        segments, info = model.transcribe("voice.wav")

        text = ""

        for seg in segments:
            text += seg.text + " "

        text = text.strip()

        if text == "":
            text = "❌ No speech detected."

        # -----------------------
        # SEND RESULT
        # -----------------------
        bot.delete_message(msg.chat.id, wait.message_id)

        bot.reply_to(
            msg,
            f"🌍 Language: {info.language}\n\n📝 Text:\n{text}"
        )

    except Exception as e:
        bot.reply_to(msg, f"❌ Error:\n{e}")

    finally:
        # cleanup
        for x in ["input_file", "voice.wav"]:
            if os.path.exists(x):
                os.remove(x)

# ===============================
# RUN
# ===============================
bot.infinity_polling(skip_pending=True)
