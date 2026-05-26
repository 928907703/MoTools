"""SQLite 持久层（aiosqlite 异步）。"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "ca.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    address         TEXT NOT NULL,
    chain           TEXT NOT NULL,
    name            TEXT,
    symbol          TEXT,
    first_seen_at   TEXT,
    notes           TEXT,
    rating          INTEGER,
    image_url       TEXT,
    socials_json    TEXT,
    pair_created_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(address, chain)
);

CREATE TABLE IF NOT EXISTS sources (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id   INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    kol        TEXT,
    group_name TEXT,
    link       TEXT,
    posted_at  TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE,
    category TEXT
);

CREATE TABLE IF NOT EXISTS token_tags (
    token_id INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (token_id, tag_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id      INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    price_usd     REAL,
    market_cap    REAL,
    fdv           REAL,
    liquidity_usd REAL,
    holders       INTEGER,
    snapshot_at   TEXT NOT NULL,
    source        TEXT
);

CREATE INDEX IF NOT EXISTS idx_sources_token ON sources(token_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_token ON snapshots(token_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def connect() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    return db


async def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = await connect()
    try:
        await db.executescript(SCHEMA)
        # 幂等迁移：老 DB 缺这些列时补上；存在则忽略
        for col_def in (
            "image_url TEXT",
            "socials_json TEXT",
            "pair_created_at TEXT",
        ):
            try:
                await db.execute(f"ALTER TABLE tokens ADD COLUMN {col_def}")
            except aiosqlite.OperationalError:
                pass
        await db.commit()
    finally:
        await db.close()


# ---------- tokens ----------

async def find_token(db: aiosqlite.Connection, address: str, chain: str) -> Optional[aiosqlite.Row]:
    cur = await db.execute(
        "SELECT * FROM tokens WHERE address = ? AND chain = ?", (address, chain)
    )
    return await cur.fetchone()


async def get_token(db: aiosqlite.Connection, token_id: int) -> Optional[aiosqlite.Row]:
    cur = await db.execute("SELECT * FROM tokens WHERE id = ?", (token_id,))
    return await cur.fetchone()


async def create_token(db: aiosqlite.Connection, data: dict[str, Any]) -> int:
    ts = now_iso()
    cur = await db.execute(
        """INSERT INTO tokens (address, chain, name, symbol, first_seen_at, notes, rating,
                               image_url, socials_json, pair_created_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data["address"],
            data["chain"],
            data.get("name"),
            data.get("symbol"),
            data.get("first_seen_at") or ts,
            data.get("notes"),
            data.get("rating"),
            data.get("image_url"),
            data.get("socials_json"),
            data.get("pair_created_at"),
            ts,
            ts,
        ),
    )
    await db.commit()
    return cur.lastrowid


async def update_token_metadata(
    db: aiosqlite.Connection,
    token_id: int,
    *,
    image_url: Optional[str],
    socials_json: Optional[str],
    pair_created_at: Optional[str],
) -> None:
    """从 DexScreener 刷新元数据，不动 name/symbol/notes/rating（用户可能已编辑过）。

    只覆盖非 None 的字段，避免抓不到时把已有 logo 抹掉。"""
    await db.execute(
        """UPDATE tokens SET
             image_url       = COALESCE(?, image_url),
             socials_json    = COALESCE(?, socials_json),
             pair_created_at = COALESCE(?, pair_created_at),
             updated_at      = ?
           WHERE id = ?""",
        (image_url, socials_json, pair_created_at, now_iso(), token_id),
    )
    await db.commit()


async def update_token(db: aiosqlite.Connection, token_id: int, data: dict[str, Any]) -> None:
    await db.execute(
        """UPDATE tokens SET name = ?, symbol = ?, notes = ?, rating = ?, first_seen_at = ?, updated_at = ?
           WHERE id = ?""",
        (
            data.get("name"),
            data.get("symbol"),
            data.get("notes"),
            data.get("rating"),
            data.get("first_seen_at"),
            now_iso(),
            token_id,
        ),
    )
    await db.commit()


async def delete_token(db: aiosqlite.Connection, token_id: int) -> None:
    await db.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
    await db.commit()


