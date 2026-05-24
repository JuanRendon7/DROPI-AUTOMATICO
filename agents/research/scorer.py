from collections import defaultdict
from decimal import Decimal

from agents.research.models import ProductResearch, ProductSignal
from app.logger import get_logger

log = get_logger("research.scorer")

# Pesos por fuente — deben sumar 1.0
SOURCE_WEIGHTS: dict[str, float] = {
    "google_trends": 0.30,
    "amazon": 0.25,
    "tiktok": 0.20,
    "mercadolibre": 0.15,
    "reddit": 0.10,
}

# Peso adicional del margen sobre el score final
MARGIN_WEIGHT = 0.15
BASE_WEIGHT = 1.0 - MARGIN_WEIGHT  # 0.85 del score de señales


class ProductScorer:
    """
    Calcula el score compuesto de un producto (0–100) ponderando señales
    de múltiples fuentes y el margen estimado si está en el catálogo de Dropi.
    """

    def calculate_score(
        self,
        signals: list[ProductSignal],
        price_buy: Decimal | None = None,
        price_sell: Decimal | None = None,
    ) -> float:
        """
        Score compuesto 0–100.
        - signals: señales de tendencia de todas las fuentes disponibles
        - price_buy/price_sell: si están disponibles, influye el margen
        """
        if not signals:
            return 0.0

        # Agrupar señales por fuente y promediar
        by_source: dict[str, list[float]] = defaultdict(list)
        for signal in signals:
            by_source[signal.source].append(signal.trend_score)

        source_averages: dict[str, float] = {
            source: sum(scores) / len(scores)
            for source, scores in by_source.items()
        }

        # Score ponderado de señales (normalizado a las fuentes disponibles)
        total_weight_available = sum(
            SOURCE_WEIGHTS.get(src, 0.0) for src in source_averages
        )

        if total_weight_available == 0:
            return 0.0

        signal_score = sum(
            (SOURCE_WEIGHTS.get(src, 0.0) / total_weight_available) * avg
            for src, avg in source_averages.items()
        )

        # Incorporar margen si hay datos de precio
        if price_buy and price_sell and price_sell > 0:
            margin_pct = float((price_sell - price_buy) / price_sell * 100)
            # Margen > 40% = score_margin = 100; < 10% = 0
            margin_score = max(0.0, min(100.0, (margin_pct - 10) * (100 / 30)))
            final_score = signal_score * BASE_WEIGHT + margin_score * MARGIN_WEIGHT
        else:
            final_score = signal_score

        return round(max(0.0, min(100.0, final_score)), 2)

    def rank_products(
        self,
        researches: list[ProductResearch],
        top_n: int = 10,
    ) -> list[ProductResearch]:
        """Ordena productos por composite_score DESC y retorna TOP N."""
        sorted_products = sorted(
            researches,
            key=lambda p: p.composite_score,
            reverse=True,
        )
        return sorted_products[:top_n]

    def aggregate_signals(
        self, all_signals: list[ProductSignal]
    ) -> list[ProductResearch]:
        """
        Agrupa señales por keyword (normalizando variantes) y crea ProductResearch.
        """
        by_keyword: dict[str, list[ProductSignal]] = defaultdict(list)
        for signal in all_signals:
            key = signal.keyword.lower().strip()
            by_keyword[key].append(signal)

        researches: list[ProductResearch] = []
        for keyword, signals in by_keyword.items():
            score = self.calculate_score(signals)
            researches.append(
                ProductResearch(
                    keyword=keyword,
                    signals=signals,
                    composite_score=score,
                )
            )

        return researches

    def enrich_with_dropi(
        self,
        researches: list[ProductResearch],
        dropi_catalog: list[dict],
    ) -> list[ProductResearch]:
        """
        Cruza los researches con el catálogo de Dropi.
        Los productos que existen en Dropi se marcan y se recalcula score con margen.
        """
        # Índice por keyword para búsqueda rápida
        dropi_by_keyword: dict[str, dict] = {}
        for product in dropi_catalog:
            name = (product.get("name") or "").lower()
            dropi_by_keyword[name] = product

        enriched: list[ProductResearch] = []
        for research in researches:
            kw = research.keyword.lower()
            # Búsqueda parcial: si el keyword aparece en algún nombre del catálogo
            matched_product = None
            for name, product in dropi_by_keyword.items():
                if kw in name or name in kw:
                    matched_product = product
                    break

            if matched_product:
                price_buy = matched_product.get("price_buy")
                price_sell = matched_product.get("price_sell")
                if price_buy and price_sell:
                    margin = float((Decimal(str(price_sell)) - Decimal(str(price_buy))) / Decimal(str(price_sell)) * 100)
                else:
                    margin = 0.0

                research = research.model_copy(update={
                    "in_dropi_catalog": True,
                    "dropi_product": matched_product,
                    "estimated_margin": round(margin, 1),
                    "composite_score": self.calculate_score(
                        research.signals,
                        price_buy=Decimal(str(price_buy)) if price_buy else None,
                        price_sell=Decimal(str(price_sell)) if price_sell else None,
                    ),
                })

            enriched.append(research)

        return enriched
