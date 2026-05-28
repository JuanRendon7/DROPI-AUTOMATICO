import asyncio
from datetime import datetime, timezone

from app.celery_app import celery_app
from app.logger import get_logger

log = get_logger("tasks")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _notify_alert(title: str, body: str) -> None:
    from agents.analytics.notifier import TelegramNotifier
    from app.config import get_settings
    s = get_settings()
    notifier = TelegramNotifier(s.telegram_bot_token, s.telegram_chat_id)
    await notifier.send_alert(title, body)


async def _notify(message: str) -> None:
    from agents.analytics.notifier import TelegramNotifier
    from app.config import get_settings
    s = get_settings()
    notifier = TelegramNotifier(s.telegram_bot_token, s.telegram_chat_id)
    await notifier.send(message)


def _is_last_retry(task_self) -> bool:
    return task_self.request.retries >= task_self.max_retries


# ---------------------------------------------------------------------------
# Tareas Celery
# ---------------------------------------------------------------------------

@celery_app.task(name="app.tasks.run_analytics_collect", bind=True, max_retries=2)
def run_analytics_collect(self):
    """Recolecta métricas del día anterior de todas las plataformas. Programado: 08:00 COT diario."""
    try:
        asyncio.run(_run_analytics_collect_async())
    except Exception as exc:
        log.error("run_analytics_collect falló", error=str(exc))
        if _is_last_retry(self):
            asyncio.run(_notify_alert("Analytics Collect falló", str(exc)[:300]))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.tasks.run_analytics_optimize", bind=True, max_retries=2)
def run_analytics_optimize(self):
    """Aplica reglas de optimización autónoma. Programado: 10:00 COT diario."""
    try:
        asyncio.run(_run_analytics_optimize_async())
    except Exception as exc:
        log.error("run_analytics_optimize falló", error=str(exc))
        if _is_last_retry(self):
            asyncio.run(_notify_alert("Analytics Optimize falló", str(exc)[:300]))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.tasks.run_campaign_creation", bind=True, max_retries=2)
def run_campaign_creation(self):
    """Lanza campañas en Meta/TikTok/Google para el top producto del Research. Programado: 09:00 COT diario."""
    try:
        asyncio.run(_run_campaign_async())
    except Exception as exc:
        log.error("run_campaign_creation falló", error=str(exc))
        if _is_last_retry(self):
            asyncio.run(_notify_alert("Campaign Creation falló", str(exc)[:300]))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.tasks.run_daily_research", bind=True, max_retries=3)
def run_daily_research(self):
    """Ejecuta el Research Agent. Programado: 06:00 COT diario."""
    try:
        asyncio.run(_run_research_async())
    except Exception as exc:
        log.error("run_daily_research falló", error=str(exc))
        if _is_last_retry(self):
            asyncio.run(_notify_alert("Research Agent falló", str(exc)[:300]))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.tasks.run_dropi_sync", bind=True, max_retries=3)
def run_dropi_sync(self):
    """Sincroniza Dropi (catálogo + órdenes). Programado: cada 2 horas."""
    try:
        asyncio.run(_run_dropi_sync_async())
    except Exception as exc:
        log.error("run_dropi_sync falló", error=str(exc))
        if _is_last_retry(self):
            asyncio.run(_notify_alert("Dropi Sync falló", str(exc)[:300]))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="app.tasks.run_orchestrator_cycle", bind=True, max_retries=1)
def run_orchestrator_cycle(self):
    """Ejecuta el ciclo completo de orquestación Research→Dropi→Campaign→Analytics.
    Programado: 06:30 COT diario (después del Research standalone de 06:00)."""
    try:
        asyncio.run(_run_orchestrator_async())
    except Exception as exc:
        log.error("run_orchestrator_cycle falló", error=str(exc))
        asyncio.run(_notify_alert(
            "Ciclo diario falló",
            f"El ciclo de orquestacion de las 6:30 AM termino con error:\n`{str(exc)[:300]}`",
        ))
        raise self.retry(exc=exc, countdown=600)


