# 🤖 Telegram Group Engagement Bot

A production-ready Telegram bot that rewards group members for active participation. Built in a single Python file with JSON storage.

---

## ✨ Features

- `/start` in private chat with Add to Group button
- **4 points** per valid message, **2 points** for stickers
- Smart anti-spam (cooldown, duplicate detection, forward filtering)
- `/leaderboard` — top 15 with medals and titles
- **Find My Place** — personal rank, points, message count
- `/rank` — personal stats anywhere
- `/gift` — send points to other users
- `/shop` + `/buy` — points shop with custom titles
- Admin panel via `/admin`
- Airdrop system (manual + scheduled)
- Daily best typer announcement
- Group approval system
- JSON-based persistence

---

## 🚀 Setup (Local)

### 1. Clone the repo
```bash
git clone [github.com](https://github.com/yourusername/your-repo.git)
cd your-repo
```

### 2. Create `.env`
```bash
cp .env.example .env
```
Fill in your `BOT_TOKEN` and `ADMIN_ID`.

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Run
```bash
python single.py
```

---

## 📋 Commands

| Command | Description |
|---|---|
| `/start` | Welcome message (private only) |
| `/leaderboard` | Top 15 in current group |
| `/rank` | Your personal stats |
| `/gift <amount> @user` | Send points to someone |
| `/shop` | View available items |
| `/buy <id> [title]` | Purchase an item |
| `/admin` | Admin control panel |
| `/add <pts> <user>` | Admin: add points |
| `/get <pts> <user>` | Admin: remove points |
| `/ignore <chat_id>` | Admin: ignore a chat |

---

## ☁️ Railway Deployment

See Section 8 in the project docs.

