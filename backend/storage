from __future__ import annotations
import aiosqlite
from typing import Optional
from datetime import datetime

DB_PATH = "logs.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    chat_id TEXT,
    query TEXT,
    answer TEXT,
    top_context TEXT,
    rating INTEGER
);
"""

async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_TABLE_SQL)
        await db.commit()

async def insert_log(chat_id: str, query: str, answer: str, top_context: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        ts = datetime.utcnow().isoformat()
        cursor = await db.execute(
            "INSERT INTO logs(ts, chat_id, query, answer, top_context) VALUES (?, ?, ?, ?, ?)",
            (ts, chat_id, query, answer, top_context),
        )
        await db.commit()
        return cursor.lastrowid

async def set_rating_by_id(row_id: int, rating: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE logs SET rating = ? WHERE id = ?", (rating, row_id))
        await db.commit()
