import asyncio
import time

from sqlalchemy.ext.asyncio import AsyncSession

from agents.campaign.image_handler import ImageHandler
from agents.campaign.models import AdCopy, CampaignRequest, CampaignResult, PlatformCampaignResult
from agents.campaign.platforms.google_ads import GoogleAdsClient
from agents.campaign.platforms.meta import MetaAdsClient
from agents.campaign.platforms.tiktok import TikTokAdsClient
from agents.research.llm_analyst import LLMAnalyst
from app.config import Settings
from app.logger import get_logger
from app.models import AgentLog, Campaign, Product

log = get_logger("campaign_agent")


def _exc_to_result(platform: str, exc: Exception) -> PlatformCampaignResult:
    return PlatformCampaignResult(platform=platform, success=False, error=str(exc))


class CampaignAgent:
    """
    Crea campañas publicitarias en Meta, TikTok y Google Ads automáticamente.
    Genera copy con Claude y sube imágenes de Dropi a cada plataforma.

    Llamado por el Orchestrator (Fase 6) diariamente a las 09:00 COT,
    después del Research Agent.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._analyst = LLMAnalyst(
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
        )

    async def run(self, db: AsyncSession, product: Product) -> CampaignResult:
        """
        Flujo completo de creación de campañas:
        1. Generar ad copy con Claude para cada plataforma
        2. Descargar primera imagen válida del producto
        3. Subir imagen a cada plataforma + crear campañas en paralelo
        4. Guardar campañas exitosas en DB (tabla campaigns)
        5. Persistir AgentLog
        """
        start_time = time.monotonic()
        log.info("Iniciando Campaign Agent", product=product.name, dropi_id=product.dropi_id)

        # ── Paso 1: generar copies ─────────────────────────────────────────────
        copies = await self._generate_copies(product)

        # ── Paso 2: construir request ──────────────────────────────────────────
        request = CampaignRequest(
            product_id=str(product.id),
            dropi_id=product.dropi_id,
            product_name=product.name,
            product_url=f"{self._settings.dropi_base_url}/productos/{product.dropi_id}",
            image_urls=product.images or [],
            price_sell=product.price_sell,
            category=product.category or "general",
            daily_budget_usd=self._settings.campaign_daily_budget_usd,
            ad_copies=copies,
        )

        # ── Paso 3: descargar imagen ───────────────────────────────────────────
        image_data: tuple[bytes, str] | None = None
        if request.image_urls:
            async with ImageHandler() as handler:
                image_data = await handler.download_first_valid(request.image_urls)

        # ── Paso 4: lanzar campañas en paralelo ───────────────────────────────
        results = await self._launch_campaigns(request, image_data)

        # ── Paso 5: guardar en DB ──────────────────────────────────────────────
        await self._save_to_db(db, results, product, request)

        elapsed = round(time.monotonic() - start_time, 1)
        log.info(
            "Campaign Agent completado",
            product=product.name,
            platforms_ok=CampaignResult(product_id=str(product.id), results=results).successful_platforms,
            duration_s=elapsed,
        )

        return CampaignResult(product_id=str(product.id), results=results)

    async def _generate_copies(self, product: Product) -> dict[str, AdCopy]:
        """Genera copies para las 3 plataformas usando Claude. Fallback a copies genéricos."""
        copies: dict[str, AdCopy] = {}
        platforms = [("facebook", "meta"), ("tiktok", "tiktok"), ("google", "google")]

        for platform_key, copy_key in platforms:
            raw = await self._analyst.suggest_ad_copy(product.name, platform_key)
            copies[copy_key] = AdCopy(
                platform=copy_key,
                headline=raw.get("headline", product.name[:30]),
                body=raw.get("body", f"¡Consigue {product.name}!"),
                cta=raw.get("cta", "Ver más"),
            )

        log.info("Ad copies generados con Claude", platforms=list(copies.keys()))
        return copies

    async def _launch_campaigns(
        self,
        request: CampaignRequest,
        image_data: tuple[bytes, str] | None,
    ) -> list[PlatformCampaignResult]:
        """Lanza campañas en paralelo. Errores de plataforma no son fatales."""
        tasks: list = []
        platform_names: list[str] = []

        s = self._settings

        if s.meta_access_token and s.meta_ad_account_id:
            tasks.append(self._run_meta(request, image_data))
            platform_names.append("meta")
        else:
            log.info("Meta Ads no configurado — omitiendo")

        if s.tiktok_access_token and s.tiktok_advertiser_id:
            tasks.append(self._run_tiktok(request, image_data))
            platform_names.append("tiktok")
        else:
            log.info("TikTok Ads no configurado — omitiendo")

        if s.google_ads_customer_id:
            tasks.append(self._run_google(request, image_data))
            platform_names.append("google")
        else:
            log.info("Google Ads no configurado — omitiendo")

        if not tasks:
            log.warning("Ninguna plataforma de ads configurada")
            return []

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[PlatformCampaignResult] = []
        for platform, result in zip(platform_names, raw_results):
            if isinstance(result, Exception):
                results.append(_exc_to_result(platform, result))
            else:
                results.append(result)

        return results

    async def _run_meta(
        self, request: CampaignRequest, image_data: tuple[bytes, str] | None
    ) -> PlatformCampaignResult:
        s = self._settings
        async with MetaAdsClient(s.meta_access_token, s.meta_ad_account_id, s.meta_page_id) as client:
            image_ids: list[str] = []
            if image_data:
                image_hash = await client.upload_image(*image_data)
                image_ids = [image_hash]
            return await client.create_campaign(request, image_ids)

    async def _run_tiktok(
        self, request: CampaignRequest, image_data: tuple[bytes, str] | None
    ) -> PlatformCampaignResult:
        s = self._settings
        async with TikTokAdsClient(s.tiktok_access_token, s.tiktok_advertiser_id) as client:
            image_ids: list[str] = []
            if image_data:
                image_id = await client.upload_image(*image_data)
                image_ids = [image_id]
            return await client.create_campaign(request, image_ids)

    async def _run_google(
        self, request: CampaignRequest, image_data: tuple[bytes, str] | None
    ) -> PlatformCampaignResult:
        s = self._settings
        client = GoogleAdsClient(
            developer_token=s.google_ads_developer_token,
            customer_id=s.google_ads_customer_id,
            client_id=s.google_ads_client_id,
            client_secret=s.google_ads_client_secret,
            refresh_token=s.google_ads_refresh_token,
        )
        image_ids: list[str] = []
        if image_data:
            resource_name = await client.upload_image(*image_data)
            if resource_name:
                image_ids = [resource_name]
        return await client.create_campaign(request, image_ids)

    async def _save_to_db(
        self,
        db: AsyncSession,
        results: list[PlatformCampaignResult],
        product: Product,
        request: CampaignRequest,
    ) -> None:
        """Persiste campañas exitosas y registra AgentLog."""
        campaign_summary = []

        for result in results:
            if result.success and result.campaign_id:
                db.add(
                    Campaign(
                        product_id=product.id,
                        platform=result.platform,
                        external_id=result.campaign_id,
                        status="active",
                        daily_budget_usd=request.daily_budget_usd,
                    )
                )
                campaign_summary.append(
                    {"platform": result.platform, "campaign_id": result.campaign_id}
                )

        overall = CampaignResult(product_id=str(product.id), results=results)
        status = "success" if overall.successful_platforms else "failure"
        if overall.successful_platforms and overall.failed_platforms:
            status = "partial"

        db.add(
            AgentLog(
                agent="campaign",
                action="create_campaigns",
                status=status,
                meta={
                    "product_id": str(product.id),
                    "product_name": product.name,
                    "campaigns_created": campaign_summary,
                    "platforms_ok": overall.successful_platforms,
                    "platforms_failed": overall.failed_platforms,
                    "platforms_skipped": [r.platform for r in results if r.skipped],
                },
            )
        )
        await db.commit()
        log.info("Campañas guardadas en DB", campaigns=len(campaign_summary))
