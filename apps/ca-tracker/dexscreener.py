"""DexScreener 链上数据抓取（免 key）。

返回价格/市值/FDV/流动性 + 名称/符号 + logo + 社交链接 + 上线时间。
holders 不提供。
文档: https://docs.dexscreener.com/api/reference
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

API = "https://api.dexscreener.com/latest/dex/tokens/{address}"

# DexScreener chainId -> 我们内部使用的链名
CHAIN_ALIASES = {
    "ethereum": "eth",
    "solana": "sol",
    "bsc": "bsc",
    "base": "base",
    "arbitrum": "arb",
    "polygon": "polygon",
}


async def fetch_token(address: str, chain: Optional[str] = None) -> Optional[dict[str, Any]]:
    """抓取某 CA 的市场数据。chain 用于在多链命中时优先选定链的池子。

    返回 dict（name/symbol/chain/price_usd/market_cap/fdv/liquidity_usd/pair_url），
    抓不到返回 None。
    """
    address = address.strip()
    url = API.format(address=address)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    pairs = payload.get("pairs") or []
    if not pairs:
        return None

    # 若指定链，优先选该链；否则选流动性最高的池子
    def liq(p: dict[str, Any]) -> float:
        return (p.get("liquidity") or {}).get("usd") or 0

    if chain:
        matched = [p for p in pairs if CHAIN_ALIASES.get(p.get("chainId", ""), p.get("chainId")) == chain]
        candidates = matched or pairs
    else:
        candidates = pairs

    best = max(candidates, key=liq)
    base = best.get("baseToken") or {}
    info = best.get("info") or {}
    chain_id = best.get("chainId", "")

    socials: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for s in info.get("socials") or []:
        url = s.get("url")
        stype = s.get("type")
        if url and url not in seen_urls:
            seen_urls.add(url)
            socials.append({"type": _infer_social_type(url, stype or "link"), "url": url})
    for w in info.get("websites") or []:
        url = w.get("url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            socials.append({"type": _infer_social_type(url, "website"), "url": url})

    # pairCreatedAt 在高流动池里偶尔缺失，跨所有 pair 取最早的（同一代币首次上 DEX 的时间）
    all_created = [int(p["pairCreatedAt"]) for p in pairs if p.get("pairCreatedAt")]
    pair_created_at = _ms_to_iso(min(all_created)) if all_created else None

    return {
        "address": address,
        "chain": CHAIN_ALIASES.get(chain_id, chain_id),
        "name": base.get("name"),
        "symbol": base.get("symbol"),
        "price_usd": _to_float(best.get("priceUsd")),
        "market_cap": _to_float(best.get("marketCap")),
        "fdv": _to_float(best.get("fdv")),
        "liquidity_usd": liq(best) or None,
        "pair_url": best.get("url"),
        "image_url": info.get("imageUrl"),
        "socials": socials,
        "pair_created_at": pair_created_at,
    }


def _infer_social_type(url: str, fallback: str) -> str:
    """从 URL host 推断社交平台类型。DexScreener 把 GitHub docs 这类统统标 website,
    这里通过域名匹配落到更具体的图标。"""
    u = (url or "").lower()
    if "github.com" in u or "github.io" in u:
        return "github"
    if "twitter.com" in u or "://x.com" in u or u.startswith("x.com"):
        return "twitter"
    if "t.me/" in u or "telegram.me" in u or "telegram.org" in u:
        return "telegram"
    if "discord.com" in u or "discord.gg" in u:
        return "discord"
    if "medium.com" in u:
        return "medium"
    if "reddit.com" in u:
        return "reddit"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return (fallback or "link").lower()


def _ms_to_iso(ms: Any) -> Optional[str]:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
