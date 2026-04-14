"""
Telegram Group Engagement Bot
==============================
Tracks valid group messages, awards points, and provides
a leaderboard with personal rank lookup.

All logic lives in this single file: single.py
"""

# ─────────────────────────────────────────────
# Imports
# ─────────────────────────────────────────────
import asyncio
import hashlib
import logging
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "bot_data.db")

# Anti-spam settings
COOLDOWN_SECONDS: int = int(os.getenv("COOLDOWN_SECONDS", "5"))
MIN_MESSAGE_LENGTH: int = int(os.getenv("MIN_MESSAGE_LENGTH", "3"))
POINTS_PER_MESSAGE: int = 4
TOP_N: int = 15

# URL / link detection pattern
URL_PATTERN = re.compile(
    r"(https?://|www\.|t\.me/|tg://)",
    re.IGNORECASE,
)

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("EngagementBot")

# Silence overly verbose third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ─────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Return a thread-safe SQLite connection with WAL mode enabled."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_cursor():
    """Context manager that yields a cursor and commits / rolls back automatically."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all required tables if they do not exist."""
    with db_cursor() as cur:
        # Groups table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id    INTEGER PRIMARY KEY,
                title       TEXT    NOT NULL,
                added_at    TEXT    NOT NULL
            )
        """)

        # Users table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id      INTEGER PRIMARY KEY,
                display_name TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL
            )
        """)

        # Stats table — one row per (group, user) pair
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id        INTEGER NOT NULL REFERENCES groups(group_id),
                user_id         INTEGER NOT NULL REFERENCES users(user_id),
                message_count   INTEGER NOT NULL DEFAULT 0,
                total_points    INTEGER NOT NULL DEFAULT 0,
                last_message_at REAL    NOT NULL DEFAULT 0,
                last_msg_hash   TEXT    NOT NULL DEFAULT '',
                UNIQUE(group_id, user_id)
            )
        """)

        # Index for fast leaderboard queries
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_stats_group_points
            ON stats(group_id, total_points DESC)
        """)

    logger.info("Database initialised at '%s'", DATABASE_PATH)


# ─────────────────────────────────────────────
# Database helper functions
# ─────────────────────────────────────────────

def upsert_group(group_id: int, title: str) -> None:
    """Insert or update a group record."""
    now = datetime.now(timezone.utc).isoformat()
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO groups (group_id, title, added_at)
            VALUES (?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET title = excluded.title
        """, (group_id, title, now))


def upsert_user(user_id: int, display_name: str) -> None:
    """Insert or update a user record."""
    now = datetime.now(timezone.utc).isoformat()
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO users (user_id, display_name, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE
                SET display_name = excluded.display_name,
                    updated_at   = excluded.updated_at
        """, (user_id, display_name, now))


def get_user_stats(group_id: int, user_id: int) -> Optional[sqlite3.Row]:
    """Return the stats row for a (group, user) pair, or None."""
    with db_cursor() as cur:
        cur.execute("""
            SELECT * FROM stats
            WHERE group_id = ? AND user_id = ?
        """, (group_id, user_id))
        return cur.fetchone()


