# single.py — Telegram Group Engagement Bot
# All logic in one file. JSON-based storage.

import os
import json
import time
import hashlib
import logging
import asyncio
from datetime import datetime
from threading import Thread

import pytz
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ChatType, ParseMode

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATA_FILE = "data.json"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# ─────────────────────────────────────────────
# JSON DATABASE
# ─────────────────────────────────────────────

def load_data() -> dict:
    defaults = {
        "users": {},
        "approved_groups": [],
        "logs": [],
        "ignored_chats": [],
        "banned_users": [],
        "settings": {
            "airdrop_word": "Fhaaa",
            "airdrop_points": 100
        },
        "shop": {
            "title": {
                "name": "Custom Title",
                "price": 1000,
                "type": "title"
            }
        }
    }
    if not os.path.exists(DATA_FILE):
        return defaults

    with open(DATA_FILE, "r") as f:
        data = json.load(f)

    for key, value in defaults.items():
        if key not in data:
            data[key] = value

    # Migrate old "groups" key
    if "groups" in data:
        for g in data["groups"]:
            if g not in data["approved_groups"]:
                data["approved_groups"].append(g)
        del data["groups"]

    # Ensure all user fields exist
    for uid in data["users"]:
        u = data["users"][uid]
        u.setdefault("groups_active", [])
        u.setdefault("last_msg_time", 0)
        u.setdefault("custom_title", "")
        u.setdefault("daily_points", 0)
        u.setdefault("message_count", 0)
        u.setdefault("last_msg_hash", "")

    return data


def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(db, f, indent=4)


db = load_data()

# Runtime state
bot_status = True
airdrop_active = False
current_airdrop_word = db["settings"]["airdrop_word"]
current_airdrop_points = db["settings"]["airdrop_points"]

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def find_user(target: str):
    """Find user by ID or username."""
    target_clean = str(target).lstrip("@")
    if target_clean in db["users"]:
        return target_clean
    for uid, info in db["users"].items():
        uname = (info.get("username") or "").lower()
        if uname == target_clean.lower():
            return uid
    return None


def get_title(pts: int, custom: str) -> str:
    if custom:
        return custom
    if pts >= 5000:
        return "🏆 Legend"
    if pts >= 2000:
        return "💎 Expert"
    if pts >= 500:
        return "⚡ Pro"
    return "🌱 Beginner"


def add_log(msg: str):
    db["logs"].append(f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | {msg}")
    if len(db["logs"]) > 200:
        db["logs"] = db["logs"][-200:]
    save_data()


def msg_hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()


def ensure_user(uid: str, name: str, username: str, chat_id_str: str):
    """Create user record if not exists, update name/username."""
    if uid not in db["users"]:
        db["users"][uid] = {
            "name": name,
            "username": username or "",
            "points": 0,
            "daily_points": 0,
            "message_count": 0,
            "last_msg_time": 0,
            "last_msg_hash": "",
            "groups_active": [chat_id_str],
            "custom_title": ""
        }
    else:
        db["users"][uid]["name"] = name
        if username:
            db["users"][uid]["username"] = username
        if chat_id_str not in db["users"][uid].get("groups_active", []):
            db["users"][uid].setdefault("groups_active", []).append(chat_id_str)

# ─────────────────────────────────────────────
# FLASK KEEP-ALIVE
# ─────────────────────────────────────────────

flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return "✅ Bot server is live."

def run_flask():
    flask_app.run(host="0.0.0.0", port=8080)

# ─────────────────────────────────────────────
# AIRDROP & DAILY
# ─────────────────────────────────────────────

async def _broadcast_text(app: Application, text: str):
    for g in db["approved_groups"]:
        if g not in db["ignored_chats"]:
            try:
                await app.bot.send_message(g, text)
            except Exception as e:
                log.warning(f"Broadcast to {g} failed: {e}")


def send_airdrop_sync(app: Application):
    global airdrop_active, current_airdrop_word, current_airdrop_points
    current_airdrop_word = db["settings"]["airdrop_word"]
    current_airdrop_points = db["settings"]["airdrop_points"]
    airdrop_active = True
    text = (
        f"🪂 *AIRDROP ALERT!*\n\n"
        f"The government is verifying unemployment status.\n"
        f"Type `{current_airdrop_word}` to prove it!\n\n"
        f"🎁 Reward: *{current_airdrop_points} points*"
    )
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_broadcast_text(app, text))
    loop.close()


