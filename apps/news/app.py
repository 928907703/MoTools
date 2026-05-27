from __future__ import annotations

import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import CHANNELS, SOURCE_TYPE_COLOR, SOURCE_TYPE_LABEL
from db import init_db, query_news, source_counts
from scheduler import run_fetch_cycle, start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

templates = Jinja2Templates(directory="templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    sched = start_scheduler()
    yield
    sched.shutdown(wait=False)


app = FastAPI(title="News Hub", lifespan=lifespan)


def _humanize(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{secs // 60} 分钟前"
    if secs < 86400:
        return f"{secs // 3600} 小时前"
    return f"{secs // 86400} 天前"


def _prefix(request: Request) -> str:
    prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")
    return prefix


def _normalize_channel(channel: str | None) -> str:
    return channel if channel in CHANNELS else "ai"


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    hours: int = Query(168, ge=1, le=720),
    category: str | None = None,
    channel: str = Query("ai"),
):
    active_channel = _normalize_channel(channel)
    channel_config = CHANNELS[active_channel]
    categories = channel_config["categories"]
    rows = await query_news(channel=active_channel, category=category, hours=hours)

    grouped: OrderedDict[str, list[dict]] = OrderedDict((c, []) for c in categories)
    for r in rows:
        cat = r.get("category") or "其他"
        if cat not in grouped:
            cat = "其他"
        r["pub_human"] = _humanize(r["published_at"])
        r["type_label"] = SOURCE_TYPE_LABEL.get(r["source_type"], r["source_type"])
        r["type_color"] = SOURCE_TYPE_COLOR.get(r["source_type"], "bg-slate-100 text-slate-700")
        grouped[cat].append(r)

    counts = await source_counts(active_channel)
    return templates.TemplateResponse(request, "index.html", {
        "grouped": grouped,
        "categories": categories,
        "channels": CHANNELS,
        "channel": active_channel,
        "channel_config": channel_config,
        "total": len(rows),
        "hours": hours,
        "active_category": category or "全部",
        "source_counts": counts,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "prefix": _prefix(request),
    })


@app.get("/ai")
async def ai_redirect():
    return RedirectResponse("./?channel=ai", status_code=303)


@app.get("/rwa")
async def rwa_redirect():
    return RedirectResponse("./?channel=rwa", status_code=303)


@app.get("/api/news")
async def api_news(hours: int = 168, category: str | None = None, channel: str = "ai"):
    active_channel = _normalize_channel(channel)
    rows = await query_news(channel=active_channel, category=category, hours=hours)
    return {"channel": active_channel, "total": len(rows), "items": rows}


@app.post("/api/refresh")
async def api_refresh(background: BackgroundTasks):
    background.add_task(run_fetch_cycle)
    return JSONResponse({"status": "accepted"}, status_code=202)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
