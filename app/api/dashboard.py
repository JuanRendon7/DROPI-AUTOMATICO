import hashlib
import hmac
import secrets as _secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.logger import get_logger
from app.models import AgentLog, Campaign, Metric, Product

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")
log = get_logger("api.dashboard")

_COOKIE = "dsm_session"
_MAX_AGE = 86400  # 24 h


# ── Cookie auth ───────────────────────────────────────────────────────────────────

def _secret() -> str:
    s = get_settings()
    return hashlib.sha256(f"{s.dashboard_username}:{s.dashboard_password}".encode()).hexdigest()


def _make_token(username: str) -> str:
    ts = str(int(time.time()))
    payload = f"{username}|{ts}"
    sig = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def _verify_token(token: str) -> str | None:
    try:
        parts = token.split("|")
        if len(parts) != 3:
            return None
        username, ts, sig = parts
        if time.time() - float(ts) > _MAX_AGE:
            return None
        payload = f"{username}|{ts}"
        expected = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return username
    except Exception:
        return None


def _session(dsm_session: str | None = Cookie(default=None)) -> str | None:
    return _verify_token(dsm_session) if dsm_session else None


def _auth(user: Annotated[str | None, Depends(_session)] = None) -> str:
    if not user:
        raise HTTPException(status_code=401)
    return user


# ── Login / Logout ────────────────────────────────────────────────────────────────

@router.get("/login")
async def login_page(request: Request, user: Annotated[str | None, Depends(_session)] = None):
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login")
async def login_post(request: Request):
    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    s = get_settings()
    ok = (
        _secrets.compare_digest(username.encode(), s.dashboard_username.encode())
        and _secrets.compare_digest(password.encode(), s.dashboard_password.encode())
    )
    if not ok:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Credenciales incorrectas — verifica usuario y contraseña"},
            status_code=401,
        )
    token = _make_token(username)
    resp = RedirectResponse(url="/dashboard", status_code=302)
    resp.set_cookie(
        _COOKIE, token,
        max_age=_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=(s.environment == "production"),
    )
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(_COOKIE)
    return resp


# ── Data queries ──────────────────────────────────────────────────────────────────

async def _get_global_metrics(db: AsyncSession) -> dict:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
    result = await db.execute(
        select(
            func.coalesce(func.sum(Metric.spend_usd), 0).label("total_spend"),
            func.coalesce(func.sum(Metric.revenue_usd), 0).label("total_revenue"),
            func.coalesce(func.avg(Metric.roas), 0).label("avg_roas"),
        ).where(Metric.date >= cutoff)
    )
    row = result.one()
    active_campaigns = await db.scalar(
        select(func.count(Campaign.id)).where(Campaign.status == "active")
    )
    return {
        "total_spend": round(float(row.total_spend), 2),
        "total_revenue": round(float(row.total_revenue), 2),
        "avg_roas": round(float(row.avg_roas), 2),
        "active_campaigns": active_campaigns or 0,
    }


async def _get_products_table(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Product)
        .where(Product.status == "active")
        .order_by(Product.score.desc().nullslast())
        .limit(20)
    )
    products = result.scalars().all()
    cutoff_7d = datetime.now(timezone.utc).date() - timedelta(days=7)
    rows = []
    for p in products:
        camp_result = await db.execute(
            select(Campaign.platform).where(
                Campaign.product_id == p.id, Campaign.status == "active"
            )
        )
        platforms = [r[0] for r in camp_result.all()]
        metric_result = await db.execute(
            select(
                func.coalesce(func.sum(Metric.spend_usd), 0).label("spend"),
                func.coalesce(func.avg(Metric.roas), 0).label("roas"),
            )
            .join(Campaign, Metric.campaign_id == Campaign.id)
            .where(Campaign.product_id == p.id, Metric.date >= cutoff_7d)
        )
        m = metric_result.one()
        rows.append({
            "name": p.name[:60],
            "status": p.status,
            "score": float(p.score) if p.score else None,
            "platforms": platforms,
            "spend_7d": round(float(m.spend), 2),
            "roas_7d": round(float(m.roas), 2),
        })
    return rows


_AGENT_INTERVALS: dict[str, timedelta] = {
    "research": timedelta(hours=26),
    "dropi": timedelta(hours=2, minutes=30),
    "campaign": timedelta(hours=26),
    "analytics": timedelta(hours=26),
    "orchestrator": timedelta(hours=26),
}