def send_daily_sync(app: Application):
    users_sorted = sorted(
        db["users"].values(),
        key=lambda x: x.get("daily_points", 0),
        reverse=True
    )
    if users_sorted and users_sorted[0].get("daily_points", 0) > 0:
        top = users_sorted[0]
        text = (
            f"🌟 *Best Typer Of The Day* 🌟\n\n"
            f"@{top.get('username', top['name'])} — "
            f"*{top['daily_points']} points* today!\n\n"
            f"Keep it up! 🔥"
        )
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_broadcast_text(app, text))
        loop.close()

    for uid in db["users"]:
        db["users"][uid]["daily_points"] = 0
    save_data()

# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    ensure_user(uid, user.first_name, user.username, "private")

    bot_username = (await context.bot.get_me()).username
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "➕ Add Me To Your Group",
            url=f"[t.me](https://t.me/{bot_username}?startgroup=start)"
        )
    ]])

    welcome = (
        f"👋 *Welcome, {user.first_name}!*\n\n"
        f"I'm a *Group Engagement Bot* that rewards active members.\n\n"
        f"📌 *How it works:*\n"
        f"• Add me to your group\n"
        f"• Every valid message earns *4 points*\n"
        f"• Stickers earn *2 points*\n"
        f"• Check the leaderboard with `/leaderboard`\n"
        f"• Unlock titles and perks in `/shop`\n\n"
        f"⚙️ *Notes:*\n"
        f"• Give me permission to read messages\n"
        f"• Disable privacy mode in @BotFather for full tracking\n"
        f"• Leaderboard works inside groups\n\n"
        f"Ready? Add me to your group below! 👇"
    )

    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)


# ─────────────────────────────────────────────
# /rank
# ─────────────────────────────────────────────

async def cmd_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot_status:
        return
    user = update.effective_user
    uid = str(user.id)

    if uid not in db["users"]:
        return await update.message.reply_text("You haven't earned any points yet. Start chatting!")

    u = db["users"][uid]
    pts = u["points"]
    msgs = u.get("message_count", 0)
    title = get_title(pts, u.get("custom_title", ""))

    # Calculate rank across all groups
    all_sorted = sorted(db["users"].values(), key=lambda x: x["points"], reverse=True)
    rank = next((i + 1 for i, x in enumerate(all_sorted) if x == u), "?")

    text = (
        f"📊 *Your Stats*\n\n"
        f"👤 Name: {u['name']}\n"
        f"🏅 Title: {title}\n"
        f"🏆 Global Rank: #{rank}\n"
        f"⭐ Total Points: {pts}\n"
        f"💬 Messages Counted: {msgs}\n"
        f"📅 Points Today: {u.get('daily_points', 0)}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
# /leaderboard
# ─────────────────────────────────────────────

async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot_status:
        return
    chat = update.effective_chat

    if chat.type == ChatType.PRIVATE:
        args = context.args
        if not args:
            text = "📋 *Approved Groups*\n\n"
            for g in db["approved_groups"]:
                try:
                    info = await context.bot.get_chat(g)
                    text += f"• {info.title} — `{g}`\n"
                except:
                    text += f"• ID: `{g}`\n"
            text += "\nUse `/leaderboard <chat_id>` to view a group leaderboard."
            return await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        target_group = args[0]
    else:
        target_group = str(chat.id)

    users_in_group = [
        u for u in db["users"].values()
        if target_group in u.get("groups_active", [])
    ]
    users_sorted = sorted(users_in_group, key=lambda x: x["points"], reverse=True)[:15]

    if not users_sorted:
        return await update.message.reply_text("No data available for this group yet.")

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    text = f"🏆 *Group Leaderboard*\n\n"
    for i, u in enumerate(users_sorted, 1):
        pts = u["points"]
        title = get_title(pts, u.get("custom_title", ""))
        icon = medals.get(i, f"{i}.")
        text += f"{icon} {u['name']} | {title} | {pts} pts\n"

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔍 Find My Place", callback_data=f"rank_{target_group}")
    ]])

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)


