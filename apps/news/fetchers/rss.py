from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from time import mktime
from typing import Iterable

import feedparser
import httpx

from .base import Fetcher, NewsItem, url_uid

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/127.0 Safari/537.36")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _to_dt(struct_time) -> datetime:
    if struct_time is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(mktime(struct_time), tz=timezone.utc)


class RSSFetcher(Fetcher):
    def __init__(self, name: str, url: str, source_type: str,
                 keyword_filter: Iterable[str] = ()) -> None:
        self.name = name
        self.url = url
        self.source_type = source_type
        self.keyword_filter = tuple(k.lower() for k in keyword_filter)

    def _match_keyword(self, text: str) -> bool:
        if not self.keyword_filter:
            return True
        lower = text.lower()
        return any(k in lower for k in self.keyword_filter)

    async def fetch(self) -> list[NewsItem]:
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                         headers={"User-Agent": _UA}) as client:
                resp = await client.get(self.url)
                resp.raise_for_status()
                content = resp.content
        except Exception as e:
            log.warning("[%s] HTTP failed: %s", self.name, e)
            return []

        try:
            parsed = feedparser.parse(content)
        except Exception as e:
            log.warning("[%s] parse failed: %s", self.name, e)
            return []

        items: list[NewsItem] = []
        for entry in parsed.entries[:50]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue
            summary = _strip_html(entry.get("summary") or entry.get("description") or "")
            if not self._match_keyword(title + " " + summary):
                continue
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            items.append(NewsItem(
                uid=url_uid(link),
                source=self.name,
                source_type=self.source_type,
                title=title,
                url=link,
                published_at=_to_dt(pub),
                content=summary[:2000],
            ))
        log.info("[%s] fetched %d items", self.name, len(items))
        return items