def award_points(
    group_id: int,
    user_id: int,
    msg_hash: str,
    now_ts: float,
) -> None:
    """
    Award POINTS_PER_MESSAGE to the user in the given group.
    Assumes all anti-spam checks have already passed.
    """
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO stats
                (group_id, user_id, message_count, total_points,
                 last_message_at, last_msg_hash)
            VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(group_id, user_id) DO UPDATE
                SET message_count   = message_count + 1,
                    total_points    = total_points + ?,
                    last_message_at = ?,
                    last_msg_hash   = ?
        """, (
            group_id, user_id, POINTS_PER_MESSAGE, now_ts, msg_hash,
            POINTS_PER_MESSAGE, now_ts, msg_hash,
        ))


def get_leaderboard(group_id: int, limit: int = TOP_N) -> list:
    """Return the top `limit` users for a group, ordered by points descending."""
    with db_cursor() as cur:
        cur.execute("""
            SELECT
                u.display_name,
                s.message_count,
                s.total_points,
                RANK() OVER (ORDER BY s.total_points DESC) AS rank
            FROM stats s
            JOIN users u ON u.user_id = s.user_id
            WHERE s.group_id = ?
            ORDER BY s.total_points DESC
            LIMIT ?
        """, (group_id, limit))
        return cur.fetchall()


def get_user_rank(group_id: int, user_id: int) -> Optional[dict]:
    """
    Return a dict with rank, display_name, message_count, total_points
    for the given user in the given group. Returns None if no data found.
    """
    with db_cursor() as cur:
        cur.execute("""
            SELECT
                u.display_name,
                s.message_count,
                s.total_points,
                (
                    SELECT COUNT(*) + 1
                    FROM stats s2
                    WHERE s2.group_id = ? AND s2.total_points > s.total_points
                ) AS rank
            FROM stats s
            JOIN users u ON u.user_id = s.user_id
            WHERE s.group_id = ? AND s.user_id = ?
        """, (group_id, group_id, user_id))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)


# ─────────────────────────────────────────────
# Anti-spam validation
# ─────────────────────────────────────────────

def message_is_valid(update: Update) -> tuple:
    """
    Validate whether a group message should earn points.

    Returns (is_valid: bool, reason: str).
    The reason string is used only for debug logging.
    """
    msg = update.effective_message

    # No message object
    if msg is None:
        return False, "no message object"

    # Ignore service / system messages
    if msg.new_chat_members or msg.left_chat_member:
        return False, "service message"
    if msg.new_chat_title or msg.new_chat_photo or msg.delete_chat_photo:
        return False, "chat event"
    if msg.pinned_message or msg.migrate_from_chat_id or msg.migrate_to_chat_id:
        return False, "chat event"

    # Ignore forwarded messages (forward_origin covers all forward types in PTB v20+)
    if msg.forward_origin is not None:
        return False, "forwarded message"

    # Ignore bot messages
    sender = update.effective_user
    if sender is None or sender.is_bot:
        return False, "sender is a bot"

    # Require a text payload
    text: str = (msg.text or msg.caption or "").strip()
    if not text:
        return False, "no text content"

    # Too short
    if len(text) < MIN_MESSAGE_LENGTH:
        return False, f"text too short ({len(text)} chars)"

    # Link-only spam: if the ENTIRE message is a URL / invite link
    words = text.split()
    if len(words) == 1 and URL_PATTERN.search(text):
        return False, "link-only message"

    return True, "ok"


def passes_cooldown_and_duplicate(
    stats_row: Optional[sqlite3.Row],
    msg_hash: str,
    now_ts: float,
) -> tuple:
    """
    Check cooldown and duplicate-text rules against the stored stats row.

    Returns (passes: bool, reason: str).
    """
    if stats_row is None:
        # First message from this user in this group — always allow
        return True, "first message"

    elapsed = now_ts - float(stats_row["last_message_at"])
    if elapsed < COOLDOWN_SECONDS:
        return False, f"cooldown ({elapsed:.1f}s < {COOLDOWN_SECONDS}s)"

    if msg_hash == stats_row["last_msg_hash"] and msg_hash != "":
        return False, "duplicate consecutive message"

    return True, "ok"


def compute_hash(text: str) -> str:
    """Return a short SHA-256 hex digest of the normalised message text."""
    normalised = " ".join(text.lower().split())
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────

MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}


def format_leaderboard(
    rows: list,
    group_title: str,
) -> str:
    """Build the leaderboard message string."""
    if not rows:
        return (
            "📊 *Leaderboard*\n\n"
            "_No activity recorded in this group yet\\._\n"
            "Start chatting to earn points\\! 💬"
        )

    lines = [
        f"🏆 *{escape_md(group_title)} — Leaderboard*",
        "",
        f"{'Rank':<5} {'Name':<22} {'Points':>7}",
        "─" * 38,
    ]

    for row in rows:
        rank: int = int(row["rank"])
        name: str = row["display_name"][:20]
        pts: int = row["total_points"]
        medal: str = MEDAL.get(rank, f"#{rank}")
        lines.append(f"{medal:<5} {name:<22} {pts:>7} pts")

    lines.append("")
    lines.append("_Tap_ *Find My Place* _to see your personal rank\\._")
    return "\n".join(lines)


def escape_md(text: str) -> str:
    """Escape special MarkdownV2 characters."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)