# ---------------------------------------------------------------------------
# Implementaciones async
# ---------------------------------------------------------------------------

async def _run_analytics_collect_async() -> None:
    from agents.analytics.agent import AnalyticsAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    settings = get_settings()
    agent = AnalyticsAgent(settings)

    async with AsyncSessionLocal() as db:
        snapshots = await agent.collect_metrics(db)
        log.info("Analytics collect completado via Celery", snapshots=len(snapshots))


async def _run_analytics_optimize_async() -> None:
    from agents.analytics.agent import AnalyticsAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    settings = get_settings()
    agent = AnalyticsAgent(settings)

    async with AsyncSessionLocal() as db:
        actions = await agent.run_optimization(db)
        log.info(
            "Analytics optimize completado via Celery",
            actions=len(actions),
            paused=sum(1 for a in actions if a.action == "pause" and a.executed),
            scaled=sum(1 for a in actions if a.action == "scale_budget" and a.executed),
        )


async def _run_research_async() -> None:
    from agents.research.agent import ResearchAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    settings = get_settings()
    agent = ResearchAgent(settings)

    async with AsyncSessionLocal() as db:
        shortlist = await agent.run(db)
        log.info(
            "Research completado via Celery",
            top_product=shortlist.top_products[0].keyword if shortlist.top_products else "n/a",
            sources=shortlist.sources_used,
        )


async def _run_campaign_async() -> None:
    from agents.campaign.agent import CampaignAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal
    from app.models import Product
    from sqlalchemy import select as sa_select

    settings = get_settings()
    agent = CampaignAgent(settings)

    async with AsyncSessionLocal() as db:
        product = await db.scalar(
            sa_select(Product)
            .where(Product.status == "active")
            .order_by(Product.updated_at.desc())
            .limit(1)
        )
        if not product:
            log.warning("No hay productos activos para crear campaña")
            return

        result = await agent.run(db, product)
        log.info(
            "Campaign Agent completado via Celery",
            product=product.name,
            platforms=result.successful_platforms,
        )


async def _run_orchestrator_async() -> None:
    from agents.orchestrator.agent import OrchestratorAgent
    from app.config import get_settings

    settings = get_settings()
    agent = OrchestratorAgent(settings)
    final_state = await agent.run_cycle(trigger_source="scheduled")

    errors = final_state.get("errors", [])
    research = final_state.get("research_status", "?")
    campaign = final_state.get("campaign_status", "?")
    platforms = final_state.get("campaign_platforms", [])
    product_name = final_state.get("research_top_product_name") or "—"

    now_col = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if errors:
        lines = [
            f"⚠️ *Ciclo diario completado con errores* — {now_col}",
            f"Research: `{research}` | Producto: {product_name}",
            f"Campaign: `{campaign}` | Plataformas: {', '.join(platforms) or 'ninguna'}",
            "",
            "*Errores:*",
        ] + [f"• {e[:120]}" for e in errors]
    else:
        lines = [
            f"✅ *Ciclo diario completado* — {now_col}",
            f"Research: `{research}` | Producto: {product_name}",
            f"Campaign: `{campaign}` | Plataformas: {', '.join(platforms) or 'ninguna'}",
        ]

    from agents.analytics.notifier import TelegramNotifier
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    await notifier.send("\n".join(lines))

    log.info(
        "Orchestrator cycle completado via Celery",
        run_id=final_state.get("run_id"),
        research=research,
        campaign=campaign,
        campaign_platforms=platforms,
        errors=len(errors),
    )


async def _run_dropi_sync_async() -> None:
    from agents.dropi.agent import DropiAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    settings = get_settings()
    agent = DropiAgent(settings)

    async with AsyncSessionLocal() as db:
        result = await agent.run_full_sync(db)
        log.info("Dropi sync completado via Celery", **result)