async def _get_agent_statuses(db: AsyncSession) -> dict:
    agents = ["research", "dropi", "campaign", "analytics", "orchestrator"]
    statuses = {}
    now = datetime.now(timezone.utc)
    for agent in agents:
        last = await db.scalar(
            select(AgentLog)
            .where(AgentLog.agent == agent)
            .order_by(AgentLog.created_at.desc())
            .limit(1)
        )
        if last is None:
            statuses[agent] = {"color": "red", "label": "Sin datos", "last_run": None}
            continue
        last_run = last.created_at
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        age = now - last_run
        interval = _AGENT_INTERVALS.get(agent, timedelta(hours=26))
        if last.status == "failure":
            color, label = "red", "Error"
        elif last.status in ("success", "partial") and age < interval:
            color, label = "green", "OK"
        elif last.status == "retry":
            color, label = "yellow", "Reintentando"
        else:
            color, label = "yellow", "Retrasado"
        statuses[agent] = {
            "color": color,
            "label": label,
            "last_run": last_run.strftime("%Y-%m-%d %H:%M UTC"),
        }
    return statuses


async def _get_orchestrator_log(db: AsyncSession, limit: int = 50) -> list[dict]:
    result = await db.execute(
        select(AgentLog)
        .where(AgentLog.agent == "orchestrator")
        .order_by(AgentLog.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(e.id),
            "status": e.status,
            "created_at": e.created_at.strftime("%Y-%m-%d %H:%M"),
            "meta": e.meta or {},
        }
        for e in result.scalars().all()
    ]


async def _get_chart_data(db: AsyncSession, days: int = 7) -> dict:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    result = await db.execute(
        select(
            Metric.date,
            func.sum(Metric.spend_usd).label("spend"),
            func.sum(Metric.revenue_usd).label("revenue"),
        )
        .where(Metric.date >= cutoff)
        .group_by(Metric.date)
        .order_by(Metric.date)
    )
    rows = result.all()
    return {
        "labels": [str(r.date) for r in rows],
        "spend": [round(float(r.spend), 2) for r in rows],
        "revenue": [round(float(r.revenue), 2) for r in rows],
    }


# ── Dashboard + API endpoints ─────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[str | None, Depends(_session)] = None,
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "global_metrics": await _get_global_metrics(db),
            "products": await _get_products_table(db),
            "agents": await _get_agent_statuses(db),
            "orc_log": await _get_orchestrator_log(db),
            "chart_data": await _get_chart_data(db, days=7),
        },
    )


@router.get("/dashboard/api/agent/{agent_name}")
async def agent_detail_api(
    agent_name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[str, Depends(_auth)],
):
    result = await db.execute(
        select(AgentLog)
        .where(AgentLog.agent == agent_name)
        .order_by(AgentLog.created_at.desc())
        .limit(20)
    )
    return [
        {
            "status": e.status,
            "created_at": e.created_at.strftime("%Y-%m-%d %H:%M UTC"),
            "errors": (e.meta or {}).get("errors", []),
            "meta": {k: v for k, v in (e.meta or {}).items() if k != "errors"},
        }
        for e in result.scalars().all()
    ]


@router.get("/dashboard/api/metrics")
async def metrics_detail_api(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[str, Depends(_auth)],
):
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=14)
    result = await db.execute(
        select(
            Metric.date,
            func.coalesce(func.sum(Metric.spend_usd), 0).label("spend"),
            func.coalesce(func.sum(Metric.revenue_usd), 0).label("revenue"),
            func.coalesce(func.avg(Metric.roas), 0).label("roas"),
        )
        .where(Metric.date >= cutoff)
        .group_by(Metric.date)
        .order_by(Metric.date.desc())
    )
    return [
        {
            "date": str(r.date),
            "spend": round(float(r.spend), 2),
            "revenue": round(float(r.revenue), 2),
            "roas": round(float(r.roas), 2),
        }
        for r in result.all()
    ]


@router.get("/dashboard/api/campaigns")
async def campaigns_api(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[str, Depends(_auth)],
):
    from app.models import Product as ProductModel
    result = await db.execute(
        select(Campaign, ProductModel.name.label("product_name"))
        .join(ProductModel, Campaign.product_id == ProductModel.id)
        .where(Campaign.status == "active")
        .order_by(Campaign.created_at.desc())
        .limit(30)
    )
    return [
        {
            "platform": r.Campaign.platform,
            "product": (r.product_name or "")[:45],
            "budget_usd": float(r.Campaign.budget_usd) if r.Campaign.budget_usd else 0,
            "external_id": r.Campaign.external_id or "—",
            "created_at": r.Campaign.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for r in result.all()
    ]
