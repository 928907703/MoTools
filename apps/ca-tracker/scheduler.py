"""Background tasks for refreshing DexScreener market data."""
from __future__ import annotations

import asyncio
import json
import logging
import os

import db
import dexscreener

log = logging.getLogger("ca-tracker.scheduler")

DEFAULT_INTERVAL = 10
INITIAL_DELAY = 30


async def _write_snapshot(token: dict, info: dict) -> None:
    conn = await db.connect()
    try:
        await db.add_snapshot(
            conn,
            token["id"],
            {
                "price_usd": info.get("price_usd"),
                "market_cap": info.get("market_cap"),
                "fdv": info.get("fdv"),
                "liquidity_usd": info.get("liquidity_usd"),
                "source": "dexscreener",
            },
        )
        socials = info.get("socials") or []
        if token.get("user_id"):
            await db.refresh_token_profile(conn, token["user_id"], token["id"], info)
        else:
            await db.update_token_metadata(
                conn,
                token["id"],
                image_url=info.get("image_url"),
                socials_json=json.dumps(socials, ensure_ascii=False) if socials else None,
                pair_created_at=info.get("pair_created_at"),
            )
    finally:
        await conn.close()


async def _list_targets() -> list[dict]:
    conn = await db.connect()
    try:
        cur = await conn.execute("SELECT id, user_id, address, chain FROM tokens ORDER BY id")
        rows = await cur.fetchall()
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def _snapshot_all() -> None:
    targets = await _list_targets()
    if not targets:
        log.info("no tokens to refresh")
        return

    infos = await dexscreener.fetch_tokens_batch(targets)
    success = 0
    for token in targets:
        info = infos.get(token["id"])
        if not info:
            continue
        await _write_snapshot(token, info)
        success += 1

    log.info("batch refresh done: %d/%d ok", success, len(targets))


async def run_periodic_snapshots() -> None:
    """Run until cancelled by FastAPI lifespan shutdown."""
    interval = int(os.getenv("SNAPSHOT_INTERVAL_SECONDS", DEFAULT_INTERVAL))
    log.info("scheduler starting (batch interval=%ss)", interval)
    try:
        await asyncio.sleep(INITIAL_DELAY)
        while True:
            try:
                await _snapshot_all()
            except Exception as e:
                log.exception("batch refresh failed: %s", e)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("scheduler stopped")
        raise
