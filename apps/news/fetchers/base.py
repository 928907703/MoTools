from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any


@dataclass
class NewsItem:
    uid: str
    source: str
    source_type: str
    title: str
    url: str
    published_at: datetime
    content: str = ""

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat()
        return d


def url_uid(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


class Fetcher:
    name: str

    async def fetch(self) -> list[NewsItem]:
        raise NotImplementedError
