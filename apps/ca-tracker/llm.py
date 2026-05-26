"""可选 LLM 辅助标签建议（Anthropic Claude）。

设计：
- 无 ANTHROPIC_API_KEY 时优雅停用（is_enabled() 为 False，suggest_tags 返回 None）。
- 静态系统提示 + 标签体系打 cache_control（prefix 较短时可能不达最低缓存阈值，不影响功能）。
- 动态部分（CA、用户已有标签、分析文本）放在用户消息里，不进缓存前缀。
- 通过 ANTHROPIC_BASE_URL 支持自定义/兼容网关。
"""
from __future__ import annotations

import json
import os
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """你是加密代币标签助手。用户在追踪 CA（合约地址），需要你给出简洁的归类标签。

参考赛道分类（仅参考，可组合，可超出此范围）：
- 类型：meme、utility、governance、stable
- 赛道：AI、DeFi、L1、L2、RWA、DePIN、GameFi、SocialFi、Infra、Privacy、Restaking、Modular、Oracle、Bridge
- 链：solana、ethereum、base、bsc、arbitrum、polygon
- 状态：pre-launch、new-launch、established、suspicious、rugged

规则：
1. 优先复用用户已有标签集合里语义匹配的标签（精确匹配标签名）。
2. 仅当已有标签都不合适时再提出新标签。新标签使用 kebab-case 小写英文。
3. 标签 2-5 个，最相关的排前面。
4. reason 用中文一句话（不超过 60 字），说明依据（如代币性质、来源 KOL 风格、链上特征）。
5. 信息不足就少给标签，不要硬凑。"""


class TagSuggestion(BaseModel):
    suggested_tags: list[str] = Field(description="建议的标签名列表，2-5 个")
    reason: str = Field(description="一句话说明依据，中文，不超过 60 字")


_client: Optional[anthropic.AsyncAnthropic] = None


def is_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _get_client() -> Optional[anthropic.AsyncAnthropic]:
    global _client
    if not is_enabled():
        return None
    if _client is None:
        base_url = os.getenv("ANTHROPIC_BASE_URL") or None
        _client = anthropic.AsyncAnthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            base_url=base_url,
        )
    return _client


async def suggest_tags(
    *,
    address: str,
    chain: str,
    name: Optional[str],
    symbol: Optional[str],
    analysis: Optional[str],
    existing_tags: list[str],
) -> Optional[dict]:
    """给出标签建议。未启用或调用失败返回 None。"""
    client = _get_client()
    if client is None:
        return None

    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)

    user_payload = {
        "address": address,
        "chain": chain,
        "name": name,
        "symbol": symbol,
        "analysis": analysis or "",
        "existing_tags": existing_tags,
    }
    user_msg = (
        "请为以下 CA 建议标签：\n```json\n"
        + json.dumps(user_payload, ensure_ascii=False, indent=2)
        + "\n```"
    )

    try:
        resp = await client.messages.parse(
            model=model,
            max_tokens=512,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_msg}],
            output_format=TagSuggestion,
        )
    except (anthropic.APIError, anthropic.APIConnectionError):
        return None

    parsed: Optional[TagSuggestion] = resp.parsed_output
    if parsed is None:
        return None
    return {"tags": parsed.suggested_tags, "reason": parsed.reason}
