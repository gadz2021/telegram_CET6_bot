import aiosqlite
import json
import logging
import os
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"

async def init_db():
    """Initialize the database and create tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                current_model TEXT,
                push_mode TEXT DEFAULT 'both'
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
        # 遗忘曲线调度表 (SM-2)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS word_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                next_review DATETIME NOT NULL,
                interval INTEGER DEFAULT 1,
                ease_factor REAL DEFAULT 2.5,
                review_count INTEGER DEFAULT 0,
                last_reviewed DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, word)
            )
        """)
        # 连续打卡记录表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS learning_streak (
                user_id INTEGER PRIMARY KEY,
                last_date TEXT,
                streak INTEGER DEFAULT 0
            )
        """)
        await db.commit()

        # 数据库迁移：为旧表补充列
        try:
            await db.execute("ALTER TABLE vocab_progress ADD COLUMN pause_until DATETIME")
            await db.commit()
            logger.info("Database migration: Added pause_until to vocab_progress")
        except aiosqlite.OperationalError:
            pass

        try:
            await db.execute("ALTER TABLE users ADD COLUMN push_mode TEXT DEFAULT 'both'")
            await db.commit()
            logger.info("Database migration: Added push_mode to users")
        except aiosqlite.OperationalError:
            pass

        try:
            await db.execute("ALTER TABLE users ADD COLUMN pending_eval_word TEXT DEFAULT NULL")
            await db.commit()
            logger.info("Database migration: Added pending_eval_word to users")
        except aiosqlite.OperationalError:
            pass

        try:
            await db.execute("ALTER TABLE users ADD COLUMN pending_quiz_word TEXT DEFAULT NULL")
            await db.commit()
            logger.info("Database migration: Added pending_quiz_word to users")
        except aiosqlite.OperationalError:
            pass

    logger.info("Database initialized.")


# ----------------------------------------------------------------------
# 用户模型与设置
# ----------------------------------------------------------------------
async def set_user_model(user_id: int, model: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, current_model) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET current_model = excluded.current_model
        """, (user_id, model))
        await db.commit()

async def get_user_model(user_id: int, default_model: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT current_model FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if (row and row[0]) else default_model

async def get_user_push_mode(user_id: int) -> str:
    """获取用户推送模式：'both' | 'evening_only'"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT push_mode FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if (row and row[0]) else 'both'

async def set_user_push_mode(user_id: int, push_mode: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, push_mode) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET push_mode = excluded.push_mode
        """, (user_id, push_mode))
        await db.commit()


async def get_pending_eval_word(user_id: int) -> str | None:
    """获取当前用户尚未完成自评（记住了/模糊/忘了）的单词"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT pending_eval_word FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if (row and row[0]) else None


async def set_pending_eval_word(user_id: int, word: str | None):
    """设置或清空用户当前待自评的单词"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, pending_eval_word) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET pending_eval_word = excluded.pending_eval_word
        """, (user_id, word.lower() if word else None))
        await db.commit()


async def get_pending_quiz_word(user_id: int) -> str | None:
    """获取当前用户正在被闪卡测验的单词"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT pending_quiz_word FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if (row and row[0]) else None


async def set_pending_quiz_word(user_id: int, word: str | None):
    """设置或清空用户当前闪卡测验的单词"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, pending_quiz_word) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET pending_quiz_word = excluded.pending_quiz_word
        """, (user_id, word.lower() if word else None))
        await db.commit()


# ----------------------------------------------------------------------
# 历史记录
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# 白名单
# ----------------------------------------------------------------------
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
            os.rename(whitelist_file, whitelist_file + ".bak")
        except Exception as e:
            logger.error("Migration failed: %s", e)


# ----------------------------------------------------------------------
# 词汇进度与暂停
# ----------------------------------------------------------------------
async def get_vocab_progress(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT word_index FROM vocab_progress WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def update_vocab_progress(user_id: int, word_index: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO vocab_progress (user_id, word_index, last_sent) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET word_index = excluded.word_index, last_sent = excluded.last_sent
        """, (user_id, word_index))
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
        await db.execute("""
            INSERT INTO vocab_progress (user_id, pause_until) VALUES (?, datetime('now', '+' || ? || ' days'))
            ON CONFLICT(user_id) DO UPDATE SET pause_until = datetime('now', '+' || ? || ' days')
        """, (user_id, days, days))
        await db.commit()

async def set_pause_until(user_id: int, pause_until_dt: datetime):
    """Pause pushes until a specific datetime."""
    dt_str = pause_until_dt.strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO vocab_progress (user_id, pause_until) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET pause_until = excluded.pause_until
        """, (user_id, dt_str))
        await db.commit()

async def cancel_pause(user_id: int):
    """Resume pushes immediately."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE vocab_progress SET pause_until = NULL WHERE user_id = ?
        """, (user_id,))
        await db.commit()

async def is_paused(user_id: int) -> bool:
    """Check if the user has paused pushes."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM vocab_progress WHERE user_id = ? AND pause_until > datetime('now')",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

async def get_pause_until(user_id: int) -> str | None:
    """获取暂停截止时间字符串"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT pause_until FROM vocab_progress WHERE user_id = ? AND pause_until > datetime('now')",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


# ----------------------------------------------------------------------
# 遗忘曲线 (SM-2) 调度
# ----------------------------------------------------------------------
async def record_new_word(user_id: int, word: str):
    """新词推送后录入复习库，初始默认 1 天后复习"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO word_schedule (user_id, word, next_review, interval, ease_factor, review_count)
            VALUES (?, ?, datetime('now', '+1 day'), 1, 2.5, 0)
        """, (user_id, word.lower()))
        await db.commit()

