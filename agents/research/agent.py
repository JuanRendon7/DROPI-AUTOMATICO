import asyncio
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.research.llm_analyst import LLMAnalyst
from agents.research.models import ProductResearch, ProductSignal, ResearchShortlist
from agents.research.scorer import ProductScorer
from agents.research.sources.amazon import AmazonSource
from agents.research.sources.google_trends import GoogleTrendsSource
from agents.research.sources.mercadolibre import MercadoLibreSource
from agents.research.sources.reddit import RedditSource
from agents.research.sources.tiktok import TikTokSource
from app.config import Settings
from app.logger import get_logger
from app.models import AgentLog, Product


class ResearchAgent:
    """
    Agente de investigación de mercado.
    Orquesta 5 fuentes de datos, calcula scores, cruza con Dropi y
    genera un shortlist TOP 10 con análisis de Claude.

    Llamado por el Orchestrator (Fase 6) diariamente a las 06:00 COT.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._log = get_logger("research")

        self.google = GoogleTrendsSource(geo="CO", lang="es")
        self.amazon = AmazonSource(api_key=settings.serpapi_key)
        self.tiktok = TikTokSource()
        self.mercadolibre = MercadoLibreSource()
        self.reddit = RedditSource(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )
        self.scorer = ProductScorer()
        self.analyst = LLMAnalyst(
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
        )

    async def run(self, db: AsyncSession) -> ResearchShortlist:
        """
        Ciclo completo de investigación:
        1. Recolectar señales de todas las fuentes en paralelo
        2. Agregar señales por keyword
        3. Cruzar con catálogo de Dropi
        4. Calcular scores compuestos
        5. Generar análisis con Claude (TOP 10)
        6. Persistir en AgentLog
        7. Retornar ResearchShortlist
        """
        start_time = time.monotonic()
        self._log.info("Iniciando ciclo de investigación de mercado")

        # ── Paso 1: recolectar en paralelo ─────────────────────────────────────
        all_signals = await self._collect_all_signals()

        if not all_signals:
            self._log.error("No se obtuvieron señales de ninguna fuente")
            return ResearchShortlist(
                top_products=[],
                analysis="Sin datos disponibles. Verificar configuración de APIs.",
                total_keywords_analyzed=0,
            )

        sources_used = list({s.source for s in all_signals})
        self._log.info(
            "Señales recolectadas",
            total=len(all_signals),
            sources=sources_used,
        )

        # ── Paso 2: agregar por keyword ─────────────────────────────────────────
        researches = self.scorer.aggregate_signals(all_signals)
        self._log.info("Keywords únicos agregados", count=len(researches))

        # ── Paso 3: cruzar con catálogo de Dropi ───────────────────────────────
        dropi_catalog = await self._get_dropi_catalog(db)
        if dropi_catalog:
            researches = self.scorer.enrich_with_dropi(researches, dropi_catalog)
            in_catalog = sum(1 for r in researches if r.in_dropi_catalog)
            self._log.info("Cruce con Dropi completado", in_catalog=in_catalog)

        # ── Paso 4: ranking TOP 10 ──────────────────────────────────────────────
        top_10 = self.scorer.rank_products(researches, top_n=10)
        self._log.info("TOP 10 productos determinados", scores=[p.composite_score for p in top_10])

        # ── Paso 5: análisis con Claude ─────────────────────────────────────────
        analysis = await self.analyst.generate_shortlist_analysis(top_10)

        # ── Paso 6: persistir ───────────────────────────────────────────────────
        elapsed = time.monotonic() - start_time
        shortlist = ResearchShortlist(
            generated_at=datetime.now(),
            top_products=top_10,
            analysis=analysis,
            sources_used=sources_used,
            total_keywords_analyzed=len(researches),
            execution_time_seconds=round(elapsed, 1),
        )

        await self._persist_results(db, shortlist)

        self._log.info(
            "Investigación completada",
            duration_s=shortlist.execution_time_seconds,
            top_product=top_10[0].keyword if top_10 else "n/a",
        )
        return shortlist

    async def _collect_all_signals(self) -> list[ProductSignal]:
        """Recolecta señales de todas las fuentes en paralelo. Errores no son fatales."""
        tasks = [
            self.google.get_trending_keywords(),
            self._run_amazon(),
            self.tiktok.get_trending_products(),
            self._run_mercadolibre(),
            self.reddit.get_trending_signals(),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_signals: list[ProductSignal] = []
        source_names = ["google_trends", "amazon", "tiktok", "mercadolibre", "reddit"]

        for source_name, result in zip(source_names, results):
            if isinstance(result, Exception):
                self._log.warning(
                    "Fuente falló — continuando sin ella",
                    source=source_name,
                    error=str(result),
                )
            elif isinstance(result, list):
                all_signals.extend(result)
                self._log.info("Fuente completada", source=source_name, signals=len(result))

        return all_signals

    async def _run_amazon(self) -> list[ProductSignal]:
        """Wrapper para usar Amazon como context manager."""
        async with self.amazon:
            return await self.amazon.get_multiple_categories()

    async def _run_mercadolibre(self) -> list[ProductSignal]:
        """Wrapper para usar MercadoLibre como context manager."""
        async with self.mercadolibre:
            return await self.mercadolibre.get_all_signals()

    async def _get_dropi_catalog(self, db: AsyncSession) -> list[dict]:
        """Obtiene el catálogo activo de Dropi desde la DB."""
        try:
            result = await db.execute(
                select(Product).where(Product.status == "active")
            )
            products = result.scalars().all()
            return [
                {
                    "dropi_id": str(p.dropi_id),
                    "name": p.name,
                    "price_buy": float(p.price_buy),
                    "price_sell": float(p.price_sell),
                    "stock": p.stock,
                    "category": p.category,
                }
                for p in products
            ]
        except Exception as e:
            self._log.warning("No se pudo cargar catálogo de Dropi", error=str(e))
            return []

    async def _persist_results(
        self, db: AsyncSession, shortlist: ResearchShortlist
    ) -> None:
        """Guarda el resultado de la investigación en AgentLog."""
        top_summary = [
            {
                "keyword": p.keyword,
                "score": p.composite_score,
                "in_dropi": p.in_dropi_catalog,
                "margin": p.estimated_margin,
            }
            for p in shortlist.top_products
        ]

        db.add(
            AgentLog(
                agent="research",
                action="daily_research",
                status="success",
                reasoning=shortlist.analysis[:2000] if shortlist.analysis else None,
                meta={
                    "top_products": top_summary,
                    "sources_used": shortlist.sources_used,
                    "total_keywords": shortlist.total_keywords_analyzed,
                    "execution_time_s": shortlist.execution_time_seconds,
                },
            )
        )
        await db.commit()
        self._log.info("Resultados persistidos en AgentLog")
