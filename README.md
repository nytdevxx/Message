# 🤖 Telegram Group Engagement Bot

A production-ready Telegram bot that tracks meaningful group messages,
rewards members with points, and provides a live leaderboard — all
inside a single Python file.

---

## ✨ Features

- **/start** in private chat with a polished welcome and group-add button
- Silent message tracking — 4 points per valid message
- Smart anti-spam: ignores bots, forwards, very short texts, links-only, cooldowns, and duplicate messages
- **/leaderboard** command showing top 15 users with medal emojis
- **Find My Place** button — privately shows your rank, points, and message count
- SQLite database (zero configuration)
- Fully async, production-grade code in a single file

---

## 📁 Project Structure

```
telegram-engagement-bot/
├── single.py          ← entire bot logic
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── Procfile
```

---

## ⚙️ Setup

### Prerequisites

- Python 3.11+
- A bot token from [@BotFather](https://t.me/BotFather)
- Privacy Mode **disabled** in BotFather for your bot (so it can read all group messages)

### Local Setup

```bash
# 1. Clone the repository
git clone [github.com](https://github.com/your-username/telegram-engagement-bot.git)
cd telegram-engagement-bot

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and paste your BOT_TOKEN

# 5. Run
python single.py
```

---

## 🤖 Bot Commands

| Command        | Where       | Description                              |
|----------------|-------------|------------------------------------------|
| `/start`       | Private     | Welcome message + Add to Group button    |
| `/leaderboard` | Group       | Top 15 members by points                 |
| Find My Place  | Group (btn) | Your personal rank, points, and messages |

---

## 🚂 Railway Deployment

See the full deployment guide in **Section 8** of the project documentation.

Key environment variable required on Railway:

| Variable          | Description                     |
|-------------------|---------------------------------|
| `BOT_TOKEN`       | Telegram bot token (required)   |
| `DATABASE_PATH`   | SQLite path (default: bot_data.db) |
| `COOLDOWN_SECONDS`| Anti-spam cooldown (default: 5) |
| `MIN_MESSAGE_LENGTH` | Min text length (default: 3) |

---

## 📌 BotFather Setup Checklist

1. Create bot via [@BotFather](https://t.me/BotFather) → `/newbot`
2. Go to **Bot Settings → Group Privacy → Turn Off** (critical for reading messages)
3. Set commands:
   ```
   start - Start the bot
   leaderboard - Show group leaderboard
   ```
4. Copy the token to your `.env`

---

## 📝 Notes

- SQLite data **will be lost** on Railway redeploys if not using a persistent volume.
  See deployment guide for details.
- For long-term production use, migrating to PostgreSQL is recommended.
- The bot never sends messages on every counted message — it is intentionally silent.

---

## 📄 License

MIT

