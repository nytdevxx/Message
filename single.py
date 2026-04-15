import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import json, os, pytz, logging, time
from flask import Flask
from threading import Thread
from apscheduler.schedulers.background import BackgroundScheduler
import config

# ── Silence Flask logs ──────────────────────────────────────────────────────
logging.getLogger('werkzeug').setLevel(logging.ERROR)

bot = telebot.TeleBot(config.BOT_TOKEN)
app = Flask(__name__)

# ── Runtime state ───────────────────────────────────────────────────────────
bot_status             = True
airdrop_active         = False
current_airdrop_word   = "Fhaaa"
current_airdrop_points = 100
DATA_FILE              = "data.json"

# ═══════════════════════════════════════════════════════════════════════════
#  DATA LAYER
# ═══════════════════════════════════════════════════════════════════════════
DEFAULT_DB = {
    "users":           {},
    "approved_groups": [],
    "logs":            [],
    "ignored_chats":   [],
    "banned_users":    [],
    "settings":        {"airdrop_word": "Fhaaa", "airdrop_points": 100},
    "shop":            {
        "title": {"name": "Custom Title", "price": 1000, "type": "title"}
    }
}

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data = json.load(f)

        for key, val in DEFAULT_DB.items():
            if key not in data:
                data[key] = val

        # migrate old "groups" key
        if "groups" in data:
            for g in data["groups"]:
                if g not in data["approved_groups"]:
                    data["approved_groups"].append(g)
            del data["groups"]

        for uid in data["users"]:
            u = data["users"][uid]
            u.setdefault("groups_active",  [])
            u.setdefault("last_msg_time",  0)
            u.setdefault("custom_title",   "")
            u.setdefault("daily_points",   0)

        return data
    return dict(DEFAULT_DB)

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(db, f, indent=4)

db = load_data()

def add_log(msg):
    db["logs"].append(msg)
    save_data()

def find_user(target):
    clean = str(target).lstrip("@")
    if clean in db["users"]:
        return clean
    for uid, info in db["users"].items():
        if (info.get("username") or "").lower() == clean.lower():
            return uid
    return None

def rank_title(pts, custom=""):
    if custom:
        return custom
    if pts >= 5000: return "🏆 Legend"
    if pts >= 2000: return "💎 Expert"
    if pts >= 500:  return "⚡ Pro"
    return "🌱 Beginner"

# ═══════════════════════════════════════════════════════════════════════════
#  FLASK KEEP-ALIVE
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/')
def index():
    return "✅ Bot is live!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# ═══════════════════════════════════════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['start'])
def start_cmd(message):
    if message.chat.type != 'private':
        return

    name = message.from_user.first_name
    uid  = str(message.from_user.id)

    # Register user if first time
    if uid not in db["users"]:
        db["users"][uid] = {
            "name":          name,
            "username":      message.from_user.username or "",
            "points":        0,
            "daily_points":  0,
            "last_msg_time": 0,
            "groups_active": [],
            "custom_title":  ""
        }
        save_data()

    welcome = (
        f"👋 *Hello, {name}!*\n\n"
        f"🤖 I'm *NYT Group Bot* — your group's activity & points tracker!\n\n"
        f"📌 *What I do:*\n"
        f"   • Award points for every message in the group\n"
        f"   • Run surprise airdrops for bonus points 🪂\n"
        f"   • Show a live leaderboard 🏅\n"
        f"   • Let you gift points to friends 🎁\n"
        f"   • Point shop for custom titles 🛍️\n\n"
        f"💬 *Commands:*\n"
        f"`/leaderboard` — Top 15 members\n"
        f"`/balance` — Your points\n"
        f"`/gift <amount> @user` — Gift points\n"
        f"`/shop` — Browse the shop\n"
        f"`/buy <id>` — Purchase an item\n\n"
        f"➕ *Add me to your group and start earning!*"
    )

    markup = InlineKeyboardMarkup()
    bot_info = bot.get_me()
    markup.add(
        InlineKeyboardButton(
            "➕ Add Me to Your Group",
            url=f"[t.me](https://t.me/{bot_info.username}?startgroup=true)"
        )
    )

    bot.send_message(message.chat.id, welcome, parse_mode="Markdown", reply_markup=markup)

