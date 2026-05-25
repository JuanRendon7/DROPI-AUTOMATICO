import asyncio
from datetime import datetime, timezone

from sqlalchemy import select as sa_select

from agents.analytics.agent import AnalyticsAgent
from agents.campaign.agent import CampaignAgent
from agents.dropi.agent import DropiAgent
from agents.orchestrator.state import OrchestratorState
from agents.research.agent import ResearchAgent
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.logger import get_logger
from app.models import Product

log = get_logger("orchestrator.nodes")
_MAX_RETRIES = 3


async def _with_backoff(coro_factory, max_retries: int = _MAX_RETRIES):
    """
    Ejecuta coro_factory() con backoff exponencial (1s, 2s, 4s).
    coro_factory es un callable sin args que retorna una coroutine.
    Lanza la última excepción si todos los reintentos fallan.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                log.warning(
                    "Reintentando nodo",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    wait_seconds=wait,
                    error=str(exc),
                )
                await asyncio.sleep(wait)
    raise last_exc  # type: ignore[misc]


async def research_node(state: OrchestratorState) -> dict:
    """Ejecuta ResearchAgent.run() y extrae el top producto para el ciclo."""
    settings = get_settings()

    async def _run():
        async with AsyncSessionLocal() as db:
            agent = ResearchAgent(settings)
            shortlist = await agent.run(db)
            top = shortlist.top_products[0] if shortlist.top_products else None
            top_id = None
            if top and top.dropi_product:
                top_id = str(top.dropi_product.get("id", "")) or None
            return {
                "research_status": "success",
                "research_top_product_id": top_id,
                "research_top_product_name": top.keyword if top else None,
            }

    try:
        return await _with_backoff(_run)
    except Exception as exc:
        msg = f"research: {exc}"
        log.error("research_node falló", error=msg)
        return {
            "research_status": "failed",
            "research_error": str(exc),
            "errors": state.get("errors", []) + [msg],
        }


async def dropi_sync_node(state: OrchestratorState) -> dict:
    """Ejecuta DropiAgent.run_full_sync() para sincronizar catálogo y órdenes."""
    settings = get_settings()

    async def _run():
        async with AsyncSessionLocal() as db:
            agent = DropiAgent(settings)
            result = await agent.run_full_sync(db)
            return {
                "dropi_status": "success",
                "dropi_synced_count": result.get("synced", 0),
            }

    try:
        return await _with_backoff(_run)
    except Exception as exc:
        msg = f"dropi: {exc}"
        log.error("dropi_sync_node falló", error=msg)
        return {
            "dropi_status": "failed",
            "dropi_error": str(exc),
            "errors": state.get("errors", []) + [msg],
        }


async def campaign_node(state: OrchestratorState) -> dict:
    """
    Ejecuta CampaignAgent.run() para el top producto de Research.
    Usa research_top_product_id si está disponible; si no, el producto activo más reciente.
    """
    settings = get_settings()
    top_product_id = state.get("research_top_product_id")

    async def _run():
        async with AsyncSessionLocal() as db:
            product = None
            if top_product_id:
                product = await db.get(Product, top_product_id)
            if product is None:
                product = await db.scalar(
                    sa_select(Product)
                    .where(Product.status == "active")
                    .order_by(Product.updated_at.desc())
                    .limit(1)
                )
            if product is None:
                log.warning("campaign_node: sin productos activos, omitiendo")
                return {"campaign_status": "skipped", "campaign_platforms": []}

            agent = CampaignAgent(settings)
            result = await agent.run(db, product)
            return {
                "campaign_status": "success",
                "campaign_platforms": result.successful_platforms,
            }

    try:
        return await _with_backoff(_run)
    except Exception as exc:
        msg = f"campaign: {exc}"
        log.error("campaign_node falló", error=msg)
        return {
            "campaign_status": "failed",
            "campaign_error": str(exc),
            "errors": state.get("errors", []) + [msg],
        }


async def collect_metrics_node(state: OrchestratorState) -> dict:
    """Ejecuta AnalyticsAgent.collect_metrics() para recolectar métricas del día anterior."""
    settings = get_settings()

    async def _run():
        async with AsyncSessionLocal() as db:
            agent = AnalyticsAgent(settings)
            snapshots = await agent.collect_metrics(db)
            log.info("collect_metrics_node completado", snapshots=len(snapshots))
            return {"analytics_collect_status": "success"}

    try:
        return await _with_backoff(_run)
    except Exception as exc:
        msg = f"analytics_collect: {exc}"
        log.error("collect_metrics_node falló", error=msg)
        return {
            "analytics_collect_status": "failed",
            "analytics_collect_error": str(exc),
            "errors": state.get("errors", []) + [msg],
        }


async def optimize_node(state: OrchestratorState) -> dict:
    """Ejecuta AnalyticsAgent.run_optimization() y registra las acciones tomadas."""
    settings = get_settings()

    async def _run():
        async with AsyncSessionLocal() as db:
            agent = AnalyticsAgent(settings)
            actions = await agent.run_optimization(db)
            executed = sum(1 for a in actions if a.executed)
            return {
                "analytics_optimize_status": "success",
                "analytics_actions_count": executed,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

    try:
        return await _with_backoff(_run)
    except Exception as exc:
        msg = f"analytics_optimize: {exc}"
        log.error("optimize_node falló", error=msg)
        return {
            "analytics_optimize_status": "failed",
            "analytics_optimize_error": str(exc),
            "errors": state.get("errors", []) + [msg],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
