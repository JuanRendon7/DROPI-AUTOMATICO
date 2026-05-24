from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agents.dropi.models import DropiProductRaw


class ProductSignal(BaseModel):
    """Señal de tendencia de una fuente específica."""

    source: str  # google_trends | amazon | tiktok | mercadolibre | reddit
    keyword: str
    trend_score: float = Field(ge=0.0, le=100.0)
    rank: int | None = None
    volume: int | None = None
    growth_rate: float = 0.0  # positivo = creciendo
    fetched_at: datetime = Field(default_factory=datetime.now)


class ProductResearch(BaseModel):
    """Datos agregados de investigación para un producto/keyword."""

    keyword: str
    signals: list[ProductSignal] = []
    composite_score: float = Field(default=0.0, ge=0.0, le=100.0)
    estimated_margin: float = 0.0  # % margen (0–100)
    dropi_product: dict | None = None  # DropiProductRaw serializado
    in_dropi_catalog: bool = False

    @property
    def sources_present(self) -> list[str]:
        return list({s.source for s in self.signals})

    @property
    def average_trend_score(self) -> float:
        if not self.signals:
            return 0.0
        return sum(s.trend_score for s in self.signals) / len(self.signals)


class ResearchShortlist(BaseModel):
    """Resultado final del Research Agent — TOP 10 productos."""

    generated_at: datetime = Field(default_factory=datetime.now)
    top_products: list[ProductResearch]
    analysis: str = ""
    sources_used: list[str] = []
    total_keywords_analyzed: int = 0
    execution_time_seconds: float = 0.0

    def to_summary(self) -> str:
        lines = [f"ResearchShortlist — {self.generated_at.strftime('%Y-%m-%d %H:%M')}"]
        lines.append(f"Fuentes: {', '.join(self.sources_used)}")
        lines.append(f"Keywords analizados: {self.total_keywords_analyzed}")
        lines.append(f"Top {len(self.top_products)} productos:")
        for i, p in enumerate(self.top_products, 1):
            lines.append(f"  {i}. {p.keyword} (score={p.composite_score:.1f})")
        return "\n".join(lines)