async def cb_find_my_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = str(query.from_user.id)
    target_group = query.data.split("_", 1)[1]

    if uid not in db["users"]:
        return await query.answer("You have no points yet.", show_alert=True)

    u = db["users"][uid]
    users_in_group = sorted(
        [x for x in db["users"].values() if target_group in x.get("groups_active", [])],
        key=lambda x: x["points"],
        reverse=True
    )

    rank = next((i + 1 for i, x in enumerate(users_in_group) if x == u), None)
    if rank is None:
        return await query.answer("You're not active in this group yet.", show_alert=True)

    pts = u["points"]
    msgs = u.get("message_count", 0)
    title = get_title(pts, u.get("custom_title", ""))

    await query.answer(
        f"📊 Your Stats in this group:\n"
        f"🏆 Rank: #{rank}\n"
        f"⭐ Points: {pts}\n"
        f"💬 Messages: {msgs}\n"
        f"🏅 Title: {title}",
        show_alert=True
    )


# ─────────────────────────────────────────────
# /gift
# ─────────────────────────────────────────────

async def cmd_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot_status:
        return
    uid = str(update.effective_user.id)
    if uid not in db["users"]:
        return await update.message.reply_text("You have no points to gift.")

    args = context.args
    if len(args) < 2:
        return await update.message.reply_text("Usage: `/gift 100 @username`", parse_mode=ParseMode.MARKDOWN)

    try:
        amount = int(args[0])
    except ValueError:
        return await update.message.reply_text("Invalid amount.")

    if amount <= 0 or db["users"][uid]["points"] < amount:
        return await update.message.reply_text("❌ Invalid amount or insufficient points.")

    target_id = find_user(args[1])
    if not target_id:
        return await update.message.reply_text("User not found.")

    if target_id == uid:
        return await update.message.reply_text("You can't gift yourself.")

    db["users"][uid]["points"] -= amount
    db["users"][target_id]["points"] += amount
    save_data()

    target_name = db["users"][target_id]["name"]
    await update.message.reply_text(f"🎁 Sent *{amount} points* to {target_name}!", parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
# /shop & /buy
# ─────────────────────────────────────────────

async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot_status:
        return
    if not db["shop"]:
        return await update.message.reply_text("🛒 Shop is currently empty.")

    text = "🛒 *Point Shop*\n\n"
    for k, v in db["shop"].items():
        if v["type"] == "title":
            text += f"🏷️ *{v['name']}*\nPrice: {v['price']} pts\nCommand: `/buy {k} YourTitle`\n\n"
        else:
            text += f"📦 *{v['name']}*\nPrice: {v['price']} pts\nCommand: `/buy {k}`\n\n"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot_status:
        return
    uid = str(update.effective_user.id)
    if uid not in db["users"]:
        return await update.message.reply_text("You have no points yet.")

    args = context.args
    if not args:
        return await update.message.reply_text("Usage: `/buy <item_id>`", parse_mode=ParseMode.MARKDOWN)

    item_id = args[0].lower()
    if item_id not in db["shop"]:
        return await update.message.reply_text("❌ Invalid item ID. Check `/shop`", parse_mode=ParseMode.MARKDOWN)

    item = db["shop"][item_id]
    pts = db["users"][uid]["points"]

    if pts < item["price"]:
        return await update.message.reply_text(
            f"❌ Not enough points. You need *{item['price']} pts*.", parse_mode=ParseMode.MARKDOWN
        )

    if item["type"] == "title":
        if len(args) < 2:
            return await update.message.reply_text(
                f"Usage: `/buy {item_id} YourTitle`", parse_mode=ParseMode.MARKDOWN
            )
        new_title = " ".join(args[1:])[:20]
        db["users"][uid]["points"] -= item["price"]
        db["users"][uid]["custom_title"] = new_title
        save_data()
        await update.message.reply_text(
            f"✅ Custom title set to: *{new_title}*", parse_mode=ParseMode.MARKDOWN
        )
    else:
        db["users"][uid]["points"] -= item["price"]
        save_data()
        uname = db["users"][uid].get("username", "")
        await update.message.reply_text(
            f"✅ You purchased: *{item['name']}*!", parse_mode=ParseMode.MARKDOWN
        )
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"🛒 Purchase Alert!\n@{uname} (ID: {uid}) bought *{item['name']}*.",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass


# ─────────────────────────────────────────────
# ADMIN: /admin panel
# ─────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪂 Direct Airdrop", callback_data="adm_air_dir"),
            InlineKeyboardButton("✏️ Custom Airdrop", callback_data="adm_air_cus"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc"),
            InlineKeyboardButton("📊 Stats", callback_data="adm_stats"),
        ],
        [
            InlineKeyboardButton("🛒 Shop Manager", callback_data="adm_shop_mgr"),
            InlineKeyboardButton("🚫 Ban System", callback_data="adm_ban_mgr"),
        ],
        [
            InlineKeyboardButton("⚙️ Airdrop Settings", callback_data="adm_set"),
            InlineKeyboardButton("🔍 Get User ID", callback_data="adm_get_id"),
        ],
        [
            InlineKeyboardButton("🔌 Toggle Bot Power", callback_data="adm_toggle"),
        ]
    ])
    await update.message.reply_text("🛠️ *Admin Control Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=markup)


async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_status, airdrop_active, current_airdrop_word, current_airdrop_points

    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return await query.answer("Unauthorized.", show_alert=True)

    await query.answer()
    data = query.data

    # ── Direct Airdrop
    if data == "adm_air_dir":
        current_airdrop_word = db["settings"]["airdrop_word"]
        current_airdrop_points = db["settings"]["airdrop_points"]
        airdrop_active = True
        text = (
            f"🪂 *AIRDROP ALERT!*\n\n"
            f"Type `{current_airdrop_word}` to claim *{current_airdrop_points} points*!"
        )
        count = 0
        for g in db["approved_groups"]:
            try:
                await context.bot.send_message(g, text, parse_mode=ParseMode.MARKDOWN)
                count += 1
            except:
                pass
        await query.message.reply_text(f"✅ Airdrop sent to {count} groups.")

    # ── Custom Airdrop
    elif data == "adm_air_cus":
        msg = await query.message.reply_text("Send word and points (Format: `Word Points`)", parse_mode=ParseMode.MARKDOWN)
        context.user_data["next_step"] = "custom_airdrop"

    # ── Broadcast
    elif data == "adm_bc":
        await query.message.reply_text("Send the message to broadcast to all groups:")
        context.user_data["next_step"] = "broadcast"

    # ── Stats
    elif data == "adm_stats":
        total_users = len(db["users"])
        total_groups = len(db["approved_groups"])
        total_pts = sum(u["points"] for u in db["users"].values())
        total_msgs = sum(u.get("message_count", 0) for u in db["users"].values())
        await query.message.reply_text(
            f"📊 *Bot Stats*\n\n"
            f"👤 Users: {total_users}\n"
            f"🏠 Approved Groups: {total_groups}\n"
            f"⭐ Total Points Given: {total_pts}\n"
            f"💬 Total Messages Counted: {total_msgs}",
            parse_mode=ParseMode.MARKDOWN
        )

    # ── Ban Manager
    elif data == "adm_ban_mgr":
        await query.message.reply_text("Send the User ID to ban or unban:")
        context.user_data["next_step"] = "ban_manage"

    # ── Settings
    elif data == "adm_set":
        current = db["settings"]
        await query.message.reply_text(
            f"Current: word=`{current['airdrop_word']}`, pts=`{current['airdrop_points']}`\n"
            f"Send new defaults (Format: `Word Points`):",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data["next_step"] = "settings"

    # ── Get ID
    elif data == "adm_get_id":
        await query.message.reply_text("Send the username or first name to search:")
        context.user_data["next_step"] = "get_id"

    # ── Shop Manager
    elif data == "adm_shop_mgr":
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 View Products", callback_data="adm_shop_view"),
                InlineKeyboardButton("➕ Add Product", callback_data="adm_shop_add"),
            ],
            [
                InlineKeyboardButton("✏️ Edit Product", callback_data="adm_shop_edit"),
                InlineKeyboardButton("🗑️ Delete Product", callback_data="adm_shop_del"),
            ]
        ])
        await query.message.reply_text("🛒 *Shop Manager*", parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    elif data == "adm_shop_view":
        if not db["shop"]:
            return await query.message.reply_text("Shop is empty.")
        text = "🛒 *Shop Products*\n\n"
        for k, v in db["shop"].items():
            text += f"ID: `{k}`\nName: {v['name']}\nPrice: {v['price']} pts\nType: {v['type']}\n\n"
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    elif data == "adm_shop_add":
        await query.message.reply_text(
            "Send product details:\nFormat: `ID Name Price`\nExample: `vip VIP_Member 5000`",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data["next_step"] = "shop_add"

    elif data == "adm_shop_edit":
        await query.message.reply_text(
            "Send: `Existing_ID New_Name New_Price`\nExample: `vip Super_VIP 8000`",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data["next_step"] = "shop_edit"

    elif data == "adm_shop_del":
        await query.message.reply_text("Send the product ID to delete:")
        context.user_data["next_step"] = "shop_del"

    # ── Toggle
    elif data == "adm_toggle":
        bot_status = not bot_status
        state = "✅ ON" if bot_status else "❌ OFF"
        await query.message.reply_text(f"Bot is now {state}.")


# ─────────────────────────────────────────────
# ADMIN: next-step message handler (private only)
# ─────────────────────────────────────────────

async def handle_admin_steps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global airdrop_active, current_airdrop_word, current_airdrop_points

    if update.effective_user.id != ADMIN_ID:
        return False
    if update.effective_chat.type != ChatType.PRIVATE:
        return False

    step = context.user_data.get("next_step")
    if not step:
        return False

    context.user_data.pop("next_step")
    text = update.message.text.strip()

    if step == "custom_airdrop":
        try:
            parts = text.split()
            current_airdrop_word = parts[0]
            current_airdrop_points = int(parts[1])
            airdrop_active = True
            msg = (
                f"🪂 *AIRDROP ALERT!*\n\n"
                f"Type `{current_airdrop_word}` to claim *{current_airdrop_points} points*!"
            )
            count = 0
            for g in db["approved_groups"]:
                try:
                    await context.bot.send_message(g, msg, parse_mode=ParseMode.MARKDOWN)
                    count += 1
                except:
                    pass
            await update.message.reply_text(f"✅ Custom airdrop sent to {count} groups.")
        except:
            await update.message.reply_text("❌ Error. Format: `Word Points`", parse_mode=ParseMode.MARKDOWN)

    elif step == "broadcast":
        count = 0
        for g in db["approved_groups"]:
            try:
                await context.bot.send_message(g, text)
                count += 1
            except:
                pass
        await update.message.reply_text(f"✅ Sent to {count} groups.")

    elif step == "ban_manage":
        uid = text
        if uid in db["banned_users"]:
            db["banned_users"].remove(uid)
            await update.message.reply_text(f"✅ User {uid} unbanned.")
        else:
            db["banned_users"].append(uid)
            await update.message.reply_text(f"🚫 User {uid} banned.")
        save_data()

    elif step == "settings":
        try:
            word, pts = text.split()
            db["settings"]["airdrop_word"] = word
            db["settings"]["airdrop_points"] = int(pts)
            save_data()
            await update.message.reply_text(f"✅ Settings updated: `{word}` / `{pts} pts`", parse_mode=ParseMode.MARKDOWN)
        except:
            await update.message.reply_text("❌ Error. Format: `Word Points`", parse_mode=ParseMode.MARKDOWN)

    elif step == "get_id":
        search = text.lower()
        found = []
        for uid, info in db["users"].items():
            uname = (info.get("username") or "").lower()
            name = (info.get("name") or "").lower()
            if search == uname or search in name:
                found.append(f"• {info['name']} | @{info.get('username','')} | ID: `{uid}`")
        if found:
            await update.message.reply_text(
                "🔍 Found:\n\n" + "\n".join(found), parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text("User not found.")

    elif step == "shop_add":
        try:
            parts = text.split(maxsplit=2)
            p_id = parts[0].lower()
            p_name = parts[1].replace("_", " ")
            p_price = int(parts[2])
            db["shop"][p_id] = {"name": p_name, "price": p_price, "type": "custom"}
            save_data()
            await update.message.reply_text(f"✅ Product added: *{p_name}*", parse_mode=ParseMode.MARKDOWN)
        except:
            await update.message.reply_text("❌ Format: `id Product_Name 500`", parse_mode=ParseMode.MARKDOWN)

    elif step == "shop_edit":
        try:
            parts = text.split(maxsplit=2)
            p_id = parts[0].lower()
            if p_id not in db["shop"]:
                return await update.message.reply_text("Product ID not found.")
            p_name = parts[1].replace("_", " ")
            p_price = int(parts[2])
            db["shop"][p_id]["name"] = p_name
            db["shop"][p_id]["price"] = p_price
            save_data()
            await update.message.reply_text(f"✅ Updated: *{p_name}*", parse_mode=ParseMode.MARKDOWN)
        except:
            await update.message.reply_text("❌ Format: `id New_Name 500`", parse_mode=ParseMode.MARKDOWN)

    elif step == "shop_del":
        p_id = text.lower()
        if p_id in db["shop"]:
            del db["shop"][p_id]
            save_data()
            await update.message.reply_text("✅ Product deleted.")
        else:
            await update.message.reply_text("Product ID not found.")

    return True


# ─────────────────────────────────────────────
# ADMIN: /add, /get, /ignore
# ─────────────────────────────────────────────

async def cmd_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        amount = int(context.args[0])
        uid = find_user(context.args[1])
        if not uid:
            return await update.message.reply_text("User not found.")
        db["users"][uid]["points"] += amount
        save_data()
        add_log(f"Admin added {amount} pts to {uid}")
        await update.message.reply_text(f"✅ Added {amount} pts to {db['users'][uid]['name']}.")
    except:
        await update.message.reply_text("Format: `/add 100 username_or_id`", parse_mode=ParseMode.MARKDOWN)


async def cmd_remove_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        amount = int(context.args[0])
        uid = find_user(context.args[1])
        if not uid:
            return await update.message.reply_text("User not found.")
        db["users"][uid]["points"] = max(0, db["users"][uid]["points"] - amount)
        save_data()
        add_log(f"Admin removed {amount} pts from {uid}")
        await update.message.reply_text(f"✅ Removed {amount} pts from {db['users'][uid]['name']}.")
    except:
        await update.message.reply_text("Format: `/get 100 username_or_id`", parse_mode=ParseMode.MARKDOWN)


async def cmd_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        cid = int(context.args[0])
        if cid not in db["ignored_chats"]:
            db["ignored_chats"].append(cid)
        save_data()
        await update.message.reply_text(f"✅ Chat {cid} is now ignored.")
    except:
        await update.message.reply_text("Format: `/ignore <chat_id>`", parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
# GROUP APPROVAL
# ─────────────────────────────────────────────

async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            chat = update.effective_chat
            if chat.id not in db["approved_groups"]:
                markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"grp_app_{chat.id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"grp_rej_{chat.id}"),
                ]])
                try:
                    await context.bot.send_message(
                        ADMIN_ID,
                        f"📥 *Bot added to a new group!*\n\n"
                        f"Name: *{chat.title}*\nID: `{chat.id}`",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=markup
                    )
                except:
                    pass


async def cb_group_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return await query.answer("Unauthorized.")

    parts = query.data.split("_")
    action = parts[1]
    chat_id = int(parts[2])

    if action == "app":
        if chat_id not in db["approved_groups"]:
            db["approved_groups"].append(chat_id)
            save_data()
        await query.edit_message_text(f"✅ Approved group: `{chat_id}`", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.send_message(chat_id, "✅ Bot approved! I'm now active in this group.")
        except:
            pass

    elif action == "rej":
        await query.edit_message_text(f"❌ Rejected group: `{chat_id}`", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.leave_chat(chat_id)
        except:
            pass


# ─────────────────────────────────────────────
# MAIN MESSAGE HANDLER (points)
# ─────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global airdrop_active

    if not bot_status:
        return

    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    # Basic guards
    if not message or not user or user.is_bot:
        return
    if chat.type == ChatType.CHANNEL:
        return
    if chat.id in db["ignored_chats"]:
        return

    uid = str(user.id)

    if uid in db["banned_users"]:
        return

    # Group must be approved
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if chat.id not in db["approved_groups"]:
            return

    # Handle admin next-step in private
    if chat.type == ChatType.PRIVATE:
        handled = await handle_admin_steps(update, context)
        if handled:
            return

    # Only count in groups
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return

    chat_id_str = str(chat.id)
    current_time = time.time()

    # ── Anti-spam checks
    content_type = message.content_type

    # Ignore forwarded messages
    if message.forward_date or message.forward_from or message.forward_from_chat:
        return

    # Ignore very short messages (≤1 char)
    if content_type == "text":
        text_content = message.text or ""
        if len(text_content.strip()) <= 1:
            return

    ensure_user(uid, user.first_name, user.username or "", chat_id_str)
    u = db["users"][uid]

    # Cooldown: 3 seconds per user
    if current_time - u.get("last_msg_time", 0) < 3:
        return

    # Duplicate text detection (same message within 60s window skips)
    if content_type == "text":
        h = msg_hash(text_content)
        if h == u.get("last_msg_hash", "") and current_time - u.get("last_msg_time", 0) < 60:
            return
        u["last_msg_hash"] = h

    # Points
    pts_add = 2 if content_type == "sticker" else 4
    old_pts = u["points"]

    u["points"] += pts_add
    u["daily_points"] = u.get("daily_points", 0) + pts_add
    u["message_count"] = u.get("message_count", 0) + 1
    u["last_msg_time"] = current_time

    # Milestone celebration (every 100 pts)
    if (u["points"] // 100) > (old_pts // 100):
        milestone = (u["points"] // 100) * 100
        try:
            await context.bot.send_message(
                chat.id,
                f"🎉 Congrats *{user.first_name}*! You've reached *{milestone} points*!",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass

    # Airdrop claim
    if airdrop_active and content_type == "text" and message.text == current_airdrop_word:
        airdrop_active = False
        u["points"] += current_airdrop_points
        u["daily_points"] += current_airdrop_points
        try:
            await message.reply_text(
                f"🎊 *CERTIFIED!* {user.first_name} is officially the most berozgar person here.\n"
                f"+{current_airdrop_points} pts! 🪂",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass

    save_data()


# ─────────────────────────────────────────────
# STARTUP & MAIN
# ─────────────────────────────────────────────

def setup_scheduler(app: Application):
    tz = pytz.timezone("Asia/Dhaka")
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(lambda: send_airdrop_sync(app), "cron", hour=11, minute=0)
    scheduler.add_job(lambda: send_airdrop_sync(app), "cron", hour=23, minute=0)
    scheduler.add_job(lambda: send_daily_sync(app), "cron", hour=23, minute=59)
    scheduler.start()
    log.info("Scheduler started.")


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not set in environment.")
    if not ADMIN_ID:
        raise ValueError("ADMIN_ID not set in environment.")

    # Start Flask in background thread
    Thread(target=run_flask, daemon=True).start()
    log.info("Flask keep-alive running on :8080")

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("rank", cmd_rank))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("gift", cmd_gift))
    app.add_handler(CommandHandler("shop", cmd_shop))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("add", cmd_add_points))
    app.add_handler(CommandHandler("get", cmd_remove_points))
    app.add_handler(CommandHandler("ignore", cmd_ignore))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_admin, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(cb_group_approval, pattern="^grp_"))
    app.add_handler(CallbackQueryHandler(cb_find_my_place, pattern="^rank_"))

    # Messages
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members))
    app.add_handler(MessageHandler(
        filters.TEXT | filters.Sticker.ALL | filters.PHOTO | filters.VIDEO |
        filters.Document.ALL | filters.VOICE | filters.ANIMATION,
        handle_message
    ))

    # Scheduler
    setup_scheduler(app)

    log.info("Bot is starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
