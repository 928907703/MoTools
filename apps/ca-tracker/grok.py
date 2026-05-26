"""Optional Grok analysis for newly added CA tokens."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4"

SYSTEM_PROMPT = """You are a crypto token research assistant. Analyze the DexScreener data cautiously.
Return the answer in Simplified Chinese.
Rules:
1. Do not promise profit and do not give buy/sell instructions.
2. Focus on token overview, liquidity/market-cap structure, launch age, risks, and follow-up checks.
3. If data is insufficient, say so clearly. Do not invent on-chain facts.
4. Output 4-6 concise bullet points, each under 45 Chinese characters."""


def is_enabled() -> bool:
    return bool(os.getenv("GROK_API_KEY"))


def analyzed_at_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def analyze_token(*, address: str, chain: str, info: dict[str, Any]) -> Optional[dict[str, str]]:
    """Return {analysis, analyzed_at}; disabled or failed returns None."""
    api_key = os.getenv("GROK_API_KEY")
    if not api_key:
        return None

    base_url = (os.getenv("GROK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    model = os.getenv("GROK_MODEL", DEFAULT_MODEL)
    payload = {
        "address": address,
        "chain": chain,
        "name": info.get("name"),
        "symbol": info.get("symbol"),
        "price_usd": info.get("price_usd"),
        "market_cap": info.get("market_cap"),
        "fdv": info.get("fdv"),
        "liquidity_usd": info.get("liquidity_usd"),
        "pair_created_at": info.get("pair_created_at"),
        "pair_url": info.get("pair_url"),
        "socials": info.get("socials") or [],
    }

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "temperature": 0.2,
                    "max_tokens": 700,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": "Analyze this CA:\n```json\n"
                            + json.dumps(payload, ensure_ascii=False, indent=2)
                            + "\n```",
                        },
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None

    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not content:
        return None
    return {"analysis": content, "analyzed_at": analyzed_at_iso()}
