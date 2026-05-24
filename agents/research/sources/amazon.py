from datetime import datetime

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agents.research.models import ProductSignal
from app.logger import get_logger

log = get_logger("research.amazon")

_SERPAPI_BASE = "https://serpapi.com/search"

# Categorías de Amazon más relevantes para dropshipping
AMAZON_CATEGORIES = {
    "electronics": "electronics",
    "home": "home-garden",
    "beauty": "beauty",
    "sports": "sporting-goods",
    "toys": "toys-games",
    "fashion": "apparel",
}


class AmazonSource:
    """
    Fuente de datos de Amazon Best Sellers via SerpAPI.
    Si SERPAPI_KEY no está configurada, retorna lista vacía.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AmazonSource":
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    def _check_configured(self) -> bool:
        if not self._api_key:
            log.warning("SERPAPI_KEY no configurada — saltando fuente Amazon")
            return False
        return True

    @retry(
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=5, max=30),
        reraise=False,
    )
    async def get_best_sellers(
        self, category: str = "electronics"
    ) -> list[ProductSignal]:
        """Top productos de Amazon Best Sellers para una categoría."""
        if not self._check_configured():
            return []

        try:
            params = {
                "engine": "amazon",
                "amazon_domain": "amazon.com",
                "search_type": "best_sellers",
                "category_id": AMAZON_CATEGORIES.get(category, category),
                "api_key": self._api_key,
            }
            assert self._client is not None
            response = await self._client.get(_SERPAPI_BASE, params=params)
            response.raise_for_status()

            data = response.json()
            results = data.get("results", data.get("best_sellers", []))

            signals: list[ProductSignal] = []
            for i, item in enumerate(results[:20], start=1):
                title = item.get("title", item.get("name", ""))
                if not title:
                    continue
                # Rank más bajo = más popular → invertir para score
                signals.append(
                    ProductSignal(
                        source="amazon",
                        keyword=title[:100],
                        trend_score=max(0.0, 100.0 - (i - 1) * 5.0),
                        rank=i,
                        volume=item.get("sales_volume"),
                        fetched_at=datetime.now(),
                    )
                )

            log.info("Amazon: best sellers obtenidos", category=category, count=len(signals))
            return signals

        except Exception as e:
            log.warning("Amazon: error obteniendo best sellers", category=category, error=str(e))
            return []

    async def search_product(self, keyword: str) -> list[ProductSignal]:
        """Busca un keyword en Amazon y retorna los resultados como señales."""
        if not self._check_configured():
            return []

        try:
            params = {
                "engine": "amazon",
                "amazon_domain": "amazon.com",
                "k": keyword,
                "api_key": self._api_key,
            }
            assert self._client is not None
            response = await self._client.get(_SERPAPI_BASE, params=params)
            response.raise_for_status()

            data = response.json()
            results = data.get("organic_results", data.get("results", []))

            signals: list[ProductSignal] = []
            for i, item in enumerate(results[:10], start=1):
                title = item.get("title", "")
                signals.append(
                    ProductSignal(
                        source="amazon",
                        keyword=title[:100] or keyword,
                        trend_score=max(0.0, 100.0 - (i - 1) * 10.0),
                        rank=i,
                        fetched_at=datetime.now(),
                    )
                )

            return signals

        except Exception as e:
            log.warning("Amazon: error buscando keyword", keyword=keyword, error=str(e))
            return []

    async def get_multiple_categories(
        self, categories: list[str] | None = None
    ) -> list[ProductSignal]:
        """Obtiene best sellers de múltiples categorías."""
        cats = categories or list(AMAZON_CATEGORIES.keys())[:3]
        all_signals: list[ProductSignal] = []
        for cat in cats:
            signals = await self.get_best_sellers(cat)
            all_signals.extend(signals)
        return all_signals
