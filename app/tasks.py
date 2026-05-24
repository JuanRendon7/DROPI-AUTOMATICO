import asyncio

from app.celery_app import celery_app
from app.logger import get_logger

log = get_logger("tasks")


@celery_app.task(name="app.tasks.run_campaign_creation", bind=True, max_retries=2)
def run_campaign_creation(self):
    """Lanza campañas en Meta/TikTok/Google para el top producto del Research. Programado: 09:00 COT diario."""
    try:
        asyncio.run(_run_campaign_async())
    except Exception as exc:
        log.error("run_campaign_creation falló", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.tasks.run_daily_research", bind=True, max_retries=3)
def run_daily_research(self):
    """Ejecuta el Research Agent. Programado: 06:00 COT diario."""
    try:
        asyncio.run(_run_research_async())
    except Exception as exc:
        log.error("run_daily_research falló", error=str(exc))
        raise self.retry(exc=exc, countdown=300)  # reintentar en 5 min


@celery_app.task(name="app.tasks.run_dropi_sync", bind=True, max_retries=3)
def run_dropi_sync(self):
    """Sincroniza Dropi (catálogo + órdenes). Programado: cada 2 horas."""
    try:
        asyncio.run(_run_dropi_sync_async())
    except Exception as exc:
        log.error("run_dropi_sync falló", error=str(exc))
        raise self.retry(exc=exc, countdown=120)


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


async def _run_dropi_sync_async() -> None:
    from agents.dropi.agent import DropiAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    settings = get_settings()
    agent = DropiAgent(settings)

    async with AsyncSessionLocal() as db:
        result = await agent.run_full_sync(db)
        log.info("Dropi sync completado via Celery", **result)
