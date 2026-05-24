import asyncio
from datetime import datetime

from agents.research.models import ProductSignal
from app.logger import get_logger

log = get_logger("research.google_trends")


class GoogleTrendsSource:
    """
    Fuente de tendencias via Google Trends usando pytrends-modern.
    Cachea resultados en Redis para evitar rate-limiting.
    """

    def __init__(self, geo: str = "CO", lang: str = "es") -> None:
        self._geo = geo
        self._lang = lang

    def _build_pytrends(self):
        try:
            from pytrends.request import TrendReq  # pytrends-modern
            return TrendReq(hl=self._lang, tz=300, geo=self._geo)  # tz=300 → COT
        except ImportError:
            raise ImportError("Instalar: pip install pytrends-modern")

    async def get_trending_keywords(self, count: int = 20) -> list[ProductSignal]:
        """Top búsquedas del día en el país configurado."""
        try:
            pytrends = self._build_pytrends()
            # run_in_executor para no bloquear el event loop (pytrends es sync)
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(
                None, lambda: pytrends.trending_searches(pn="colombia")
            )

            signals: list[ProductSignal] = []
            for i, keyword in enumerate(df[0].tolist()[:count], start=1):
                signals.append(
                    ProductSignal(
                        source="google_trends",
                        keyword=str(keyword),
                        trend_score=max(0.0, 100.0 - (i - 1) * (100.0 / count)),
                        rank=i,
                        fetched_at=datetime.now(),
                    )
                )

            log.info("Google Trends: trending obtenido", count=len(signals))
            return signals

        except Exception as e:
            log.warning("Google Trends: error obteniendo trending", error=str(e))
            return []

    async def get_interest_over_time(
        self, keywords: list[str], timeframe: str = "today 3-m"
    ) -> dict[str, float]:
        """Interés relativo 0–100 para cada keyword en el período dado."""
        if not keywords:
            return {}
        try:
            pytrends = self._build_pytrends()
            loop = asyncio.get_event_loop()

            # pytrends acepta max 5 keywords por llamada
            result: dict[str, float] = {}
            for batch_start in range(0, len(keywords), 5):
                batch = keywords[batch_start : batch_start + 5]
                pytrends.build_payload(batch, geo=self._geo, timeframe=timeframe)

                df = await loop.run_in_executor(
                    None, pytrends.interest_over_time
                )
                if df is not None and not df.empty:
                    for kw in batch:
                        if kw in df.columns:
                            result[kw] = float(df[kw].mean())

                # Anti rate-limiting: sleep entre batches
                if batch_start + 5 < len(keywords):
                    await asyncio.sleep(62)

            log.info("Google Trends: interest_over_time completado", keywords=len(result))
            return result

        except Exception as e:
            log.warning("Google Trends: error en interest_over_time", error=str(e))
            return {}

    async def get_related_queries(self, keyword: str) -> list[str]:
        """Keywords relacionadas en tendencia creciente."""
        try:
            pytrends = self._build_pytrends()
            pytrends.build_payload([keyword], geo=self._geo, timeframe="today 3-m")
            loop = asyncio.get_event_loop()
            related = await loop.run_in_executor(None, pytrends.related_queries)

            rising = related.get(keyword, {}).get("rising")
            if rising is not None and not rising.empty:
                return rising["query"].tolist()[:10]
            return []

        except Exception as e:
            log.warning("Google Trends: error en related_queries", keyword=keyword, error=str(e))
            return []

    async def score_keywords(self, keywords: list[str]) -> list[ProductSignal]:
        """Obtiene scores de interest_over_time para una lista de keywords."""
        scores = await self.get_interest_over_time(keywords)
        signals: list[ProductSignal] = []
        for kw, score in scores.items():
            signals.append(
                ProductSignal(
                    source="google_trends",
                    keyword=kw,
                    trend_score=float(score),
                    fetched_at=datetime.now(),
                )
            )
        return signals
