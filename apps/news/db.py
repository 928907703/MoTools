from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import aiosqlite

from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    raw_content TEXT,
    summary TEXT,
    category TEXT,
    importance INTEGER DEFAULT 3,
    summarized INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_published ON news_items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_category ON news_items(category);
CREATE INDEX IF NOT EXISTS idx_summarized ON news_items(summarized);
"""

_write_lock = asyncio.Lock()


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(_SCHEMA)
        await conn.commit()


async def insert_items(items: Iterable[dict[str, Any]]) -> int:
    items = list(items)
    if not items:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as conn:
            for it in items:
                cur = await conn.execute(
                    """INSERT OR IGNORE INTO news_items
                       (uid, source, source_type, title, url, published_at,
                        raw_content, summarized, fetched_at)
                       VALUES (?,?,?,?,?,?,?,0,?)""",
                    (it["uid"], it["source"], it["source_type"], it["title"],
                     it["url"], it["published_at"], it.get("content", ""), now),
                )
                if cur.rowcount > 0:
                    inserted += 1
            await conn.commit()
    return inserted


async def fetch_pending_summaries(limit: int = 80) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """SELECT id, source, source_type, title, url, raw_content
               FROM news_items WHERE summarized = 0
               ORDER BY published_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def update_summary(item_id: int, summary: str, category: str, importance: int) -> None:
    async with _write_lock:
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                """UPDATE news_items
                   SET summary = ?, category = ?, importance = ?, summarized = 1
                   WHERE id = ?""",
                (summary, category, importance, item_id),
            )
            await conn.commit()


async def query_news(category: str | None = None, hours: int = 168) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    sql = ("SELECT id, uid, source, source_type, title, url, published_at, "
           "summary, category, importance "
           "FROM news_items WHERE published_at >= ? ")
    params: list[Any] = [cutoff]
    if category and category != "全部":
        sql += "AND category = ? "
        params.append(category)
    sql += "ORDER BY published_at DESC LIMIT 500"

    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def source_counts() -> list[tuple[str, int]]:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT source, COUNT(*) FROM news_items GROUP BY source ORDER BY 2 DESC"
        )
        return list(await cur.fetchall())