def build_display_name(user) -> str:
    """Construct a readable display name from a Telegram User object."""
    if user.full_name:
        return user.full_name.strip()[:60]
    return f"User_{user.id}"


# ─────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /start in private chat.
    Shows a welcome message and an 'Add Me To Your Group' button.
    """
    if update.effective_chat.type != ChatType.PRIVATE:
        return  # Only respond in private chat

    bot: Bot = context.bot
    bot_info = await bot.get_me()
    bot_username = bot_info.username

    # FIX: plain HTTPS URL — NOT Markdown link syntax
    add_url = f"https://t.me/{bot_username}?startgroup=true"

    welcome_text = (
        "👋 *Welcome to the Group Engagement Bot\\!*\n\n"
        "I help your Telegram groups stay active by rewarding members "
        "for every meaningful message they send\\.\n\n"
        "✨ *How it works:*\n"
        "• Add me to your group\n"
        "• I silently track valid messages\n"
        "• Each valid message earns *4 points*\n"
        "• Use `/leaderboard` in the group to see the top members\n"
        "• Use *Find My Place* to check your own rank\n\n"
        "📋 *Before you add me, please ensure:*\n"
        "• I am promoted with permission to *read messages*\n"
        "• If your group has Privacy Mode enabled via \\@BotFather, "
        "disable it so I can see all messages\n\n"
        "👇 *Ready\\? Add me to your group now\\!*"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Me To Your Group", url=add_url)],
    ])

    await update.effective_message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
    )
    logger.info("Sent /start welcome to user %s", update.effective_user.id)


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /leaderboard in a group chat.
    Displays top-15 users and a 'Find My Place' button.
    """
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await update.effective_message.reply_text(
            "⚠️ This command only works inside a group."
        )
        return

    group_id: int = chat.id
    group_title: str = chat.title or "This Group"

    # Ensure the group is registered
    upsert_group(group_id, group_title)

    rows = get_leaderboard(group_id)
    text = format_leaderboard(rows, group_title)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Find My Place", callback_data=f"myplace:{group_id}")],
    ])

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
    )
    logger.info(
        "Leaderboard requested in group %s (%s) by user %s",
        group_id, group_title, update.effective_user.id,
    )