# ═══════════════════════════════════════════════════════════════════════════
#  /balance
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['balance'])
def balance_cmd(message):
    if not bot_status: return
    uid = str(message.from_user.id)
    if uid not in db["users"]:
        return bot.reply_to(message, "❌ You have no points yet. Chat in a group first!")

    u     = db["users"][uid]
    title = rank_title(u["points"], u.get("custom_title", ""))
    text  = (
        f"💰 *Your Balance*\n\n"
        f"👤 Name: {u['name']}\n"
        f"🏅 Rank: {title}\n"
        f"⭐ Points: `{u['points']}`\n"
        f"📅 Today: `{u['daily_points']}`"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

# ═══════════════════════════════════════════════════════════════════════════
#  /leaderboard
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['leaderboard'])
def leaderboard(message):
    if not bot_status or message.chat.id in db["ignored_chats"]: return

    if message.chat.type == 'private':
        args = message.text.split()
        if len(args) == 1:
            text = "📋 *Approved Groups:*\n\n"
            for g in db["approved_groups"]:
                try:
                    info = bot.get_chat(g)
                    text += f"🏘 {info.title}\n`/leaderboard {g}`\n\n"
                except:
                    text += f"🆔 `{g}`\n`/leaderboard {g}`\n\n"
            return bot.reply_to(message, text, parse_mode="Markdown")
        target_group = args[1]
    else:
        target_group = str(message.chat.id)

    members = [u for u in db["users"].values()
               if target_group in u.get("groups_active", [])]
    top15   = sorted(members, key=lambda x: x["points"], reverse=True)[:15]

    if not top15:
        return bot.reply_to(message, "📭 No data yet for this group.")

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines  = ["🏆 *Top 15 Leaderboard*\n"]
    for i, u in enumerate(top15, 1):
        medal = medals.get(i, f"{i}.")
        title = rank_title(u["points"], u.get("custom_title", ""))
        lines.append(f"{medal} {u['name']} | {title} | `{u['points']}` pts")

    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

# ═══════════════════════════════════════════════════════════════════════════
#  /gift
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['gift'])
def gift_points(message):
    if not bot_status: return
    try:
        args      = message.text.split()
        amount    = int(args[1])
        target_un = args[2]
        sender_id = str(message.from_user.id)

        if sender_id not in db["users"]:
            return bot.reply_to(message, "❌ You have no points to gift.")
        if amount <= 0 or db["users"][sender_id]["points"] < amount:
            return bot.reply_to(message, "❌ Invalid amount or insufficient balance.")

        target_id = find_user(target_un)
        if not target_id:
            return bot.reply_to(message, "❌ User not found.")

        db["users"][sender_id]["points"] -= amount
        db["users"][target_id]["points"] += amount
        save_data()
        bot.reply_to(
            message,
            f"🎁 *Gift Sent!*\n\n"
            f"You gifted *{amount} pts* to {db['users'][target_id]['name']} 🎉",
            parse_mode="Markdown"
        )
    except:
        bot.reply_to(message, "ℹ️ Usage: `/gift 100 @username`", parse_mode="Markdown")

# ═══════════════════════════════════════════════════════════════════════════
#  /shop  &  /buy
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['shop'])
def shop_menu(message):
    if not bot_status: return
    if not db["shop"]:
        return bot.reply_to(message, "🛒 Shop is currently empty.")

    lines = ["🛍️ *Point Shop*\n"]
    for k, v in db["shop"].items():
        if v['type'] == 'title':
            lines.append(f"🏷️ *{v['name']}* — `{v['price']}` pts\n   `/buy {k} YourTitle`\n")
        else:
            lines.append(f"📦 *{v['name']}* — `{v['price']}` pts\n   `/buy {k}`\n")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=['buy'])