async def list_tokens(
    db: aiosqlite.Connection,
    *,
    q: Optional[str] = None,
    chain: Optional[str] = None,
    tag_id: Optional[int] = None,
    min_rating: Optional[int] = None,
) -> list[dict[str, Any]]:
    sql = "SELECT DISTINCT t.* FROM tokens t"
    params: list[Any] = []
    where: list[str] = []
    if tag_id:
        sql += " JOIN token_tags tt ON tt.token_id = t.id"
        where.append("tt.tag_id = ?")
        params.append(tag_id)
    if q:
        where.append("(t.address LIKE ? OR t.name LIKE ? OR t.symbol LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    if chain:
        where.append("t.chain = ?")
        params.append(chain)
    if min_rating is not None:
        where.append("t.rating >= ?")
        params.append(min_rating)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.updated_at DESC"
    cur = await db.execute(sql, params)
    rows = await cur.fetchall()
    result: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["tags"] = await get_token_tags(db, r["id"])
        d["latest_snapshot"] = await latest_snapshot(db, r["id"])
        d["change_1h"] = await market_cap_change(db, r["id"], 1)
        d["change_24h"] = await market_cap_change(db, r["id"], 24)
        result.append(d)
    return result


# ---------- sources ----------

async def add_source(db: aiosqlite.Connection, token_id: int, data: dict[str, Any]) -> None:
    await db.execute(
        "INSERT INTO sources (token_id, kol, group_name, link, posted_at) VALUES (?, ?, ?, ?, ?)",
        (token_id, data.get("kol"), data.get("group_name"), data.get("link"), data.get("posted_at") or now_iso()),
    )
    await db.commit()


async def get_sources(db: aiosqlite.Connection, token_id: int) -> list[dict[str, Any]]:
    cur = await db.execute(
        "SELECT * FROM sources WHERE token_id = ? ORDER BY posted_at DESC", (token_id,)
    )
    return [dict(r) for r in await cur.fetchall()]


# ---------- tags ----------

async def list_tags(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    cur = await db.execute("SELECT * FROM tags ORDER BY category, name")
    return [dict(r) for r in await cur.fetchall()]


async def create_tag(db: aiosqlite.Connection, name: str, category: Optional[str]) -> int:
    cur = await db.execute(
        "INSERT OR IGNORE INTO tags (name, category) VALUES (?, ?)", (name.strip(), category)
    )
    await db.commit()
    if cur.lastrowid:
        return cur.lastrowid
    row = await (await db.execute("SELECT id FROM tags WHERE name = ?", (name.strip(),))).fetchone()
    return row["id"]


async def update_tag(db: aiosqlite.Connection, tag_id: int, name: str, category: Optional[str]) -> None:
    await db.execute("UPDATE tags SET name = ?, category = ? WHERE id = ?", (name.strip(), category, tag_id))
    await db.commit()


async def delete_tag(db: aiosqlite.Connection, tag_id: int) -> None:
    await db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    await db.commit()


async def get_token_tags(db: aiosqlite.Connection, token_id: int) -> list[dict[str, Any]]:
    cur = await db.execute(
        """SELECT tg.* FROM tags tg JOIN token_tags tt ON tt.tag_id = tg.id
           WHERE tt.token_id = ? ORDER BY tg.category, tg.name""",
        (token_id,),
    )
    return [dict(r) for r in await cur.fetchall()]


async def set_token_tags(db: aiosqlite.Connection, token_id: int, tag_ids: list[int]) -> None:
    await db.execute("DELETE FROM token_tags WHERE token_id = ?", (token_id,))
    for tid in tag_ids:
        await db.execute(
            "INSERT OR IGNORE INTO token_tags (token_id, tag_id) VALUES (?, ?)", (token_id, tid)
        )
    await db.commit()


# ---------- snapshots ----------

async def add_snapshot(db: aiosqlite.Connection, token_id: int, data: dict[str, Any]) -> None:
    await db.execute(
        """INSERT INTO snapshots (token_id, price_usd, market_cap, fdv, liquidity_usd, holders, snapshot_at, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            token_id,
            data.get("price_usd"),
            data.get("market_cap"),
            data.get("fdv"),
            data.get("liquidity_usd"),
            data.get("holders"),
            data.get("snapshot_at") or now_iso(),
            data.get("source") or "dexscreener",
        ),
    )
    await db.commit()


async def get_snapshots(db: aiosqlite.Connection, token_id: int) -> list[dict[str, Any]]:
    cur = await db.execute(
        "SELECT * FROM snapshots WHERE token_id = ? ORDER BY snapshot_at DESC", (token_id,)
    )
    return [dict(r) for r in await cur.fetchall()]


async def latest_snapshot(db: aiosqlite.Connection, token_id: int) -> Optional[dict[str, Any]]:
    cur = await db.execute(
        "SELECT * FROM snapshots WHERE token_id = ? ORDER BY snapshot_at DESC LIMIT 1", (token_id,)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def market_cap_change(
    db: aiosqlite.Connection, token_id: int, hours: int
) -> Optional[float]:
    """返回 hours 小时窗口的市值涨幅百分比。数据不足返回 None。

    取最新 snapshot 和距离 (now - hours) 最近、不晚于该时刻的那条 snapshot。
    两端任一缺市值或市值为 0,返回 None。
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    cur = await db.execute(
        "SELECT market_cap FROM snapshots WHERE token_id = ? ORDER BY snapshot_at DESC LIMIT 1",
        (token_id,),
    )
    latest = await cur.fetchone()
    if not latest or not latest["market_cap"]:
        return None

    cur = await db.execute(
        """SELECT market_cap FROM snapshots
           WHERE token_id = ? AND snapshot_at <= ?
           ORDER BY snapshot_at DESC LIMIT 1""",
        (token_id, cutoff),
    )
    old = await cur.fetchone()
    if not old or not old["market_cap"]:
        return None

    return (latest["market_cap"] - old["market_cap"]) / old["market_cap"] * 100


async def distinct_chains(db: aiosqlite.Connection) -> list[str]:
    cur = await db.execute("SELECT DISTINCT chain FROM tokens ORDER BY chain")
    return [r["chain"] for r in await cur.fetchall()]
