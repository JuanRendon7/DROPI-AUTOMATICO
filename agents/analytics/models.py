from datetime import date

from pydantic import BaseModel


class MetricSnapshot(BaseModel):
    """Métricas de una campaña para un día específico."""
    campaign_db_id: str
    external_id: str
    platform: str
    date: date
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    spend_usd: float = 0.0
    revenue_usd: float = 0.0

    @property
    def roas(self) -> float:
        return round(self.revenue_usd / self.spend_usd, 4) if self.spend_usd > 0 else 0.0

    @property
    def ctr(self) -> float:
        return round(self.clicks / self.impressions, 6) if self.impressions > 0 else 0.0

    @property
    def cpc(self) -> float:
        return round(self.spend_usd / self.clicks, 4) if self.clicks > 0 else 0.0


class OptimizationAction(BaseModel):
    """Resultado de una decisión autónoma del optimizer."""
    campaign_db_id: str
    external_id: str
    platform: str
    action: str          # "pause" | "scale_budget" | "alert_spike" | "flag_low_ctr"
    reason: str
    old_value: float | None = None
    new_value: float | None = None
    executed: bool = False
    error: str | None = None


class WeeklyReport(BaseModel):
    """Reporte semanal generado por Claude."""
    week_start: date
    week_end: date
    total_spend_usd: float
    total_revenue_usd: float
    overall_roas: float
    top_campaign: str | None = None
    worst_campaign: str | None = None
    analysis_text: str = ""
    actions_taken: list[OptimizationAction] = []
