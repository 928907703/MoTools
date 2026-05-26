from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
SUMMARY_ENABLED = os.getenv("SUMMARY_ENABLED", "true").lower() == "true"
FETCH_INTERVAL_HOURS = float(os.getenv("FETCH_INTERVAL_HOURS", "2"))
DB_PATH = Path(os.getenv("DB_PATH", "./data/news.db")).resolve()

CATEGORIES = ["模型发布", "融资动态", "政策法规", "研究论文", "产品应用", "其他"]


@dataclass
class SourceConfig:
    name: str
    kind: str           # rss / hn / arxiv / html
    source_type: str    # media / blog / cn / arxiv
    url: str = ""
    keyword_filter: tuple[str, ...] = ()


SOURCES: list[SourceConfig] = [
    SourceConfig("TechCrunch AI", "rss", "media",
                 "https://techcrunch.com/category/artificial-intelligence/feed/"),
    SourceConfig("The Verge AI", "rss", "media",
                 "https://www.theverge.com/rss/index.xml",
                 keyword_filter=("ai", "openai", "anthropic", "claude", "gpt",
                                 "gemini", "llm", "deepmind", "meta ai", "mistral",
                                 "machine learning", "neural", "chatbot", "copilot")),
    SourceConfig("MIT Tech Review AI", "rss", "media",
                 "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    SourceConfig("Hacker News", "hn", "media",
                 "https://hnrss.org/frontpage?q=AI+OR+LLM+OR+GPT+OR+Claude",
                 keyword_filter=("ai", "llm", "gpt", "claude", "openai", "anthropic",
                                 "gemini", "model", "neural", "deepmind", "mistral",
                                 "agent", "rag", "transformer")),
    SourceConfig("OpenAI Blog", "rss", "blog",
                 "https://openai.com/news/rss.xml"),
    SourceConfig("Google DeepMind", "rss", "blog",
                 "https://deepmind.google/blog/rss.xml"),
    SourceConfig("机器之心", "rss", "cn",
                 "https://www.jiqizhixin.com/rss"),
    SourceConfig("量子位", "rss", "cn",
                 "https://www.qbitai.com/feed"),
    SourceConfig("36氪 AI", "rss", "cn",
                 "https://36kr.com/feed-newsflash",
                 keyword_filter=("ai", "人工智能", "大模型", "llm", "gpt", "claude",
                                 "openai", "anthropic", "智能体", "agent", "机器人")),
    SourceConfig("arXiv AI", "arxiv", "arxiv"),
]

SOURCE_TYPE_LABEL = {
    "media": "媒体",
    "blog": "官方博客",
    "cn": "国内",
    "arxiv": "论文",
}

SOURCE_TYPE_COLOR = {
    "media": "bg-sky-100 text-sky-700",
    "blog": "bg-violet-100 text-violet-700",
    "cn": "bg-rose-100 text-rose-700",
    "arxiv": "bg-emerald-100 text-emerald-700",
}
