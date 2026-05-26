"""DexScreener market data client."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

TOKEN_API = "https://api.dexscreener.com/latest/dex/tokens/{address}"
BATCH_TOKEN_API = "https://api.dexscreener.com/tokens/v1/{chain_id}/{addresses}"
BATCH_SIZE = 30

# DexScreener chainId -> internal chain name.
CHAIN_ALIASES = {
    "ethereum": "eth",
    "solana": "sol",
    "bsc": "bsc",
    "base": "base",
    "arbitrum": "arb",
    "polygon": "polygon",
}

DEX_CHAIN_BY_INTERNAL = {
    "eth": "ethereum",
    "sol": "solana",
    "bsc": "bsc",
    "base": "base",
    "arb": "arbitrum",
    "polygon": "polygon",
}


async def fetch_token(address: str, chain: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Fetch one token with the broad token endpoint.

    This is kept for lookup/import flows because it can search across chains.
    """
    address = address.strip()
    url = TOKEN_API.format(address=address)
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

    best = _select_best_pair(pairs, address, chain)
    return _pair_to_info(best, address)


async def fetch_tokens_batch(tokens: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Fetch many tokens in chain/address batches.

    DexScreener supports up to 30 comma-separated token addresses per request.
    Returns a map: token_id -> market info.
    """
    results: dict[int, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for token in tokens:
        chain = (token.get("chain") or "").strip()
        dex_chain = DEX_CHAIN_BY_INTERNAL.get(chain, chain)
        if dex_chain and token.get("address"):
            grouped.setdefault(dex_chain, []).append(token)

    async with httpx.AsyncClient(timeout=20) as client:
        for dex_chain, chain_tokens in grouped.items():
            for chunk in _chunks(chain_tokens, BATCH_SIZE):
                batch = await _fetch_batch_chunk(client, dex_chain, chunk)
                results.update(batch)
    return results


async def _fetch_batch_chunk(
    client: httpx.AsyncClient,
    dex_chain: str,
    tokens: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    addresses = ",".join(t["address"].strip() for t in tokens if t.get("address"))
    if not addresses:
        return {}

    try:
        resp = await client.get(BATCH_TOKEN_API.format(chain_id=dex_chain, addresses=addresses))
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError):
        return {}

    pairs = payload if isinstance(payload, list) else payload.get("pairs") or []
    if not pairs:
        return {}

    results: dict[int, dict[str, Any]] = {}
    for token in tokens:
        address = token["address"]
        chain = token.get("chain")
        matched = [p for p in pairs if _pair_matches_token(p, address)]
        if not matched:
            continue
        best = _select_best_pair(matched, address, chain)
        info = _pair_to_info(best, address)
        if info:
            results[token["id"]] = info
    return results


def _select_best_pair(
    pairs: list[dict[str, Any]],
    address: str,
    chain: Optional[str] = None,
) -> dict[str, Any]:
    def liq(p: dict[str, Any]) -> float:
        return (p.get("liquidity") or {}).get("usd") or 0

    candidates = pairs
    if chain:
        matched_chain = [
            p
            for p in pairs
            if CHAIN_ALIASES.get(p.get("chainId", ""), p.get("chainId")) == chain
        ]
        candidates = matched_chain or pairs

    matched_token = [p for p in candidates if _pair_matches_token(p, address)]
    candidates = matched_token or candidates
    return max(candidates, key=liq)


def _pair_matches_token(pair: dict[str, Any], address: str) -> bool:
    target = _norm_addr(address)
    base = _norm_addr((pair.get("baseToken") or {}).get("address"))
    quote = _norm_addr((pair.get("quoteToken") or {}).get("address"))
    return target in {base, quote}


def _pair_to_info(pair: dict[str, Any], address: str) -> dict[str, Any]:
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    token_side = quote if _norm_addr(quote.get("address")) == _norm_addr(address) else base
    info = pair.get("info") or {}
    chain_id = pair.get("chainId", "")

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

    pair_created_at = _ms_to_iso(pair.get("pairCreatedAt"))

    return {
        "address": address,
        "chain": CHAIN_ALIASES.get(chain_id, chain_id),
        "name": token_side.get("name"),
        "symbol": token_side.get("symbol"),
        "price_usd": _to_float(pair.get("priceUsd")),
        "market_cap": _to_float(pair.get("marketCap")),
        "fdv": _to_float(pair.get("fdv")),
        "liquidity_usd": (pair.get("liquidity") or {}).get("usd") or None,
        "pair_url": pair.get("url"),
        "image_url": info.get("imageUrl"),
        "socials": socials,
        "pair_created_at": pair_created_at,
    }


def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _norm_addr(address: Any) -> str:
    return str(address or "").strip().lower()


def _infer_social_type(url: str, fallback: str) -> str:
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