async def get_due_review_word(user_id: int) -> dict | None:
    """获取下一个到期复习的单词"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT word, interval, ease_factor, review_count
            FROM word_schedule
            WHERE user_id = ? AND next_review <= datetime('now')
            ORDER BY next_review ASC
            LIMIT 1
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "word": row[0],
                    "interval": row[1],
                    "ease_factor": row[2],
                    "review_count": row[3],
                }
            return None

async def update_word_review(user_id: int, word: str, grade: str) -> dict:
    """
    更新单词的复习状态 (简化版 SM-2 算法)
    grade: "good" (记住了), "fuzzy" (有点模糊), "forgot" (完全忘了)
    """
    word = word.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT interval, ease_factor, review_count
            FROM word_schedule
            WHERE user_id = ? AND word = ?
        """, (user_id, word)) as cursor:
            row = await cursor.fetchone()

        if row:
            interval, ease_factor, review_count = row
        else:
            interval, ease_factor, review_count = 1, 2.5, 0

        if grade == "good":
            # 记住了：间隔乘上难度因子，最长30天
            interval = max(1, min(30, int(round(interval * ease_factor))))
            ease_factor = min(2.5, ease_factor + 0.1)
            review_count += 1
        elif grade == "fuzzy":
            # 模糊：间隔保持最少1天，难度因子微降
            interval = max(1, interval)
            ease_factor = max(1.3, ease_factor - 0.15)
            review_count += 1
        else:  # "forgot"
            # 忘了：间隔重置为1天，难度因子大降
            interval = 1
            ease_factor = max(1.3, ease_factor - 0.3)
            review_count += 1

        await db.execute("""
            INSERT INTO word_schedule (user_id, word, next_review, interval, ease_factor, review_count, last_reviewed)
            VALUES (?, ?, datetime('now', '+' || ? || ' days'), ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, word) DO UPDATE SET
                next_review = datetime('now', '+' || excluded.interval || ' days'),
                interval = excluded.interval,
                ease_factor = excluded.ease_factor,
                review_count = excluded.review_count,
                last_reviewed = CURRENT_TIMESTAMP
        """, (user_id, word, interval, interval, ease_factor, review_count))
        await db.commit()

    # 触发连续打卡
    streak = await update_streak(user_id)
    return {
        "word": word,
        "interval": interval,
        "ease_factor": ease_factor,
        "review_count": review_count,
        "streak": streak,
    }


# ----------------------------------------------------------------------
# 连续学习打卡 (Streak) 与 统计 (Stats)
# ----------------------------------------------------------------------
async def update_streak(user_id: int) -> int:
    """打卡：更新连续天数"""
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_date, streak FROM learning_streak WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()

        if not row:
            new_streak = 1
            await db.execute(
                "INSERT INTO learning_streak (user_id, last_date, streak) VALUES (?, ?, ?)",
                (user_id, today_str, new_streak)
            )
        else:
            last_date, current_streak = row
            if last_date == today_str:
                return current_streak
            elif last_date == yesterday_str:
                new_streak = current_streak + 1
            else:
                new_streak = 1
            await db.execute(
                "UPDATE learning_streak SET last_date = ?, streak = ? WHERE user_id = ?",
                (today_str, new_streak, user_id)
            )
        await db.commit()
        return new_streak

async def get_stats(user_id: int, total_vocab: int = 5651) -> dict:
    """获取用户学习看板数据"""
    async with aiosqlite.connect(DB_PATH) as db:
        # 1. 词汇总进度
        async with db.execute("SELECT word_index FROM vocab_progress WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            word_index = row[0] if row else 0

        # 2. 进入复习计划总数
        async with db.execute("SELECT COUNT(1) FROM word_schedule WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            in_schedule = row[0] if row else 0

        # 3. 稳固掌握数（复习>=3次）
        async with db.execute("SELECT COUNT(1) FROM word_schedule WHERE user_id = ? AND review_count >= 3", (user_id,)) as cursor:
            row = await cursor.fetchone()
            mastered = row[0] if row else 0

        # 4. 到期待复习单词数
        async with db.execute("SELECT COUNT(1) FROM word_schedule WHERE user_id = ? AND next_review <= datetime('now')", (user_id,)) as cursor:
            row = await cursor.fetchone()
            due_count = row[0] if row else 0

        # 5. 打卡 streak
        async with db.execute("SELECT last_date, streak FROM learning_streak WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            streak = 0
            if row:
                last_date, s = row
                today_str = date.today().isoformat()
                yesterday_str = (date.today() - timedelta(days=1)).isoformat()
                if last_date in (today_str, yesterday_str):
                    streak = s

        # 6. 推送设置
        async with db.execute("SELECT push_mode FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            push_mode = row[0] if (row and row[0]) else "both"

        # 7. 暂停状态
        async with db.execute(
            "SELECT pause_until FROM vocab_progress WHERE user_id = ? AND pause_until > datetime('now')",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            pause_until = row[0] if row else None

    return {
        "word_index": word_index,
        "total_vocab": total_vocab,
        "in_schedule": in_schedule,
        "mastered": mastered,
        "due_count": due_count,
        "streak": streak,
        "push_mode": push_mode,
        "pause_until": pause_until,
    }

async def reset_vocab_progress_all():
    """重置所有用户的词汇进度（用于全量重新学习乱序词库）"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE vocab_progress SET word_index = 0, last_sent = NULL, pause_until = NULL")
        await db.execute("DELETE FROM word_schedule")
        await db.execute("UPDATE users SET pending_eval_word = NULL, pending_quiz_word = NULL")
        await db.commit()
    logger.info("All vocab progress, schedules, and pending words have been reset.")