async def cb_find_my_place(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the 'Find My Place' inline button callback.
    Answers with a private alert showing the user's rank, points, and message count.
    """
    query = update.callback_query
    await query.answer()  # Acknowledge immediately to avoid Telegram timeout

    user = update.effective_user
    if user is None:
        return

    # Parse group_id from callback data
    try:
        _, raw_group_id = query.data.split(":")
        group_id = int(raw_group_id)
    except (ValueError, AttributeError):
        await query.answer("❌ Invalid request.", show_alert=True)
        return

    result = get_user_rank(group_id, user.id)

    if result is None:
        msg = (
            "😶 You haven't earned any points in this group yet.\n"
            "Start chatting to appear on the leaderboard!"
        )
    else:
        rank = result["rank"]
        pts = result["total_points"]
        msgs = result["message_count"]
        medal = MEDAL.get(rank, f"#{rank}")
        msg = (
            f"📊 Your Stats\n"
            f"─────────────────\n"
            f"🏅 Rank:     {medal} {rank}\n"
            f"⭐ Points:   {pts} pts\n"
            f"💬 Messages: {msgs}\n"
            f"─────────────────\n"
            f"Keep it up! Every message counts."
        )

    await query.answer(msg, show_alert=True)
    logger.info(
        "Find My Place: user %s in group %s — %s",
        user.id, group_id, "found" if result else "not found",
    )


async def handle_group_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle every incoming group message.
    Validates it through anti-spam checks and awards points if valid.
    """
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if msg is None or chat is None or user is None:
        return

    group_id: int = chat.id
    group_title: str = chat.title or "Unknown Group"
    user_id: int = user.id
    now_ts: float = time.time()

    # ── Step 1: Basic content validation ──────────────────────────────────────
    valid, reason = message_is_valid(update)
    if not valid:
        logger.debug("Message rejected [%s] from user %s in group %s", reason, user_id, group_id)
        return

    # ── Step 2: Register group and user (idempotent) ──────────────────────────
    upsert_group(group_id, group_title)
    upsert_user(user_id, build_display_name(user))

    # ── Step 3: Cooldown + duplicate check ────────────────────────────────────
    text = (msg.text or msg.caption or "").strip()
    msg_hash = compute_hash(text)
    stats_row = get_user_stats(group_id, user_id)

    passes, reason = passes_cooldown_and_duplicate(stats_row, msg_hash, now_ts)
    if not passes:
        logger.debug(
            "Message skipped [%s] for user %s in group %s", reason, user_id, group_id
        )
        return

    # ── Step 4: Award points ──────────────────────────────────────────────────
    award_points(group_id, user_id, msg_hash, now_ts)
    logger.debug(
        "Awarded %d pts to user %s (%s) in group %s",
        POINTS_PER_MESSAGE, user_id, build_display_name(user), group_id,
    )


async def handle_bot_added(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Detect when the bot is added to a group and send a welcome message there.
    """
    bot: Bot = context.bot
    bot_info = await bot.get_me()
    msg = update.effective_message
    chat = update.effective_chat

    if msg is None or chat is None:
        return

    new_members = msg.new_chat_members or []
    bot_was_added = any(m.id == bot_info.id for m in new_members)

    if not bot_was_added:
        return

    group_title = chat.title or "this group"
    upsert_group(chat.id, group_title)

    welcome = (
        f"👋 Hello, *{escape_md(group_title)}*\\!\n\n"
        "I'm your *Group Engagement Bot*\\. From now on I'll silently track "
        "every meaningful message in this chat and reward members with points\\.\n\n"
        "📌 *How to use me:*\n"
        "• Just chat naturally — every valid message earns *4 points*\n"
        "• Run `/leaderboard` to see who's leading\n"
        "• Tap *Find My Place* under the leaderboard to see your rank\n\n"
        "Let the competition begin\\! 🚀"
    )

    await chat.send_message(welcome, parse_mode=ParseMode.MARKDOWN_V2)
    logger.info("Bot added to group %s (%s)", chat.id, group_title)


# ─────────────────────────────────────────────
# Application factory
# ─────────────────────────────────────────────

def build_application() -> Application:
    """Construct and configure the Telegram Application."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not set. "
            "Create a .env file or set the environment variable."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # ── Command handlers ───────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard, filters=filters.ChatType.GROUPS))

    # ── Callback handlers ──────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_find_my_place, pattern=r"^myplace:"))

    # ── Bot-added-to-group handler ─────────────────────────────────────────────
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_bot_added)
    )

    # ── Group message handler ──────────────────────────────────────────────────
    app.add_handler(
        MessageHandler(
            (filters.ChatType.GROUPS)
            & (~filters.COMMAND)
            & (~filters.StatusUpdate.ALL),
            handle_group_message,
        )
    )

    logger.info("All handlers registered.")
    return app


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main() -> None:
    """Initialise the database and start the bot in polling mode."""
    logger.info("Starting Telegram Group Engagement Bot…")
    init_db()

    app = build_application()

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
