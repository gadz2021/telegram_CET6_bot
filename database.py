import aiosqlite
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"

async def init_db():
    """Initialize the database and create tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                current_model TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS whitelist (
                user_id INTEGER PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vocab_progress (
                user_id INTEGER PRIMARY KEY,
                word_index INTEGER DEFAULT 0,
                last_sent DATETIME,
                pause_until DATETIME
            )
        """)
        await db.commit()
    logger.info("Database initialized.")

async def set_user_model(user_id: int, model: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, current_model) VALUES (?, ?)",
            (user_id, model)
        )
        await db.commit()

async def get_user_model(user_id: int, default_model: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT current_model FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default_model

async def add_history(user_id: int, role: str, content: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content)
        ) as cursor:
            await db.commit()
            return cursor.lastrowid

async def get_history(user_id: int, limit: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        # Get last N messages
        async with db.execute(
            "SELECT role, content FROM (SELECT * FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"role": r, "content": c} for r, c in rows]

async def get_history_by_id(history_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT role, content FROM history WHERE id = ?", (history_id,)) as cursor:
            row = await cursor.fetchone()
            return {"role": row[0], "content": row[1]} if row else None

async def clear_history(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        await db.commit()

async def add_to_whitelist(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def remove_from_whitelist(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_whitelist() -> set[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM whitelist") as cursor:
            rows = await cursor.fetchall()
            return {row[0] for row in rows}

async def migrate_json_whitelist(whitelist_file: str):
    """Import whitelist from JSON if it exists."""
    if os.path.exists(whitelist_file):
        try:
            with open(whitelist_file, "r") as f:
                ids = json.load(f)
                async with aiosqlite.connect(DB_PATH) as db:
                    for uid in ids:
                        await db.execute("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (uid,))
                    await db.commit()
            logger.info("Migrated %d users from whitelist.json", len(ids))
            # Optional: Rename the old file to avoid re-migration
            os.rename(whitelist_file, whitelist_file + ".bak")
        except Exception as e:
            logger.error("Migration failed: %s", e)

async def get_vocab_progress(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT word_index FROM vocab_progress WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def update_vocab_progress(user_id: int, word_index: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO vocab_progress (user_id, word_index, last_sent) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (user_id, word_index)
        )
        await db.commit()

async def get_all_active_users() -> list[int]:
    """Get all users who have ever interacted with the bot."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT DISTINCT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def set_pause(user_id: int, days: int):
    """Pause pushes for N days."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO vocab_progress (user_id, pause_until) VALUES (?, datetime('now', '+' || ? || ' days')) "
            "ON CONFLICT(user_id) DO UPDATE SET pause_until = datetime('now', '+' || ? || ' days')",
            (user_id, days, days)
        )
        await db.commit()

async def is_paused(user_id: int) -> bool:
    """Check if the user has paused pushes."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM vocab_progress WHERE user_id = ? AND pause_until > datetime('now')",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None
