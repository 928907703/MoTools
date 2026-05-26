"""FastAPI 入口：路由 + 模板渲染。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import dexscreener
import llm
import scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

load_dotenv()

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def _age_days(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except ValueError:
        return None


def _parse_socials(raw: Optional[str]) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


templates.env.filters["age_days"] = _age_days
templates.env.filters["socials"] = _parse_socials


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    task = asyncio.create_task(scheduler.run_periodic_snapshots())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="CA Tracker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def _prefix(request: Request) -> str:
    return request.headers.get("x-forwarded-prefix", "").rstrip("/")


def _url(request: Request, path: str = "") -> str:
    prefix = _prefix(request)
    if not path:
        return prefix or "/"
    return f"{prefix}{path}"


def _context(request: Request, **values):
    values["prefix"] = _prefix(request)
    return values


def _parse_int(v: Optional[str]) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except ValueError:
        return None


# ---------- 列表页 ----------

@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: Optional[str] = None,
    chain: Optional[str] = None,
    tag_id: Optional[str] = None,
    min_rating: Optional[str] = None,
):
    """筛选页。tag_id/min_rating 用 str 接，避免表单留空时空字符串 422。"""
    tag_id_int = _parse_int(tag_id)
    min_rating_int = _parse_int(min_rating)
    conn = await db.connect()
    try:
        tokens = await db.list_tokens(
            conn,
            q=q or None,
            chain=chain or None,
            tag_id=tag_id_int,
            min_rating=min_rating_int,
        )
        tags = await db.list_tags(conn)
        chains = await db.distinct_chains(conn)
    finally:
        await conn.close()
    return templates.TemplateResponse(
        request,
        "index.html",
        _context(
            request,
            tokens=tokens,
            tags=tags,
            chains=chains,
            q=q or "",
            chain=chain or "",
            tag_id=tag_id_int,
            min_rating=min_rating_int,
            llm_enabled=llm.is_enabled(),
        ),
    )


# ---------- 录入 ----------

@app.get("/new", response_class=HTMLResponse)
async def new_form(request: Request, address: Optional[str] = None, chain: Optional[str] = None):
    conn = await db.connect()
    try:
        tags = await db.list_tags(conn)
    finally:
        await conn.close()
    return templates.TemplateResponse(
        request,
        "new.html",
        _context(
            request,
            tags=tags,
            prefill=None,
            address=address or "",
            chain=chain or "",
            duplicate=None,
            llm_enabled=llm.is_enabled(),
            llm_suggestion=None,
        ),
    )


@app.post("/new/lookup", response_class=HTMLResponse)
async def new_lookup(
    request: Request,
    address: str = Form(...),
    chain: str = Form(""),
):
    """抓 DexScreener 数据 + 查重 + (可选) LLM 标签建议，回填表单。"""
    address = address.strip()
    chain = chain.strip() or None
    info = await dexscreener.fetch_token(address, chain)
    resolved_chain = (info.get("chain") if info else None) or chain or ""

    conn = await db.connect()
    try:
        tags = await db.list_tags(conn)
        duplicate = None
        if resolved_chain:
            existing = await db.find_token(conn, address, resolved_chain)
            if existing:
                duplicate = {
                    "id": existing["id"],
                    "rating": existing["rating"],
                    "notes": existing["notes"],
                }
    finally:
        await conn.close()

    suggestion = None
    if llm.is_enabled() and info:
        suggestion = await llm.suggest_tags(
            address=address,
            chain=resolved_chain or "",
            name=info.get("name"),
            symbol=info.get("symbol"),
            analysis=None,
            existing_tags=[t["name"] for t in tags],
        )

    return templates.TemplateResponse(
        request,
        "new.html",
        _context(
            request,
            tags=tags,
            prefill=info,
            address=address,
            chain=resolved_chain,
            duplicate=duplicate,
            llm_enabled=llm.is_enabled(),
            llm_suggestion=suggestion,
        ),
    )


@app.post("/new")
async def new_submit(
    request: Request,
    address: str = Form(...),
    chain: str = Form(...),
    name: str = Form(""),
    symbol: str = Form(""),
    notes: str = Form(""),
    rating: str = Form(""),
    first_seen_at: str = Form(""),
    kol: str = Form(""),
    group_name: str = Form(""),
    link: str = Form(""),
    posted_at: str = Form(""),
    tag_ids: list[int] = Form(default=[]),
    new_tags: str = Form(""),
    price_usd: str = Form(""),
    market_cap: str = Form(""),
    fdv: str = Form(""),
    liquidity_usd: str = Form(""),
    image_url: str = Form(""),
    socials_json: str = Form(""),
    pair_created_at: str = Form(""),
):
    address = address.strip()
    chain = chain.strip()
    if not address or not chain:
        raise HTTPException(400, "address and chain required")

    conn = await db.connect()
    try:
        existing = await db.find_token(conn, address, chain)
        if existing:
            return RedirectResponse(_url(request, f"/token/{existing['id']}"), status_code=303)

        token_id = await db.create_token(
            conn,
            {
                "address": address,
                "chain": chain,
                "name": name.strip() or None,
                "symbol": symbol.strip() or None,
                "notes": notes.strip() or None,
                "rating": _parse_int(rating),
                "first_seen_at": first_seen_at.strip() or None,
                "image_url": image_url.strip() or None,
                "socials_json": socials_json.strip() or None,
                "pair_created_at": pair_created_at.strip() or None,
            },
        )

        if any([kol, group_name, link, posted_at]):
            await db.add_source(
                conn,
                token_id,
                {
                    "kol": kol.strip() or None,
                    "group_name": group_name.strip() or None,
                    "link": link.strip() or None,
                    "posted_at": posted_at.strip() or None,
                },
            )

        tag_id_set: list[int] = list(tag_ids)
        for raw in new_tags.split(","):
            n = raw.strip()
            if n:
                tag_id_set.append(await db.create_tag(conn, n, None))
        if tag_id_set:
            await db.set_token_tags(conn, token_id, tag_id_set)

        if any([price_usd, market_cap, fdv, liquidity_usd]):
            def f(v: str) -> Optional[float]:
                try:
                    return float(v) if v.strip() else None
                except ValueError:
                    return None
            await db.add_snapshot(
                conn,
                token_id,
                {
                    "price_usd": f(price_usd),
                    "market_cap": f(market_cap),
                    "fdv": f(fdv),
                    "liquidity_usd": f(liquidity_usd),
                    "source": "dexscreener",
                },
            )
    finally:
        await conn.close()

    return RedirectResponse(_url(request, f"/token/{token_id}"), status_code=303)


# ---------- 详情 ----------

@app.get("/token/{token_id}", response_class=HTMLResponse)
async def token_detail(request: Request, token_id: int):
    conn = await db.connect()
    try:
        token = await db.get_token(conn, token_id)
        if not token:
            raise HTTPException(404)
        token_d = dict(token)
        token_d["tags"] = await db.get_token_tags(conn, token_id)
        token_d["change_1h"] = await db.market_cap_change(conn, token_id, 1)
        token_d["change_24h"] = await db.market_cap_change(conn, token_id, 24)
        sources = await db.get_sources(conn, token_id)
        snapshots = await db.get_snapshots(conn, token_id)
        all_tags = await db.list_tags(conn)
    finally:
        await conn.close()
    return templates.TemplateResponse(
        request,
        "detail.html",
        _context(
            request,
            token=token_d,
            sources=sources,
            snapshots=snapshots,
            all_tags=all_tags,
            selected_tag_ids={t["id"] for t in token_d["tags"]},
        ),
    )


@app.post("/token/{token_id}/edit")
async def token_edit(
    request: Request,
    token_id: int,
    name: str = Form(""),
    symbol: str = Form(""),
    notes: str = Form(""),
    rating: str = Form(""),
    first_seen_at: str = Form(""),
    tag_ids: list[int] = Form(default=[]),
    new_tags: str = Form(""),
):
    conn = await db.connect()
    try:
        t = await db.get_token(conn, token_id)
        if not t:
            raise HTTPException(404)
        await db.update_token(
            conn,
            token_id,
            {
                "name": name.strip() or None,
                "symbol": symbol.strip() or None,
                "notes": notes.strip() or None,
                "rating": _parse_int(rating),
                "first_seen_at": first_seen_at.strip() or t["first_seen_at"],
            },
        )
        tag_id_set: list[int] = list(tag_ids)
        for raw in new_tags.split(","):
            n = raw.strip()
            if n:
                tag_id_set.append(await db.create_tag(conn, n, None))
        await db.set_token_tags(conn, token_id, tag_id_set)
    finally:
        await conn.close()
    return RedirectResponse(_url(request, f"/token/{token_id}"), status_code=303)


@app.post("/token/{token_id}/source")
async def token_add_source(
    request: Request,
    token_id: int,
    kol: str = Form(""),
    group_name: str = Form(""),
    link: str = Form(""),
    posted_at: str = Form(""),
):
    conn = await db.connect()
    try:
        if not await db.get_token(conn, token_id):
            raise HTTPException(404)
        await db.add_source(
            conn,
            token_id,
            {
                "kol": kol.strip() or None,
                "group_name": group_name.strip() or None,
                "link": link.strip() or None,
                "posted_at": posted_at.strip() or None,
            },
        )
    finally:
        await conn.close()
    return RedirectResponse(_url(request, f"/token/{token_id}"), status_code=303)


@app.post("/token/{token_id}/snapshot")
async def token_refresh_snapshot(request: Request, token_id: int):
    conn = await db.connect()
    try:
        token = await db.get_token(conn, token_id)
        if not token:
            raise HTTPException(404)
        info = await dexscreener.fetch_token(token["address"], token["chain"])
        if info:
            await db.add_snapshot(
                conn,
                token_id,
                {
                    "price_usd": info.get("price_usd"),
                    "market_cap": info.get("market_cap"),
                    "fdv": info.get("fdv"),
                    "liquidity_usd": info.get("liquidity_usd"),
                    "source": "dexscreener",
                },
            )
            socials = info.get("socials") or []
            await db.update_token_metadata(
                conn,
                token_id,
                image_url=info.get("image_url"),
                socials_json=json.dumps(socials, ensure_ascii=False) if socials else None,
                pair_created_at=info.get("pair_created_at"),
            )
    finally:
        await conn.close()
    return RedirectResponse(_url(request, f"/token/{token_id}"), status_code=303)


@app.post("/token/{token_id}/delete")
async def token_delete(request: Request, token_id: int):
    conn = await db.connect()
    try:
        await db.delete_token(conn, token_id)
    finally:
        await conn.close()
    return RedirectResponse(_url(request), status_code=303)


# ---------- 标签 ----------

@app.get("/tags", response_class=HTMLResponse)
async def tags_page(request: Request):
    conn = await db.connect()
    try:
        tags = await db.list_tags(conn)
    finally:
        await conn.close()
    return templates.TemplateResponse(request, "tags.html", _context(request, tags=tags))


@app.post("/tags")
async def tags_create(request: Request, name: str = Form(...), category: str = Form("")):
    conn = await db.connect()
    try:
        await db.create_tag(conn, name, category.strip() or None)
    finally:
        await conn.close()
    return RedirectResponse(_url(request, "/tags"), status_code=303)


@app.post("/tags/{tag_id}/update")
async def tags_update(request: Request, tag_id: int, name: str = Form(...), category: str = Form("")):
    conn = await db.connect()
    try:
        await db.update_tag(conn, tag_id, name, category.strip() or None)
    finally:
        await conn.close()
    return RedirectResponse(_url(request, "/tags"), status_code=303)


@app.post("/tags/{tag_id}/delete")
async def tags_delete(request: Request, tag_id: int):
    conn = await db.connect()
    try:
        await db.delete_tag(conn, tag_id)
    finally:
        await conn.close()
    return RedirectResponse(_url(request, "/tags"), status_code=303)


# ---------- 健康检查 ----------

@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True, "llm_enabled": llm.is_enabled()})