def buy_item(message):
    if not bot_status: return
    uid  = str(message.from_user.id)
    if uid not in db["users"]:
        return bot.reply_to(message, "❌ You have no points yet.")

    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        return bot.reply_to(message, "ℹ️ Usage: `/buy <item_id>`", parse_mode="Markdown")

    item_id = args[1].lower()
    if item_id not in db["shop"]:
        return bot.reply_to(message, "❌ Invalid item. Check `/shop`", parse_mode="Markdown")

    item = db["shop"][item_id]
    pts  = db["users"][uid]["points"]

    if pts < item["price"]:
        return bot.reply_to(
            message,
            f"💸 Not enough points.\nYou need `{item['price']}` pts but have `{pts}` pts.",
            parse_mode="Markdown"
        )

    if item["type"] == "title":
        if len(args) < 3:
            return bot.reply_to(
                message,
                f"ℹ️ Usage: `/buy {item_id} YourTitle`",
                parse_mode="Markdown"
            )
        new_title = args[2][:20]
        db["users"][uid]["points"]       -= item["price"]
        db["users"][uid]["custom_title"]  = new_title
        save_data()
        bot.reply_to(
            message,
            f"✅ *Title Purchased!*\nYour new title: *{new_title}* 🎖️",
            parse_mode="Markdown"
        )
    else:
        db["users"][uid]["points"] -= item["price"]
        save_data()
        bot.reply_to(
            message,
            f"✅ You purchased *{item['name']}*! 🎉",
            parse_mode="Markdown"
        )
        bot.send_message(
            config.ADMIN_ID,
            f"🛒 *Purchase Alert*\n@{db['users'][uid].get('username','?')} bought *{item['name']}*",
            parse_mode="Markdown"
        )

# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN — /admin panel
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != config.ADMIN_ID: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🪂 Direct Airdrop",    callback_data="adm_air_dir"),
        InlineKeyboardButton("✏️ Custom Airdrop",    callback_data="adm_air_cus"),
        InlineKeyboardButton("📢 Broadcast",         callback_data="adm_bc"),
        InlineKeyboardButton("📊 Stats",             callback_data="adm_stats"),
        InlineKeyboardButton("🛍️ Shop Manager",      callback_data="adm_shop_mgr"),
        InlineKeyboardButton("🚫 Ban System",        callback_data="adm_ban_mgr"),
        InlineKeyboardButton("⚙️ Airdrop Settings",  callback_data="adm_set"),
        InlineKeyboardButton("🔍 Get Chat ID",       callback_data="adm_get_id"),
        InlineKeyboardButton("🔌 Toggle Power",      callback_data="adm_toggle")
    )
    bot.reply_to(message, "🛠️ *Admin Control Panel*", parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def admin_callback(call):
    global bot_status, airdrop_active, current_airdrop_word, current_airdrop_points
    if call.from_user.id != config.ADMIN_ID: return

    data = call.data

    if data == "adm_air_dir":
        current_airdrop_word   = db["settings"]["airdrop_word"]
        current_airdrop_points = db["settings"]["airdrop_points"]
        send_airdrop(current_airdrop_word)
        bot.answer_callback_query(call.id, "✅ Airdrop Started!")

    elif data == "adm_air_cus":
        m = bot.send_message(call.message.chat.id, "✏️ Send: `Word Points` (e.g. Lucky 200)", parse_mode="Markdown")
        bot.register_next_step_handler(m, process_custom_airdrop)

    elif data == "adm_bc":
        m = bot.send_message(call.message.chat.id, "📢 Send the message to broadcast:")
        bot.register_next_step_handler(m, process_broadcast)

    elif data == "adm_stats":
        total_pts = sum(u["points"] for u in db["users"].values())
        bot.send_message(
            call.message.chat.id,
            f"📊 *Bot Stats*\n\n"
            f"👥 Users: `{len(db['users'])}`\n"
            f"🏘 Groups: `{len(db['approved_groups'])}`\n"
            f"⭐ Total Points: `{total_pts}`",
            parse_mode="Markdown"
        )

    elif data == "adm_ban_mgr":
        m = bot.send_message(call.message.chat.id, "🚫 Send User ID to ban/unban:")
        bot.register_next_step_handler(m, process_ban_manage)

    elif data == "adm_set":
        m = bot.send_message(call.message.chat.id, "⚙️ Send new default: `Word Points`", parse_mode="Markdown")
        bot.register_next_step_handler(m, process_settings)

    elif data == "adm_get_id":
        m = bot.send_message(call.message.chat.id, "🔍 Send username or first name:")
        bot.register_next_step_handler(m, process_get_id)

    elif data == "adm_shop_mgr":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("📋 View",   callback_data="adm_shop_view"),
            InlineKeyboardButton("➕ Add",    callback_data="adm_shop_add"),
            InlineKeyboardButton("✏️ Edit",   callback_data="adm_shop_edit"),
            InlineKeyboardButton("🗑️ Delete", callback_data="adm_shop_del")
        )
        bot.send_message(call.message.chat.id, "🛍️ *Shop Manager*", parse_mode="Markdown", reply_markup=markup)

    elif data == "adm_shop_view":
        if not db["shop"]:
            return bot.send_message(call.message.chat.id, "🛒 Shop is empty.")
        lines = ["🛍️ *Shop Products:*\n"]
        for k, v in db["shop"].items():
            lines.append(f"🔑 ID: `{k}`\n📦 {v['name']} — `{v['price']}` pts ({v['type']})\n")
        bot.send_message(call.message.chat.id, "\n".join(lines), parse_mode="Markdown")

    elif data == "adm_shop_add":
        m = bot.send_message(call.message.chat.id, "➕ Format: `ID Name Price`\nE.g. `vip VIP_Role 5000`", parse_mode="Markdown")
        bot.register_next_step_handler(m, process_shop_add)

    elif data == "adm_shop_edit":
        m = bot.send_message(call.message.chat.id, "✏️ Format: `ID NewName NewPrice`", parse_mode="Markdown")
        bot.register_next_step_handler(m, process_shop_edit)

    elif data == "adm_shop_del":
        m = bot.send_message(call.message.chat.id, "🗑️ Send Product ID to delete:")
        bot.register_next_step_handler(m, process_shop_del)

    elif data == "adm_toggle":
        bot_status = not bot_status
        status_txt = "🟢 ON" if bot_status else "🔴 OFF"
        bot.send_message(call.message.chat.id, f"🔌 Bot is now *{status_txt}*", parse_mode="Markdown")

