from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import FETCH_INTERVAL_HOURS, SOURCES
from db import insert_items
from fetchers import ArxivFetcher, Fetcher, MetaAIFetcher, RSSFetcher
from llm.summarize import summarize_pending

log = logging.getLogger(__name__)

_running = asyncio.Lock()


def _build_fetchers() -> list[Fetcher]:
    fs: list[Fetcher] = []
    for s in SOURCES:
        if s.kind in ("rss", "hn"):
            fs.append(RSSFetcher(s.name, s.url, s.source_type, s.keyword_filter, s.channel))
        elif s.kind == "html":
            fs.append(MetaAIFetcher())
        elif s.kind == "arxiv":
            fs.append(ArxivFetcher())
    return fs


async def run_fetch_cycle() -> dict[str, int]:
    if _running.locked():
        log.info("fetch cycle already running, skip")
        return {"fetched": 0, "inserted": 0, "summarized": 0, "skipped": 1}

    async with _running:
        log.info("=== fetch cycle start ===")
        fetchers = _build_fetchers()
        results = await asyncio.gather(
            *[f.fetch() for f in fetchers], return_exceptions=True
        )

        all_items: list[dict] = []
        for f, r in zip(fetchers, results):
            if isinstance(r, Exception):
                log.warning("[%s] exception: %s", f.name, r)
                continue
            all_items.extend(item.to_row() for item in r)

        inserted = await insert_items(all_items)
        log.info("fetched=%d inserted=%d", len(all_items), inserted)

        summarized = await summarize_pending(limit=100)
        log.info("summarized=%d", summarized)
        log.info("=== fetch cycle done ===")
        return {"fetched": len(all_items), "inserted": inserted,
                "summarized": summarized, "skipped": 0}


def start_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(
        run_fetch_cycle,
        trigger=IntervalTrigger(hours=FETCH_INTERVAL_HOURS),
        next_run_time=datetime.now(timezone.utc),
        id="fetch_cycle",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    log.info("scheduler started, interval=%sh", FETCH_INTERVAL_HOURS)
    return sched
