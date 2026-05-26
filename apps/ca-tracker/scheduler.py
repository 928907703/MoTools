"""后台定时任务：每小时遍历所有 token 抓 DexScreener，写 snapshot + 同步元数据。"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import db
import dexscreener

log = logging.getLogger("ca-tracker.scheduler")

DEFAULT_INTERVAL = 3600  # 秒
INITIAL_DELAY = 30        # 启动后多少秒开始第一轮
PER_TOKEN_DELAY = 0.5     # 每个 token 之间错开，避免速率限制


async def _snapshot_all() -> None:
    conn = await db.connect()
    try:
        cur = await conn.execute("SELECT id, address, chain FROM tokens")
        rows = await cur.fetchall()
    finally:
        await conn.close()

    if not rows:
        log.info("no tokens to snapshot")
        return

    log.info("snapshotting %d tokens", len(rows))
    success = 0
    for r in rows:
        info = await dexscreener.fetch_token(r["address"], r["chain"])
        if info:
            conn = await db.connect()
            try:
                await db.add_snapshot(
                    conn,
                    r["id"],
                    {
                        "price_usd": info.get("price_usd"),
                        "market_cap": info.get("market_cap"),
                        "fdv": info.get("fdv"),
                        "liquidity_usd": info.get("liquidity_usd"),
                        "source": "dexscreener",
                    },
                )
                socials = info.get("socials") or []
                await db.update_token_metadata(
                    conn,
                    r["id"],
                    image_url=info.get("image_url"),
                    socials_json=json.dumps(socials, ensure_ascii=False) if socials else None,
                    pair_created_at=info.get("pair_created_at"),
                )
                success += 1
            finally:
                await conn.close()
        await asyncio.sleep(PER_TOKEN_DELAY)
    log.info("snapshot done: %d/%d ok", success, len(rows))


async def run_periodic_snapshots() -> None:
    """运行直到被取消。lifespan 关闭时 task.cancel() 会触发 CancelledError 跳出。"""
    interval = int(os.getenv("SNAPSHOT_INTERVAL_SECONDS", DEFAULT_INTERVAL))
    log.info("scheduler starting (interval=%ss)", interval)
    try:
        await asyncio.sleep(INITIAL_DELAY)
        while True:
            try:
                await _snapshot_all()
            except Exception as e:
                log.exception("snapshot round failed: %s", e)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("scheduler stopped")
        raise
