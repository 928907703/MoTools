"""SQLite 持久层（aiosqlite 异步）。"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "ca.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
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
    UNIQUE(user_id, address, chain)
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
    user_id  INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name     TEXT NOT NULL,
    category TEXT,
    color    TEXT
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

"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def connect() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH, timeout=30)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA busy_timeout = 30000")
    await db.execute("PRAGMA journal_mode = WAL")
    return db


async def _migrate_token_unique_scope(db: aiosqlite.Connection) -> None:
    indexes = await db.execute("PRAGMA index_list(tokens)")
    for index in await indexes.fetchall():
        if not index["unique"]:
            continue
        idx_name = index["name"]
        cols_cur = await db.execute(f"PRAGMA index_info({idx_name})")
        cols = [row["name"] for row in await cols_cur.fetchall()]
        if cols == ["address", "chain"]:
            await db.execute("PRAGMA foreign_keys = OFF")
            await db.execute(
                """CREATE TABLE IF NOT EXISTS tokens_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
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
                    UNIQUE(user_id, address, chain)
                )"""
            )
            await db.execute(
                """INSERT INTO tokens_new (
                    id, user_id, address, chain, name, symbol, first_seen_at, notes, rating,
                    image_url, socials_json, pair_created_at, created_at, updated_at
                )
                SELECT id, user_id, address, chain, name, symbol, first_seen_at, notes, rating,
                       image_url, socials_json, pair_created_at, created_at, updated_at
                FROM tokens"""
            )
            await db.execute("DROP TABLE tokens")
            await db.execute("ALTER TABLE tokens_new RENAME TO tokens")
            await db.execute("PRAGMA foreign_keys = ON")
            break


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
            "user_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
        ):
            try:
                await db.execute(f"ALTER TABLE tokens ADD COLUMN {col_def}")
            except aiosqlite.OperationalError:
                pass
        try:
            await db.execute("ALTER TABLE tags ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE tags ADD COLUMN color TEXT")
        except aiosqlite.OperationalError:
            pass
        await _migrate_token_unique_scope(db)
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_tags_user ON tags(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_sources_token ON sources(token_id)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_token ON snapshots(token_id)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_token_time ON snapshots(token_id, snapshot_at DESC, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_token_tags_token ON token_tags(token_id)",
            "CREATE INDEX IF NOT EXISTS idx_token_tags_tag ON token_tags(tag_id)",
        ):
            await db.execute(index_sql)
        await db.commit()
    finally:
        await db.close()


# ---------- auth ----------

async def create_user(db: aiosqlite.Connection, username: str, password_hash: str) -> int:
    cur = await db.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username.strip(), password_hash, now_iso()),
    )
    await db.commit()
    return cur.lastrowid


async def update_user_password(db: aiosqlite.Connection, user_id: int, password_hash: str) -> None:
    await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    await db.commit()


async def get_user_by_username(db: aiosqlite.Connection, username: str) -> Optional[aiosqlite.Row]:
    cur = await db.execute("SELECT * FROM users WHERE username = ?", (username.strip(),))
    return await cur.fetchone()


async def get_session_user(db: aiosqlite.Connection, token: str) -> Optional[aiosqlite.Row]:
    cur = await db.execute(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > ?""",
        (token, now_iso()),
    )
    return await cur.fetchone()


async def create_session(db: aiosqlite.Connection, token: str, user_id: int, expires_at: str) -> None:
    await db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, now_iso(), expires_at),
    )
    await db.commit()


async def delete_session(db: aiosqlite.Connection, token: str) -> None:
    await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
    await db.commit()


async def assign_unowned_data(db: aiosqlite.Connection, user_id: int) -> None:
    await db.execute("UPDATE tokens SET user_id = ? WHERE user_id IS NULL", (user_id,))
    await db.execute("UPDATE tags SET user_id = ? WHERE user_id IS NULL", (user_id,))
    await db.commit()


# ---------- tokens ----------

async def find_token(db: aiosqlite.Connection, user_id: int, address: str, chain: str) -> Optional[aiosqlite.Row]:
    cur = await db.execute(
        "SELECT * FROM tokens WHERE user_id = ? AND address = ? AND chain = ?", (user_id, address, chain)
    )
    return await cur.fetchone()


async def get_token(db: aiosqlite.Connection, user_id: int, token_id: int) -> Optional[aiosqlite.Row]:
    cur = await db.execute("SELECT * FROM tokens WHERE id = ? AND user_id = ?", (token_id, user_id))
    return await cur.fetchone()


