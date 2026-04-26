import sqlite3
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)

DB_NAME = "bot_database.db"

# 🔽 এখানে অ্যাডমিন ইউজারনেম বসাও (সামনে @ চিহ্ন না দিলেও চলবে, কোড হ্যান্ডল করবে)
ADMIN_USERNAME = "@Sefuax"


def get_connection():
    """Create and return a database connection."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                first_name TEXT,
                joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_admin   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS downloads (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER,
                username      TEXT,
                link          TEXT,
                download_type TEXT,
                timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
    finally:
        conn.close()


def add_user(user_id: int, username: str, first_name: str):
    """Add a new user and automatically set admin if username matches."""
    conn = get_connection()
    try:
        # Check if this user already exists
        existing = conn.execute(
            "SELECT is_admin FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        if existing is None:
            # Auto admin detection
            admin_flag = 1 if (username and username.lower() == ADMIN_USERNAME.lower()) else 0
            conn.execute(
                "INSERT INTO users (user_id, username, first_name, is_admin) VALUES (?, ?, ?, ?)",
                (user_id, username or "", first_name or "", admin_flag),
            )
        else:
            # If user exists but username changed (rare), update username/first_name
            # Also update admin status if they now match the admin username
            admin_flag = 1 if (username and username.lower() == ADMIN_USERNAME.lower()) else existing["is_admin"]
            conn.execute(
                "UPDATE users SET username = ?, first_name = ?, is_admin = ? WHERE user_id = ?",
                (username or "", first_name or "", admin_flag, user_id),
            )
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"add_user error: {e}")
    finally:
        conn.close()


def is_admin(user_id: int) -> bool:
    """Return True if the user has admin privileges."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row and row["is_admin"])
    except sqlite3.Error as e:
        logger.error(f"is_admin error: {e}")
        return False
    finally:
        conn.close()


def add_admin(user_id: int):
    """Grant admin privileges to a user (manual override)."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,)
        )
        conn.commit()
        logger.info(f"User {user_id} granted admin privileges.")
    except sqlite3.Error as e:
        logger.error(f"add_admin error: {e}")
    finally:
        conn.close()


def log_download(user_id: int, username: str, link: str, download_type: str):
    """Log a completed download event."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO downloads (user_id, username, link, download_type)
               VALUES (?, ?, ?, ?)""",
            (user_id, username or "", link, download_type),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"log_download error: {e}")
    finally:
        conn.close()


def get_total_users() -> int:
    """Return the total number of unique users."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
        return row["cnt"] if row else 0
    except sqlite3.Error as e:
        logger.error(f"get_total_users error: {e}")
        return 0
    finally:
        conn.close()


def get_total_downloads() -> int:
    """Return the total number of downloads ever."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM downloads").fetchone()
        return row["cnt"] if row else 0
    except sqlite3.Error as e:
        logger.error(f"get_total_downloads error: {e}")
        return 0
    finally:
        conn.close()


def get_today_downloads() -> int:
    """Return downloads that occurred today (UTC)."""
    conn = get_connection()
    try:
        today = date.today().isoformat()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM downloads WHERE DATE(timestamp) = ?",
            (today,),
        ).fetchone()
        return row["cnt"] if row else 0
    except sqlite3.Error as e:
        logger.error(f"get_today_downloads error: {e}")
        return 0
    finally:
        conn.close()
