from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import Fetcher, NewsItem, url_uid

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/127.0 Safari/537.36")


class MetaAIFetcher(Fetcher):
    name = "Meta AI Blog"
    source_type = "blog"
    url = "https://ai.meta.com/blog/"

    async def fetch(self) -> list[NewsItem]:
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                         headers={"User-Agent": _UA}) as client:
                resp = await client.get(self.url)
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            log.warning("[Meta AI] HTTP failed: %s", e)
            return []

        soup = BeautifulSoup(html, "lxml")
        items: list[NewsItem] = []
        seen: set[str] = set()
        now = datetime.now(timezone.utc)

        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            if "/blog/" not in href:
                continue
            full = urljoin(self.url, href)
            if full in seen or full.rstrip("/") == self.url.rstrip("/"):
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 8:
                continue
            seen.add(full)
            items.append(NewsItem(
                uid=url_uid(full),
                source=self.name,
                source_type=self.source_type,
                title=title,
                url=full,
                published_at=now,
                content="",
            ))
            if len(items) >= 20:
                break

        log.info("[Meta AI] fetched %d items", len(items))
        return items
