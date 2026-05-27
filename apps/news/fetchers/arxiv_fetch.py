from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import arxiv

from .base import Fetcher, NewsItem

log = logging.getLogger(__name__)


class ArxivFetcher(Fetcher):
    name = "arXiv AI"
    source_type = "arxiv"

    def __init__(self, hours: int = 72, max_results: int = 60) -> None:
        self.hours = hours
        self.max_results = max_results

    def _sync_fetch(self) -> list[NewsItem]:
        client = arxiv.Client(page_size=50, delay_seconds=3, num_retries=2)
        search = arxiv.Search(
            query="cat:cs.AI OR cat:cs.LG OR cat:cs.CL",
            max_results=self.max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.hours)
        items: list[NewsItem] = []
        try:
            for result in client.results(search):
                pub = result.published
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub < cutoff:
                    break
                arxiv_id = result.entry_id.rsplit("/", 1)[-1]
                items.append(NewsItem(
                    uid=f"arxiv:{arxiv_id}",
                    source=self.name,
                    source_type=self.source_type,
                    title=result.title.strip().replace("\n", " "),
                    url=result.entry_id,
                    published_at=pub,
                    content=(result.summary or "").strip()[:2000],
                    channel="ai",
                ))
        except Exception as e:
            log.warning("[arxiv] fetch failed: %s", e)
        return items

    async def fetch(self) -> list[NewsItem]:
        items = await asyncio.to_thread(self._sync_fetch)
        log.info("[arXiv] fetched %d items", len(items))
        return items
