from datetime import datetime

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agents.research.models import ProductSignal
from app.logger import get_logger

log = get_logger("research.mercadolibre")

# API pública de MercadoLibre — sin autenticación requerida
_ML_BASE = "https://api.mercadolibre.com"
_SITE_ID = "MCO"  # Colombia

# Categorías relevantes para dropshipping en Colombia
ML_CATEGORIES = {
    "MCO1648": "Electrónica",
    "MCO1430": "Ropa y Accesorios",
    "MCO1574": "Belleza y Cuidado Personal",
    "MCO1500": "Hogar y Jardín",
    "MCO1276": "Deportes y Fitness",
}


class MercadoLibreSource:
    """
    Fuente de tendencias de MercadoLibre Colombia.
    Usa la API REST pública (sin autenticación) — muy confiable para LatAm.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MercadoLibreSource":
        self._client = httpx.AsyncClient(
            base_url=_ML_BASE,
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(20.0),
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=15),
        reraise=False,
    )
    async def get_trending_searches(self) -> list[str]:
        """Top búsquedas trending en MercadoLibre Colombia ahora mismo."""
        try:
            assert self._client is not None
            response = await self._client.get(f"/trends/{_SITE_ID}")
            response.raise_for_status()
            data = response.json()
            keywords = [item.get("keyword", "") for item in data if item.get("keyword")]
            log.info("MercadoLibre: trending búsquedas obtenidas", count=len(keywords))
            return keywords[:20]
        except Exception as e:
            log.warning("MercadoLibre: error obteniendo trending", error=str(e))
            return []

    async def get_trending_signals(self) -> list[ProductSignal]:
        """Convierte las búsquedas trending en señales con score."""
        keywords = await self.get_trending_searches()
        signals: list[ProductSignal] = []
        total = len(keywords)
        for i, kw in enumerate(keywords, start=1):
            signals.append(
                ProductSignal(
                    source="mercadolibre",
                    keyword=kw,
                    trend_score=max(0.0, 100.0 - (i - 1) * (100.0 / max(total, 1))),
                    rank=i,
                    fetched_at=datetime.now(),
                )
            )
        return signals

    async def get_top_sellers(
        self, category_id: str = "MCO1648", limit: int = 20
    ) -> list[ProductSignal]:
        """Productos más vendidos en una categoría de MercadoLibre Colombia."""
        try:
            assert self._client is not None
            response = await self._client.get(
                f"/sites/{_SITE_ID}/search",
                params={
                    "category": category_id,
                    "sort": "sold_quantity_desc",
                    "limit": limit,
                },
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("results", [])

            signals: list[ProductSignal] = []
            for i, item in enumerate(items, start=1):
                title = item.get("title", "")
                sold = item.get("sold_quantity", 0)
                signals.append(
                    ProductSignal(
                        source="mercadolibre",
                        keyword=title[:100],
                        trend_score=max(0.0, 100.0 - (i - 1) * 5.0),
                        rank=i,
                        volume=sold,
                        fetched_at=datetime.now(),
                    )
                )

            category_name = ML_CATEGORIES.get(category_id, category_id)
            log.info("MercadoLibre: top sellers obtenidos", category=category_name, count=len(signals))
            return signals

        except Exception as e:
            log.warning("MercadoLibre: error obteniendo top sellers", category=category_id, error=str(e))
            return []

    async def search_product(self, keyword: str, limit: int = 10) -> list[ProductSignal]:
        """Busca un producto y retorna señales de popularidad."""
        try:
            assert self._client is not None
            response = await self._client.get(
                f"/sites/{_SITE_ID}/search",
                params={"q": keyword, "sort": "relevance", "limit": limit},
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("results", [])

            # Usar sold_quantity como proxy de demanda
            signals: list[ProductSignal] = []
            total_sold = sum(i.get("sold_quantity", 0) for i in items) or 1
            for item in items:
                title = item.get("title", keyword)
                sold = item.get("sold_quantity", 0)
                signals.append(
                    ProductSignal(
                        source="mercadolibre",
                        keyword=title[:100],
                        trend_score=(sold / total_sold) * 100,
                        volume=sold,
                        fetched_at=datetime.now(),
                    )
                )

            return signals

        except Exception as e:
            log.warning("MercadoLibre: error buscando producto", keyword=keyword, error=str(e))
            return []

    async def get_all_signals(self) -> list[ProductSignal]:
        """Combina trending + top sellers de todas las categorías principales."""
        all_signals: list[ProductSignal] = []
        all_signals.extend(await self.get_trending_signals())
        for cat_id in list(ML_CATEGORIES.keys())[:3]:
            all_signals.extend(await self.get_top_sellers(cat_id))
        return all_signals
