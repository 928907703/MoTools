from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import CHANNELS, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, SUMMARY_ENABLED
from db import fetch_pending_summaries, update_summary

log = logging.getLogger(__name__)

_SEM = asyncio.Semaphore(5)

_IMPORTANCE_TEXT_MAP = {
    "极高": 5, "重大": 5, "顶级": 5,
    "高": 4, "重要": 4,
    "中": 3, "中等": 3, "一般": 3, "普通": 3,
    "低": 2, "较低": 2,
    "很低": 1, "边缘": 1,
}


def _categories(channel: str) -> list[str]:
    return CHANNELS.get(channel, CHANNELS["ai"])["categories"]


def _system_prompt(channel: str) -> str:
    categories = _categories(channel)
    role = "AI 行业资讯编辑" if channel == "ai" else "区块链 RWA 资讯编辑"
    return (
        f"你是{role}。基于输入文章的标题与正文片段，返回严格 JSON。"
        "字段要求：\n"
        "- summary：80–120 字中文摘要，客观、信息密度高，不要冗余开场白和评价；\n"
        f"- category：**必须严格**等于以下其中一个值之一（不要使用其他词）：{' / '.join(categories)}；\n"
        "- importance：**必须是 1 到 5 的阿拉伯数字整数**（不是中文，不是字符串），5 表示行业重大事件，3 表示一般资讯，1 表示边缘信息。\n"
        "只输出 JSON 对象，不要任何 markdown、解释或代码块。"
    )


def _coerce_importance(val) -> int:
    if isinstance(val, (int, float)):
        return max(1, min(5, int(val)))
    if isinstance(val, str):
        s = val.strip()
        if s.isdigit():
            return max(1, min(5, int(s)))
        if s in _IMPORTANCE_TEXT_MAP:
            return _IMPORTANCE_TEXT_MAP[s]
    return 3


def _coerce_category(val, channel: str) -> str:
    categories = _categories(channel)
    if not isinstance(val, str):
        return "其他"
    s = val.strip()
    if s in categories:
        return s
    for cat in categories:
        if cat in s or s in cat:
            return cat
    aliases = {
        "发布": "模型发布", "开源": "模型发布", "新模型": "模型发布",
        "融资": "融资动态", "投资": "融资动态", "收购": "融资动态", "ipo": "融资动态",
        "监管": "政策法规", "政策": "政策法规", "法规": "政策法规",
        "论文": "研究论文", "研究": "研究论文", "学术": "研究论文",
        "产品": "产品应用", "应用": "产品应用", "工具": "产品应用",
        "代币化": "资产代币化", "tokenization": "资产代币化", "rwa": "资产代币化",
        "机构": "机构采用", "blackrock": "机构采用", "institution": "机构采用",
        "稳定币": "稳定币与支付", "stablecoin": "稳定币与支付", "payment": "稳定币与支付",
    }
    low = s.lower()
    for k, v in aliases.items():
        if k in low and v in categories:
            return v
    return "其他"


_AI_RULE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("模型发布", ["发布", "release", "launch", "推出", "open-sourced", "开源", "gpt-", "claude", "gemini", "llama", "qwen"]),
    ("融资动态", ["融资", "funding", "raises", "valuation", "估值", "投资", "ipo", "收购", "acquisition"]),
    ("政策法规", ["监管", "regulation", "policy", "法规", "政策", "executive order", "ban", "禁令", "compliance"]),
    ("研究论文", ["arxiv", "paper", "论文", "benchmark", "evaluation", "we propose", "we introduce"]),
    ("产品应用", ["产品", "app", "feature", "上线", "rollout", "用户", "users", "integration"]),
]

_RWA_RULE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("资产代币化", ["rwa", "real world asset", "tokenization", "tokenisation", "tokenized", "treasury", "buidl", "ondo", "centrifuge", "代币化", "现实世界资产", "国债"]),
    ("机构采用", ["blackrock", "jpmorgan", "franklin templeton", "institution", "机构", "银行", "asset manager"]),
    ("稳定币与支付", ["stablecoin", "stablecoins", "usdc", "usdt", "pyusd", "payment", "payments", "稳定币", "支付"]),
    ("融资动态", ["funding", "raises", "valuation", "融资", "投资", "收购", "acquisition"]),
    ("政策法规", ["regulation", "regulator", "sec", "cftc", "policy", "compliance", "监管", "政策", "法规"]),
    ("产品应用", ["launch", "product", "integration", "推出", "上线", "产品", "平台"]),
]


def _rule_classify(channel: str, source: str, source_type: str, title: str, content: str) -> tuple[str, int]:
    if source_type == "arxiv":
        return "研究论文", 3
    text = (title + " " + (content or "")).lower()
    rules = _RWA_RULE_KEYWORDS if channel == "rwa" else _AI_RULE_KEYWORDS
    for cat, words in rules:
        if any(w in text for w in words):
            return cat, 3
    return "其他", 2


def _client() -> AsyncOpenAI | None:
    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("sk-xxx"):
        return None
    return AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type(Exception))
async def _call_openai(client: AsyncOpenAI, channel: str, title: str, content: str) -> dict[str, Any]:
    payload = json.dumps({"title": title, "content": (content or "")[:2000]},
                         ensure_ascii=False)
    resp = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": _system_prompt(channel)},
            {"role": "user", "content": payload},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=400,
    )
    return json.loads(resp.choices[0].message.content or "{}")


async def _process_one(client: AsyncOpenAI | None, row: dict[str, Any]) -> None:
    title = row["title"]
    content = row.get("raw_content") or ""
    source = row["source"]
    source_type = row["source_type"]
    channel = row.get("channel") or "ai"

    if client is None or not SUMMARY_ENABLED:
        cat, imp = _rule_classify(channel, source, source_type, title, content)
        await update_summary(row["id"], "", cat, imp)
        return

    async with _SEM:
        try:
            data = await _call_openai(client, channel, title, content)
            summary = (data.get("summary") or "").strip()
            category = _coerce_category(data.get("category"), channel)
            importance = _coerce_importance(data.get("importance"))
            await update_summary(row["id"], summary, category, importance)
        except Exception as e:
            log.warning("OpenAI summarize failed (%s): %s", row["url"], e)
            cat, imp = _rule_classify(channel, source, source_type, title, content)
            await update_summary(row["id"], "", cat, imp)


async def summarize_pending(limit: int = 80) -> int:
    rows = await fetch_pending_summaries(limit=limit)
    if not rows:
        return 0
    client = _client()
    if client is None:
        log.info("OpenAI key not configured — falling back to rule classification for %d items",
                 len(rows))
    await asyncio.gather(*[_process_one(client, r) for r in rows])
    return len(rows)
