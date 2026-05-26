"""FastAPI 入口：路由 + 模板渲染。"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

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

SESSION_COOKIE = "motools_session"
SESSION_DAYS = 30
DEFAULT_USERNAME = os.getenv("CA_ADMIN_USERNAME", "moshimo")
DEFAULT_PASSWORD = os.getenv("CA_ADMIN_PASSWORD", "moshimo-change-me")

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


DISPLAY_TZ = timezone(timedelta(hours=8))


def _parse_socials(raw: Optional[str]) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _to_local_dt(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(DISPLAY_TZ)
    except ValueError:
        return None


def _local_time(iso: Optional[str]) -> str:
    dt = _to_local_dt(iso)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _datetime_local_value(iso: Optional[str]) -> str:
    dt = _to_local_dt(iso)
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""


templates.env.filters["age_days"] = _age_days
templates.env.filters["socials"] = _parse_socials
templates.env.filters["local_time"] = _local_time
templates.env.filters["datetime_local"] = _datetime_local_value


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    await _ensure_initial_user()
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



def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, salt, expected = password_hash.split("$", 2)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    actual = _hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(actual, expected)


async def _ensure_initial_user() -> None:
    conn = await db.connect()
    try:
        user = await db.get_user_by_username(conn, DEFAULT_USERNAME)
        if user is None:
            user_id = await db.create_user(conn, DEFAULT_USERNAME, _hash_password(DEFAULT_PASSWORD))
        else:
            user_id = user["id"]
            if DEFAULT_PASSWORD and DEFAULT_PASSWORD != "moshimo-change-me":
                await db.update_user_password(conn, user_id, _hash_password(DEFAULT_PASSWORD))
        await db.assign_unowned_data(conn, user_id)
    finally:
        await conn.close()


async def _current_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    conn = await db.connect()
    try:
        user = await db.get_session_user(conn, token)
        return dict(user) if user else None
    finally:
        await conn.close()


async def _require_user(request: Request) -> dict:
    user = await _current_user(request)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": _url(request, "/login")})
    return user


def _parse_int(v: Optional[str]) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _normalize_datetime_input(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DISPLAY_TZ)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return value



# ---------- 登录 ----------

@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str = ""):
    user = await _current_user(request)
    if user:
        return RedirectResponse(_url(request), status_code=303)
    return templates.TemplateResponse(request, "login.html", _context(request, error=error))


async def _create_session_response(request: Request, conn, user_id: int) -> RedirectResponse:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    await db.create_session(conn, token, user_id, expires)
    resp = RedirectResponse(_url(request), status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=True)
    return resp


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = await db.connect()
    try:
        user = await db.get_user_by_username(conn, username)
        if not user or not _verify_password(password, user["password_hash"]):
            return RedirectResponse(_url(request, "/login?error=1"), status_code=303)
        return await _create_session_response(request, conn, user["id"])
    finally:
        await conn.close()


@app.get("/register", response_class=HTMLResponse)
async def register_form(request: Request, error: str = ""):
    user = await _current_user(request)
    if user:
        return RedirectResponse(_url(request), status_code=303)
    return templates.TemplateResponse(request, "register.html", _context(request, error=error))


@app.post("/register")
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    username = username.strip()
    if len(username) < 3 or len(password) < 6:
        return RedirectResponse(_url(request, "/register?error=invalid"), status_code=303)
    if password != password_confirm:
        return RedirectResponse(_url(request, "/register?error=mismatch"), status_code=303)

    conn = await db.connect()
    try:
        if await db.get_user_by_username(conn, username):
            return RedirectResponse(_url(request, "/register?error=exists"), status_code=303)
        user_id = await db.create_user(conn, username, _hash_password(password))
        return await _create_session_response(request, conn, user_id)
    finally:
        await conn.close()


@app.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        conn = await db.connect()
        try:
            await db.delete_session(conn, token)
        finally:
            await conn.close()
    resp = RedirectResponse(_url(request, "/login"), status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------- 列表页 ----------

@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: Optional[str] = None,
    chain: Optional[str] = None,
    tag_id: Optional[str] = None,
    view: str = "list",
    columns: Optional[list[str]] = Query(default=None),
    refreshed: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=10, le=100),
):
    user = await _require_user(request)
    tag_id_int = _parse_int(tag_id)
    active_view = "columns" if view == "columns" else "list"
    conn = await db.connect()
    try:
        all_tokens = await db.list_tokens(
            conn,
            user_id=user["id"],
            q=q or None,
            chain=chain or None,
            tag_id=tag_id_int,
        )
        tags = await db.list_tags(conn, user["id"])
        chains = await db.distinct_chains(conn, user["id"])
        total = len(all_tokens)
        total_pages = max(1, (total + per_page - 1) // per_page)
        current_page = min(page, total_pages)
        start = (current_page - 1) * per_page
        tokens = all_tokens[start:start + per_page]

        column_ids = [_parse_int(v) for v in (columns or [])]
        column_ids = [v for v in column_ids if v]
        if active_view == "columns" and not column_ids:
            column_ids = [t["id"] for t in tags]
        selected_column_set = set(column_ids)
        column_tags = [t for t in tags if t["id"] in selected_column_set]
        column_groups = []
        for tag in column_tags:
            column_groups.append({
                "tag": tag,
                "tokens": [token for token in tokens if any(tt["id"] == tag["id"] for tt in token["tags"])],
            })
    finally:
        await conn.close()

    def page_url(target_page: int) -> str:
        params: list[tuple[str, str]] = [
            ("page", str(target_page)),
            ("per_page", str(per_page)),
        ]
        if q:
            params.append(("q", q))
        if chain:
            params.append(("chain", chain))
        if tag_id_int:
            params.append(("tag_id", str(tag_id_int)))
        if active_view == "columns":
            params.append(("view", "columns"))
            for col_id in column_ids:
                params.append(("columns", str(col_id)))
        return f"{_url(request)}?{urlencode(params)}"

    pagination = {
        "page": current_page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "start": start + 1 if total else 0,
        "end": min(start + per_page, total),
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
        "prev_url": page_url(current_page - 1) if current_page > 1 else "",
        "next_url": page_url(current_page + 1) if current_page < total_pages else "",
        "pages": [
            {"num": n, "url": page_url(n), "current": n == current_page}
            for n in range(max(1, current_page - 2), min(total_pages, current_page + 2) + 1)
        ],
    }

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
            view=active_view,
            selected_column_ids=selected_column_set,
            column_groups=column_groups,
            pagination=pagination,
            refreshed=_parse_int(refreshed),
            llm_enabled=llm.is_enabled(),
            user=user,
        ),
    )


# ---------- 录入 ----------

@app.get("/new", response_class=HTMLResponse)
async def new_form(request: Request, address: Optional[str] = None, chain: Optional[str] = None):
    user = await _require_user(request)
    conn = await db.connect()
    try:
        tags = await db.list_tags(conn, user["id"])
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
            user=user,
        ),
    )


@app.post("/new/lookup", response_class=HTMLResponse)
async def new_lookup(
    request: Request,
    address: str = Form(...),
    chain: str = Form(""),
):
    """抓 DexScreener 数据 + 查重 + (可选) LLM 标签建议，回填表单。"""
    user = await _require_user(request)
    address = address.strip()
    chain = chain.strip() or None
    info = await dexscreener.fetch_token(address, chain)
    resolved_chain = (info.get("chain") if info else None) or chain or ""

    conn = await db.connect()
    try:
        tags = await db.list_tags(conn, user["id"])
        duplicate = None
        if resolved_chain:
            existing = await db.find_token(conn, user["id"], address, resolved_chain)
            if existing:
                duplicate = {
                    "id": existing["id"],
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
            user=user,
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
    first_seen_at: str = Form(""),
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
    user = await _require_user(request)
    address = address.strip()
    chain = chain.strip()
    if not address or not chain:
        raise HTTPException(400, "address and chain required")

    conn = await db.connect()
    try:
        existing = await db.find_token(conn, user["id"], address, chain)
        if existing:
            return RedirectResponse(_url(request, f"/token/{existing['id']}"), status_code=303)

        token_id = await db.create_token(
            conn,
            {
                "user_id": user["id"],
                "address": address,
                "chain": chain,
                "name": name.strip() or None,
                "symbol": symbol.strip() or None,
                "notes": notes.strip() or None,
                "rating": None,
                "first_seen_at": _normalize_datetime_input(first_seen_at.strip()) or None,
                "image_url": image_url.strip() or None,
                "socials_json": socials_json.strip() or None,
                "pair_created_at": pair_created_at.strip() or None,
            },
        )

        tag_id_set: list[int] = list(tag_ids)
        for raw in new_tags.split(","):
            n = raw.strip()
            if n:
                tag_id_set.append(await db.create_tag(conn, user["id"], n, None))
        if tag_id_set:
            await db.set_token_tags(conn, user["id"], token_id, tag_id_set)

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
    user = await _require_user(request)
    conn = await db.connect()
    try:
        token = await db.get_token(conn, user["id"], token_id)
        if not token:
            raise HTTPException(404)
        token_d = dict(token)
        token_d["tags"] = await db.get_token_tags(conn, token_id)
        token_d["change_1h"] = await db.market_cap_change(conn, token_id, 1)
        token_d["change_24h"] = await db.market_cap_change(conn, token_id, 24)
        sources = await db.get_sources(conn, token_id)
        snapshots = await db.get_snapshots(conn, token_id)
        latest = await db.latest_snapshot(conn, token_id)
        first = await db.first_snapshot(conn, token_id)
        all_tags = await db.list_tags(conn, user["id"])
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
            latest_snapshot=latest,
            first_snapshot=first,
            all_tags=all_tags,
            selected_tag_ids={t["id"] for t in token_d["tags"]},
            user=user,
        ),
    )


@app.post("/token/{token_id}/edit")
async def token_edit(
    request: Request,
    token_id: int,
    name: str = Form(""),
    symbol: str = Form(""),
    notes: str = Form(""),
    first_seen_at: str = Form(""),
    tag_ids: list[int] = Form(default=[]),
    new_tags: str = Form(""),
):
    user = await _require_user(request)
    conn = await db.connect()
    try:
        t = await db.get_token(conn, user["id"], token_id)
        if not t:
            raise HTTPException(404)
        await db.update_token(
            conn,
            user["id"],
            token_id,
            {
                "name": name.strip() or None,
                "symbol": symbol.strip() or None,
                "notes": notes.strip() or None,
                "rating": None,
                "first_seen_at": _normalize_datetime_input(first_seen_at.strip()) or t["first_seen_at"],
            },
        )
        tag_id_set: list[int] = list(tag_ids)
        for raw in new_tags.split(","):
            n = raw.strip()
            if n:
                tag_id_set.append(await db.create_tag(conn, user["id"], n, None))
        await db.set_token_tags(conn, user["id"], token_id, tag_id_set)
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
    user = await _require_user(request)
    conn = await db.connect()
    try:
        if not await db.get_token(conn, user["id"], token_id):
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
    user = await _require_user(request)
    conn = await db.connect()
    try:
        token = await db.get_token(conn, user["id"], token_id)
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
            await db.refresh_token_profile(conn, user["id"], token_id, info)
    finally:
        await conn.close()
    return RedirectResponse(_url(request, f"/token/{token_id}"), status_code=303)


@app.post("/refresh-all")
async def refresh_all(request: Request):
    user = await _require_user(request)
    conn = await db.connect()
    refreshed = 0
    try:
        targets = await db.list_refresh_targets(conn, user["id"])
        for token in targets:
            info = await dexscreener.fetch_token(token["address"], token["chain"])
            if not info:
                continue
            await db.add_snapshot(
                conn,
                token["id"],
                {
                    "price_usd": info.get("price_usd"),
                    "market_cap": info.get("market_cap"),
                    "fdv": info.get("fdv"),
                    "liquidity_usd": info.get("liquidity_usd"),
                    "source": "dexscreener",
                },
            )
            await db.refresh_token_profile(conn, user["id"], token["id"], info)
            refreshed += 1
    finally:
        await conn.close()
    return RedirectResponse(_url(request, f"/?refreshed={refreshed}"), status_code=303)


@app.post("/token/{token_id}/delete")
async def token_delete(request: Request, token_id: int):
    user = await _require_user(request)
    conn = await db.connect()
    try:
        await db.delete_token(conn, user["id"], token_id)
    finally:
        await conn.close()
    return RedirectResponse(_url(request), status_code=303)


# ---------- 标签 ----------

@app.get("/tags", response_class=HTMLResponse)
async def tags_page(request: Request):
    user = await _require_user(request)
    conn = await db.connect()
    try:
        tags = await db.list_tags(conn, user["id"])
    finally:
        await conn.close()
    return templates.TemplateResponse(request, "tags.html", _context(request, tags=tags, user=user))


@app.post("/tags")
async def tags_create(
    request: Request,
    name: str = Form(...),
    category: str = Form(""),
    color: str = Form("#0ea5e9"),
):
    user = await _require_user(request)
    conn = await db.connect()
    try:
        await db.create_tag(conn, user["id"], name, category.strip() or None, color)
    finally:
        await conn.close()
    return RedirectResponse(_url(request, "/tags"), status_code=303)


@app.post("/tags/{tag_id}/update")
async def tags_update(
    request: Request,
    tag_id: int,
    name: str = Form(...),
    category: str = Form(""),
    color: str = Form("#0ea5e9"),
):
    user = await _require_user(request)
    conn = await db.connect()
    try:
        await db.update_tag(conn, user["id"], tag_id, name, category.strip() or None, color)
    finally:
        await conn.close()
    return RedirectResponse(_url(request, "/tags"), status_code=303)


@app.post("/tags/{tag_id}/delete")
async def tags_delete(request: Request, tag_id: int):
    user = await _require_user(request)
    conn = await db.connect()
    try:
        await db.delete_tag(conn, user["id"], tag_id)
    finally:
        await conn.close()
    return RedirectResponse(_url(request, "/tags"), status_code=303)


# ---------- 健康检查 ----------

@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True, "llm_enabled": llm.is_enabled()})