# ── Group approval callbacks ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: call.data.startswith("grp_"))
def group_approval_callback(call):
    if call.from_user.id != config.ADMIN_ID: return
    parts   = call.data.split("_")
    action  = parts[1]
    chat_id = int(parts[2])

    if action == "app":
        if chat_id not in db["approved_groups"]:
            db["approved_groups"].append(chat_id)
            save_data()
        bot.edit_message_text(f"✅ Approved: `{chat_id}`", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        try:
            bot.send_message(chat_id, "✅ *Bot approved by Admin!* I'm now active here. 🎉", parse_mode="Markdown")
        except: pass
    elif action == "rej":
        bot.edit_message_text(f"❌ Rejected: `{chat_id}`", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        try:
            bot.leave_chat(chat_id)
        except: pass

# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN — /add  /get (points)
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['add'])
def add_points_cmd(message):
    if message.from_user.id != config.ADMIN_ID: return
    try:
        _, amount, target = message.text.split()
        uid = find_user(target)
        if not uid: return bot.reply_to(message, "❌ User not found.")
        db["users"][uid]["points"] += int(amount)
        save_data()
        bot.reply_to(message, f"✅ Added `{amount}` pts to *{db['users'][uid]['name']}*", parse_mode="Markdown")
        add_log(f"Admin added {amount} pts to {uid}")
    except:
        bot.reply_to(message, "ℹ️ Format: `/add 100 username_or_id`", parse_mode="Markdown")

@bot.message_handler(commands=['get'])
def remove_points_cmd(message):
    if message.from_user.id != config.ADMIN_ID: return
    try:
        _, amount, target = message.text.split()
        uid = find_user(target)
        if not uid: return bot.reply_to(message, "❌ User not found.")
        db["users"][uid]["points"] = max(0, db["users"][uid]["points"] - int(amount))
        save_data()
        bot.reply_to(message, f"✅ Removed `{amount}` pts from *{db['users'][uid]['name']}*", parse_mode="Markdown")
        add_log(f"Admin removed {amount} pts from {uid}")
    except:
        bot.reply_to(message, "ℹ️ Format: `/get 100 username_or_id`", parse_mode="Markdown")

@bot.message_handler(commands=['ignore'])
def ignore_chat(message):
    if message.from_user.id != config.ADMIN_ID: return
    try:
        cid = int(message.text.split()[1])
        if cid not in db["ignored_chats"]:
            db["ignored_chats"].append(cid)
            save_data()
        bot.reply_to(message, f"🔇 Chat `{cid}` ignored.", parse_mode="Markdown")
    except: pass

# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN — step processors
# ═══════════════════════════════════════════════════════════════════════════
def process_custom_airdrop(message):
    global current_airdrop_word, current_airdrop_points
    try:
        word, pts = message.text.split()
        current_airdrop_word   = word
        current_airdrop_points = int(pts)
        send_airdrop(current_airdrop_word)
        bot.reply_to(message, f"🪂 Custom airdrop started! Word: *{word}* | Points: `{pts}`", parse_mode="Markdown")
    except:
        bot.reply_to(message, "❌ Error. Format: `Word Points`", parse_mode="Markdown")

def process_broadcast(message):
    sent = 0
    for g in db["approved_groups"]:
        try:
            bot.send_message(g, message.text)
            sent += 1
        except: pass
    bot.reply_to(message, f"📢 Broadcast sent to *{sent}* groups.", parse_mode="Markdown")

def process_ban_manage(message):
    uid = message.text.strip()
    if uid in db["banned_users"]:
        db["banned_users"].remove(uid)
        bot.reply_to(message, f"✅ User `{uid}` unbanned.", parse_mode="Markdown")
    else:
        db["banned_users"].append(uid)
        bot.reply_to(message, f"🚫 User `{uid}` banned.", parse_mode="Markdown")
    save_data()

def process_settings(message):
    try:
        word, pts = message.text.split()
        db["settings"]["airdrop_word"]   = word
        db["settings"]["airdrop_points"] = int(pts)
        save_data()
        bot.reply_to(message, f"✅ Settings saved.\nWord: *{word}* | Points: `{pts}`", parse_mode="Markdown")
    except:
        bot.reply_to(message, "❌ Error. Format: `Word Points`", parse_mode="Markdown")

def process_get_id(message):
    term = message.text.strip().lower()
    found = []
    for uid, info in db["users"].items():
        if term == (info.get("username") or "").lower() or term in (info.get("name") or "").lower():
            found.append(f"👤 {info['name']} | @{info.get('username','?')} | `{uid}`")
    if found:
        bot.reply_to(message, "🔍 *Found:*\n" + "\n".join(found), parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ User not found.")

def process_shop_add(message):
    try:
        parts = message.text.split(maxsplit=2)
        p_id, p_name, p_price = parts[0].lower(), parts[1].replace("_", " "), int(parts[2])
        db["shop"][p_id] = {"name": p_name, "price": p_price, "type": "custom"}
        save_data()
        bot.reply_to(message, f"✅ Added: *{p_name}* at `{p_price}` pts", parse_mode="Markdown")
    except:
        bot.reply_to(message, "❌ Format: `ID Name_of_Product Price`", parse_mode="Markdown")

def process_shop_edit(message):
    try:
        parts = message.text.split(maxsplit=2)
        p_id = parts[0].lower()
        if p_id not in db["shop"]:
            return bot.reply_to(message, "❌ Product ID not found.")
        db["shop"][p_id]["name"]  = parts[1].replace("_", " ")
        db["shop"][p_id]["price"] = int(parts[2])
        save_data()
        bot.reply_to(message, f"✅ Updated: *{db['shop'][p_id]['name']}*", parse_mode="Markdown")
    except:
        bot.reply_to(message, "❌ Format: `ID NewName NewPrice`", parse_mode="Markdown")

def process_shop_del(message):
    p_id = message.text.strip().lower()
    if p_id in db["shop"]:
        del db["shop"][p_id]
        save_data()
        bot.reply_to(message, "✅ Product deleted.")
    else:
        bot.reply_to(message, "❌ Product ID not found.")

# ═══════════════════════════════════════════════════════════════════════════
#  AIRDROP + DAILY
# ═══════════════════════════════════════════════════════════════════════════
def send_airdrop(word):
    global airdrop_active
    airdrop_active = True
    text = (
        f"🪂 *AIRDROP ALERT!*\n\n"
        f"The government is verifying unemployment status 😂\n"
        f"Type *'{word}'* to claim your bonus points!"
    )
    for g in db["approved_groups"]:
        if g not in db["ignored_chats"]:
            try:
                bot.send_message(g, text, parse_mode="Markdown")
            except: pass

def send_daily():
    top = sorted(db["users"].values(), key=lambda x: x.get("daily_points", 0), reverse=True)
    if top and top[0].get("daily_points", 0) > 0:
        t    = top[0]
        text = (
            f"🌟 *Best Typer of the Day!*\n\n"
            f"🏆 @{t.get('username', t['name'])} with *{t['daily_points']}* points today!\n"
            f"Keep it up! 💪"
        )
        for g in db["approved_groups"]:
            if g not in db["ignored_chats"]:
                try:
                    bot.send_message(g, text, parse_mode="Markdown")
                except: pass
    for uid in db["users"]:
        db["users"][uid]["daily_points"] = 0
    save_data()

# ═══════════════════════════════════════════════════════════════════════════
#  NEW MEMBER (bot added to group)
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(content_types=['new_chat_members'])
def handle_new_chat_members(message):
    for member in message.new_chat_members:
        if member.id == bot.get_me().id:
            chat_id = message.chat.id
            if chat_id not in db["approved_groups"]:
                markup = InlineKeyboardMarkup()
                markup.add(
                    InlineKeyboardButton("✅ Approve", callback_data=f"grp_app_{chat_id}"),
                    InlineKeyboardButton("❌ Reject",  callback_data=f"grp_rej_{chat_id}")
                )
                bot.send_message(
                    config.ADMIN_ID,
                    f"📥 *New Group Request!*\n\n"
                    f"🏘 Name: *{message.chat.title}*\n"
                    f"🆔 ID: `{chat_id}`",
                    parse_mode="Markdown",
                    reply_markup=markup
                )

# ═══════════════════════════════════════════════════════════════════════════
#  MAIN MESSAGE HANDLER — points
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(
    func=lambda m: True,
    content_types=['text','photo','video','document','sticker','animation','voice']
)
def handle_all(message):
    global airdrop_active
    if not bot_status: return
    if message.from_user.is_bot: return
    if message.chat.type == 'channel': return
    if message.chat.id in db["ignored_chats"]: return
    if str(message.from_user.id) in db["banned_users"]: return
    if message.chat.type in ['group', 'supergroup'] and message.chat.id not in db["approved_groups"]:
        return

    now       = time.time()
    uid       = str(message.from_user.id)
    cid_str   = str(message.chat.id)
    pts_add   = 2 if message.content_type == 'sticker' else 4

    # Rate-limit: 3 seconds between point awards
    if uid in db["users"] and now - db["users"][uid].get("last_msg_time", 0) < 3:
        # Still check airdrop even during cooldown
        pass
    else:
        if uid not in db["users"]:
            db["users"][uid] = {
                "name":          message.from_user.first_name,
                "username":      message.from_user.username or "",
                "points":        pts_add,
                "daily_points":  pts_add,
                "last_msg_time": now,
                "groups_active": [cid_str],
                "custom_title":  ""
            }
        else:
            old_pts = db["users"][uid]["points"]
            db["users"][uid]["points"]       += pts_add
            db["users"][uid]["daily_points"] += pts_add
            db["users"][uid]["last_msg_time"]  = now
            db["users"][uid]["name"]           = message.from_user.first_name
            if message.from_user.username:
                db["users"][uid]["username"] = message.from_user.username
            if cid_str not in db["users"][uid]["groups_active"]:
                db["users"][uid]["groups_active"].append(cid_str)

            # Milestone every 100 pts
            new_pts = db["users"][uid]["points"]
            if (new_pts // 100) > (old_pts // 100):
                milestone = (new_pts // 100) * 100
                bot.send_message(
                    message.chat.id,
                    f"🎉 *Milestone!* {db['users'][uid]['name']} just hit *{milestone} points!* 🏅",
                    parse_mode="Markdown"
                )

    # Airdrop catch
    if (airdrop_active
            and message.content_type == 'text'
            and message.text == current_airdrop_word):
        airdrop_active = False
        if uid not in db["users"]:
            db["users"][uid] = {
                "name": message.from_user.first_name,
                "username": message.from_user.username or "",
                "points": current_airdrop_points,
                "daily_points": current_airdrop_points,
                "last_msg_time": now,
                "groups_active": [cid_str],
                "custom_title": ""
            }
        else:
            db["users"][uid]["points"]       += current_airdrop_points
            db["users"][uid]["daily_points"] += current_airdrop_points

        bot.reply_to(
            message,
            f"🎊 *AIRDROP CLAIMED!*\n\n"
            f"🏆 {db['users'][uid]['name']} is officially the most *berozgar* person here 😂\n"
            f"💰 +*{current_airdrop_points}* pts added!",
            parse_mode="Markdown"
        )

    save_data()

# ═══════════════════════════════════════════════════════════════════════════
#  SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════
scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Dhaka'))
scheduler.add_job(lambda: send_airdrop(db["settings"]["airdrop_word"]), 'cron', hour=10, minute=59)
scheduler.add_job(lambda: send_airdrop(db["settings"]["airdrop_word"]), 'cron', hour=22, minute=59)
scheduler.add_job(send_daily, 'cron', hour=23, minute=59)
scheduler.start()

# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    Thread(target=run_flask).start()
    print("🚀 Bot is live!")
    bot.infinity_polling()
