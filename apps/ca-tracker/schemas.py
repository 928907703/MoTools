"""Pydantic 模型（表单/响应）。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TokenIn(BaseModel):
    address: str
    chain: str
    name: Optional[str] = None
    symbol: Optional[str] = None
    notes: Optional[str] = None
    rating: Optional[int] = None
    first_seen_at: Optional[str] = None


class SourceIn(BaseModel):
    kol: Optional[str] = None
    group_name: Optional[str] = None
    link: Optional[str] = None
    posted_at: Optional[str] = None


class TagIn(BaseModel):
    name: str
    category: Optional[str] = None
