import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.logger import get_logger
from app.models import AgentLog, Campaign, Metric, Product

router = APIRouter(tags=["dashboard"])
security = HTTPBasic()
templates = Jinja2Templates(directory="app/templates")
log = get_logger("api.dashboard")


# ── Auth ─────────────────────────────────────────────────────────────────────────

def _check_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> str:
    """Verifica credenciales HTTP Basic contra Settings. Usa compare_digest para evitar timing attacks."""
    settings = get_settings()
    ok_user = secrets.compare_digest(
        credentials.username.encode(), settings.dashboard_username.encode()
    )
    ok_pass = secrets.compare_digest(
        credentials.password.encode(), settings.dashboard_password.encode()
    )
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ── Data queries ──────────────────────────────────────────────────────────────────

async def _get_global_metrics(db: AsyncSession) -> dict:
    """Totales de gasto, ingresos y ROAS promedio (últimos 30 días) + campañas activas."""
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
    """Productos activos ordenados por score, con gasto y ROAS de los últimos 7 días."""
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
    """Estado semáforo (green/yellow/red) de cada agente basado en su último AgentLog."""
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
    """Últimos N ciclos del orquestador registrados en AgentLog."""
    result = await db.execute(
        select(AgentLog)
        .where(AgentLog.agent == "orchestrator")
        .order_by(AgentLog.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(entry.id),
            "status": entry.status,
            "created_at": entry.created_at.strftime("%Y-%m-%d %H:%M"),
            "meta": entry.meta or {},
        }
        for entry in result.scalars().all()
    ]


async def _get_chart_data(db: AsyncSession, days: int = 7) -> dict:
    """Datos diarios de gasto e ingresos para Chart.js (últimos N días)."""
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


# ── Endpoints ─────────────────────────────────────────────────────────────────────

@router.get("/logout")
async def logout():
    """Cierra sesión HTTP Basic forzando un 401 que borra las credenciales del navegador."""
    raise HTTPException(
        status_code=401,
        detail="Sesión cerrada",
        headers={"WWW-Authenticate": "Basic"},
    )



@router.get("/dashboard")
async def dashboard(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    _username: Annotated[str, Depends(_check_auth)],
):
    """Dashboard principal — renderiza HTML con todos los widgets de monitoreo."""
    global_metrics = await _get_global_metrics(db)
    products = await _get_products_table(db)
    agents = await _get_agent_statuses(db)
    orc_log = await _get_orchestrator_log(db)
    chart_data = await _get_chart_data(db, days=7)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "global_metrics": global_metrics,
            "products": products,
            "agents": agents,
            "orc_log": orc_log,
            "chart_data": chart_data,
        },
    )