async def create_token(db: aiosqlite.Connection, data: dict[str, Any]) -> int:
    ts = now_iso()
    cur = await db.execute(
        """INSERT INTO tokens (user_id, address, chain, name, symbol, first_seen_at, notes, rating,
                               image_url, socials_json, pair_created_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data["user_id"],
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
    """刷新展示元数据，只覆盖 DexScreener 返回的非空字段。"""
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


async def refresh_token_profile(db: aiosqlite.Connection, user_id: int, token_id: int, info: dict[str, Any]) -> None:
    """从 DexScreener 重新抓取币种基础信息。

    notes、first_seen_at、标签、来源这些用户维护内容不覆盖。"""
    socials = info.get("socials") or []
    await db.execute(
        """UPDATE tokens SET
             chain           = COALESCE(?, chain),
             name            = COALESCE(?, name),
             symbol          = COALESCE(?, symbol),
             image_url       = COALESCE(?, image_url),
             socials_json    = COALESCE(?, socials_json),
             pair_created_at = COALESCE(?, pair_created_at),
             updated_at      = ?
           WHERE id = ? AND user_id = ?""",
        (
            info.get("chain"),
            info.get("name"),
            info.get("symbol"),
            info.get("image_url"),
            __import__("json").dumps(socials, ensure_ascii=False) if socials else None,
            info.get("pair_created_at"),
            now_iso(),
            token_id,
            user_id,
        ),
    )
    await db.commit()


async def update_token(db: aiosqlite.Connection, user_id: int, token_id: int, data: dict[str, Any]) -> None:
    await db.execute(
        """UPDATE tokens SET name = ?, symbol = ?, notes = ?, rating = ?, first_seen_at = ?, updated_at = ?
           WHERE id = ? AND user_id = ?""",
        (
            data.get("name"),
            data.get("symbol"),
            data.get("notes"),
            data.get("rating"),
            data.get("first_seen_at"),
            now_iso(),
            token_id,
            user_id,
        ),
    )
    await db.commit()


async def delete_token(db: aiosqlite.Connection, user_id: int, token_id: int) -> None:
    await db.execute("DELETE FROM tokens WHERE id = ? AND user_id = ?", (token_id, user_id))
    await db.commit()


async def list_tokens(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    q: Optional[str] = None,
    chain: Optional[str] = None,
    tag_id: Optional[int] = None,
    min_rating: Optional[int] = None,
) -> list[dict[str, Any]]:
    sql = "SELECT DISTINCT t.* FROM tokens t"
    params: list[Any] = [user_id]
    where: list[str] = ["t.user_id = ?"]
    if tag_id:
        sql += " JOIN token_tags tt_filter ON tt_filter.token_id = t.id"
        where.append("tt_filter.tag_id = ?")
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
    sql += " ORDER BY t.created_at DESC, t.id DESC"

    rows = await (await db.execute(sql, params)).fetchall()
    result = [dict(r) for r in rows]
    if not result:
        return []

    token_ids = [r["id"] for r in result]
    placeholders = ",".join("?" for _ in token_ids)

    tags_by_token: dict[int, list[dict[str, Any]]] = {token_id: [] for token_id in token_ids}
    tag_rows = await (
        await db.execute(
            f"""SELECT tt.token_id, tg.* FROM token_tags tt
                JOIN tags tg ON tg.id = tt.tag_id
                WHERE tt.token_id IN ({placeholders})
                ORDER BY tg.category, tg.name""",
            token_ids,
        )
    ).fetchall()
    for row in tag_rows:
        item = dict(row)
        token_id = item.pop("token_id")
        tags_by_token.setdefault(token_id, []).append(item)

    latest_by_token: dict[int, dict[str, Any]] = {}
    latest_rows = await (
        await db.execute(
            f"""SELECT s.* FROM snapshots s
                JOIN (
                  SELECT token_id, MAX(id) AS max_id
                  FROM snapshots
                  WHERE token_id IN ({placeholders})
                  GROUP BY token_id
                ) latest ON latest.max_id = s.id""",
            token_ids,
        )
    ).fetchall()
    for row in latest_rows:
        item = dict(row)
        latest_by_token[item["token_id"]] = item

    from datetime import datetime, timedelta, timezone

    async def changes_for(hours: int) -> dict[int, Optional[float]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        old_rows = await (
            await db.execute(
                f"""SELECT s.* FROM snapshots s
                    JOIN (
                      SELECT token_id, MAX(id) AS max_id
                      FROM snapshots
                      WHERE token_id IN ({placeholders}) AND snapshot_at <= ?
                      GROUP BY token_id
                    ) old ON old.max_id = s.id""",
                [*token_ids, cutoff],
            )
        ).fetchall()
        old_by_token = {row["token_id"]: dict(row) for row in old_rows}
        changes: dict[int, Optional[float]] = {}
        for token_id in token_ids:
            latest = latest_by_token.get(token_id)
            old = old_by_token.get(token_id)
            if not latest or not old or not latest.get("market_cap") or not old.get("market_cap"):
                changes[token_id] = None
                continue
            changes[token_id] = (latest["market_cap"] - old["market_cap"]) / old["market_cap"] * 100
        return changes

    change_1h = await changes_for(1)
    change_24h = await changes_for(24)

    for item in result:
        token_id = item["id"]
        item["tags"] = tags_by_token.get(token_id, [])
        item["latest_snapshot"] = latest_by_token.get(token_id)
        item["change_1h"] = change_1h.get(token_id)
        item["change_24h"] = change_24h.get(token_id)
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

async def list_tags(db: aiosqlite.Connection, user_id: int) -> list[dict[str, Any]]:
    cur = await db.execute("SELECT * FROM tags WHERE user_id = ? ORDER BY category, name", (user_id,))
    return [dict(r) for r in await cur.fetchall()]


async def create_tag(
    db: aiosqlite.Connection,
    user_id: int,
    name: str,
    category: Optional[str],
    color: Optional[str] = None,
) -> int:
    clean_name = name.strip()
    row = await (
        await db.execute("SELECT id FROM tags WHERE user_id = ? AND name = ?", (user_id, clean_name))
    ).fetchone()
    if row:
        return row["id"]
    cur = await db.execute(
        "INSERT INTO tags (user_id, name, category, color) VALUES (?, ?, ?, ?)",
        (user_id, clean_name, category, normalize_tag_color(color)),
    )
    await db.commit()
    return cur.lastrowid


def normalize_tag_color(color: Optional[str]) -> str:
    color = (color or "").strip()
    if len(color) == 7 and color.startswith("#"):
        try:
            int(color[1:], 16)
            return color.lower()
        except ValueError:
            pass
    return "#0ea5e9"


async def update_tag(
    db: aiosqlite.Connection,
    user_id: int,
    tag_id: int,
    name: str,
    category: Optional[str],
    color: Optional[str] = None,
) -> None:
    await db.execute(
        "UPDATE tags SET name = ?, category = ?, color = ? WHERE id = ? AND user_id = ?",
        (name.strip(), category, normalize_tag_color(color), tag_id, user_id),
    )
    await db.commit()


async def delete_tag(db: aiosqlite.Connection, user_id: int, tag_id: int) -> None:
    await db.execute("DELETE FROM tags WHERE id = ? AND user_id = ?", (tag_id, user_id))
    await db.commit()


async def get_token_tags(db: aiosqlite.Connection, token_id: int) -> list[dict[str, Any]]:
    cur = await db.execute(
        """SELECT tg.* FROM tags tg JOIN token_tags tt ON tt.tag_id = tg.id
           WHERE tt.token_id = ? ORDER BY tg.category, tg.name""",
        (token_id,),
    )
    return [dict(r) for r in await cur.fetchall()]


async def set_token_tags(db: aiosqlite.Connection, user_id: int, token_id: int, tag_ids: list[int]) -> None:
    owned = await (await db.execute("SELECT id FROM tokens WHERE id = ? AND user_id = ?", (token_id, user_id))).fetchone()
    if not owned:
        return
    await db.execute("DELETE FROM token_tags WHERE token_id = ?", (token_id,))
    for tid in tag_ids:
        cur = await db.execute("SELECT id FROM tags WHERE id = ? AND user_id = ?", (tid, user_id))
        if await cur.fetchone():
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
        "SELECT * FROM snapshots WHERE token_id = ? ORDER BY snapshot_at DESC, id DESC", (token_id,)
    )
    return [dict(r) for r in await cur.fetchall()]


async def latest_snapshot(db: aiosqlite.Connection, token_id: int) -> Optional[dict[str, Any]]:
    cur = await db.execute(
        "SELECT * FROM snapshots WHERE token_id = ? ORDER BY snapshot_at DESC, id DESC LIMIT 1", (token_id,)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def first_snapshot(db: aiosqlite.Connection, token_id: int) -> Optional[dict[str, Any]]:
    cur = await db.execute(
        "SELECT * FROM snapshots WHERE token_id = ? ORDER BY snapshot_at ASC, id ASC LIMIT 1", (token_id,)
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
        "SELECT market_cap FROM snapshots WHERE token_id = ? ORDER BY snapshot_at DESC, id DESC LIMIT 1",
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


async def list_refresh_targets(db: aiosqlite.Connection, user_id: int) -> list[dict[str, Any]]:
    cur = await db.execute(
        "SELECT id, address, chain FROM tokens WHERE user_id = ? ORDER BY created_at DESC, id DESC", (user_id,)
    )
    return [dict(r) for r in await cur.fetchall()]


async def distinct_chains(db: aiosqlite.Connection, user_id: int) -> list[str]:
    cur = await db.execute("SELECT DISTINCT chain FROM tokens WHERE user_id = ? ORDER BY chain", (user_id,))
    return [r["chain"] for r in await cur.fetchall()]
