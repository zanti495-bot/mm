import time
from pathlib import Path

import aiosqlite

DATABASE_PATH = Path(__file__).resolve().parent.parent / "bot_data.sqlite3"


async def initialize() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT NOT NULL,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                is_blocked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS requests_created_at_idx
                ON requests(created_at);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        cursor = await database.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "is_blocked" not in columns:
            await database.execute(
                "ALTER TABLE users ADD COLUMN is_blocked "
                "INTEGER NOT NULL DEFAULT 0"
            )
        await database.commit()


async def register_user(user_id: int, username: str | None, full_name: str) -> None:
    now = time.time()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            """
            INSERT INTO users(user_id, username, full_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                last_seen = excluded.last_seen,
                is_blocked = 0
            """,
            (user_id, username, full_name, now, now),
        )
        await database.commit()


async def create_request(user_id: int) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            "INSERT INTO requests(user_id, status, created_at) VALUES (?, ?, ?)",
            (user_id, "started", time.time()),
        )
        await database.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an ID for the request.")
        return cursor.lastrowid


async def finish_request(request_id: int, status: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            "UPDATE requests SET status = ? WHERE id = ?",
            (status, request_id),
        )
        await database.commit()


async def get_maintenance() -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            "SELECT value FROM settings WHERE key = 'maintenance'"
        )
        row = await cursor.fetchone()
        return bool(row and row[0] == "1")


async def set_maintenance(enabled: bool) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            """
            INSERT INTO settings(key, value) VALUES ('maintenance', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ("1" if enabled else "0",),
        )
        await database.commit()


async def get_stats(since: float) -> dict[str, int]:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS sent,
                SUM(
                    CASE
                        WHEN status NOT IN ('started', 'success') THEN 1
                        ELSE 0
                    END
                ) AS failed,
                COUNT(DISTINCT user_id) AS users
            FROM requests
            WHERE created_at >= ?
            """,
            (since,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("SQLite did not return request statistics.")
        return {
            "total": row[0] or 0,
            "sent": row[1] or 0,
            "failed": row[2] or 0,
            "users": row[3] or 0,
        }


async def export_users() -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        cursor = await database.execute(
            """
            SELECT
                user_id,
                username,
                full_name,
                first_seen,
                last_seen,
                is_blocked
            FROM users
            ORDER BY last_seen DESC
            """
        )
        return [dict(row) for row in await cursor.fetchall()]


async def broadcast_user_ids() -> list[int]:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            """
            SELECT user_id
            FROM users
            WHERE is_blocked = 0
            ORDER BY user_id
            """
        )
        return [row[0] for row in await cursor.fetchall()]


async def mark_user_blocked(user_id: int) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            "UPDATE users SET is_blocked = 1 WHERE user_id = ?",
            (user_id,),
        )
        await database.commit()
