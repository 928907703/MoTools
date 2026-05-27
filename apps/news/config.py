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

CHANNELS = {
    "ai": {
        "label": "AI News",
        "title": "AI News Hub",
        "description": "AI 行业新闻、模型发布、融资与研究动态",
        "categories": ["模型发布", "融资动态", "政策法规", "研究论文", "产品应用", "其他"],
    },
    "rwa": {
        "label": "RWA News",
        "title": "RWA News Hub",
        "description": "区块链 RWA、资产代币化、稳定币、机构采用与监管动态",
        "categories": ["资产代币化", "机构采用", "稳定币与支付", "融资动态", "政策法规", "产品应用", "其他"],
    },
}

CATEGORIES = CHANNELS["ai"]["categories"]


@dataclass
class SourceConfig:
    name: str
    kind: str           # rss / hn / arxiv / html
    source_type: str    # media / blog / cn / arxiv / crypto
    url: str = ""
    keyword_filter: tuple[str, ...] = ()
    channel: str = "ai"


AI_KEYWORDS = (
    "ai", "openai", "anthropic", "claude", "gpt", "gemini", "llm",
    "deepmind", "meta ai", "mistral", "machine learning", "neural",
    "chatbot", "copilot", "agent", "rag", "transformer",
)

RWA_KEYWORDS = (
    "rwa", "real world asset", "real-world asset", "tokenized asset",
    "tokenization", "tokenisation", "tokenized treasury", "treasury",
    "onchain finance", "on-chain finance", "private credit", "real estate token",
    "asset-backed", "blackrock b series", "buidl", "ondo", "centrifuge",
    "maple finance", "gold token", "stablecoin", "stablecoins",
    "usdc", "usdt", "paypal usd", "pyusd", "payment stablecoin",
    "tokenized fund", "tokenized money market", "代币化", "现实世界资产",
    "稳定币", "国债代币", "链上金融",
)


SOURCES: list[SourceConfig] = [
    SourceConfig("TechCrunch AI", "rss", "media",
                 "https://techcrunch.com/category/artificial-intelligence/feed/",
                 channel="ai"),
    SourceConfig("The Verge AI", "rss", "media",
                 "https://www.theverge.com/rss/index.xml",
                 keyword_filter=AI_KEYWORDS,
                 channel="ai"),
    SourceConfig("MIT Tech Review AI", "rss", "media",
                 "https://www.technologyreview.com/topic/artificial-intelligence/feed",
                 channel="ai"),
    SourceConfig("Hacker News", "hn", "media",
                 "https://hnrss.org/frontpage?q=AI+OR+LLM+OR+GPT+OR+Claude",
                 keyword_filter=AI_KEYWORDS,
                 channel="ai"),
    SourceConfig("OpenAI Blog", "rss", "blog",
                 "https://openai.com/news/rss.xml",
                 channel="ai"),
    SourceConfig("Google DeepMind", "rss", "blog",
                 "https://deepmind.google/blog/rss.xml",
                 channel="ai"),
    SourceConfig("机器之心", "rss", "cn",
                 "https://www.jiqizhixin.com/rss",
                 channel="ai"),
    SourceConfig("量子位", "rss", "cn",
                 "https://www.qbitai.com/feed",
                 channel="ai"),
    SourceConfig("36氪 AI", "rss", "cn",
                 "https://36kr.com/feed-newsflash",
                 keyword_filter=("ai", "人工智能", "大模型", "llm", "gpt", "claude",
                                 "openai", "anthropic", "智能体", "agent", "机器人"),
                 channel="ai"),
    SourceConfig("arXiv AI", "arxiv", "arxiv", channel="ai"),

    SourceConfig("CoinDesk RWA", "rss", "crypto",
                 "https://www.coindesk.com/arc/outboundfeeds/rss/",
                 keyword_filter=RWA_KEYWORDS,
                 channel="rwa"),
    SourceConfig("Cointelegraph RWA", "rss", "crypto",
                 "https://cointelegraph.com/rss",
                 keyword_filter=RWA_KEYWORDS,
                 channel="rwa"),
    SourceConfig("Decrypt RWA", "rss", "crypto",
                 "https://decrypt.co/feed",
                 keyword_filter=RWA_KEYWORDS,
                 channel="rwa"),
    SourceConfig("The Block RWA", "rss", "crypto",
                 "https://www.theblock.co/rss.xml",
                 keyword_filter=RWA_KEYWORDS,
                 channel="rwa"),
    SourceConfig("RWA.xyz", "rss", "blog",
                 "https://www.rwa.xyz/rss.xml",
                 keyword_filter=RWA_KEYWORDS,
                 channel="rwa"),
]

SOURCE_TYPE_LABEL = {
    "media": "媒体",
    "blog": "官方/研究",
    "cn": "国内",
    "arxiv": "论文",
    "crypto": "加密媒体",
}

SOURCE_TYPE_COLOR = {
    "media": "bg-sky-100 text-sky-700",
    "blog": "bg-violet-100 text-violet-700",
    "cn": "bg-rose-100 text-rose-700",
    "arxiv": "bg-emerald-100 text-emerald-700",
    "crypto": "bg-amber-100 text-amber-800",
}
