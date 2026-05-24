from decimal import Decimal

from pydantic import BaseModel, Field


class AdCopy(BaseModel):
    platform: str
    headline: str
    body: str
    cta: str


class CampaignRequest(BaseModel):
    product_id: str
    dropi_id: str
    product_name: str
    product_url: str
    image_urls: list[str]
    price_sell: Decimal
    category: str
    daily_budget_usd: float = Field(default=10.0, ge=1.0, le=50.0)
    ad_copies: dict[str, AdCopy] = Field(default_factory=dict)


class PlatformCampaignResult(BaseModel):
    platform: str
    success: bool
    campaign_id: str | None = None
    adset_id: str | None = None
    ad_id: str | None = None
    error: str | None = None
    skipped: bool = False

    @property
    def external_id(self) -> str | None:
        return self.campaign_id


class CampaignResult(BaseModel):
    product_id: str
    results: list[PlatformCampaignResult]

    @property
    def successful_platforms(self) -> list[str]:
        return [r.platform for r in self.results if r.success]

    @property
    def failed_platforms(self) -> list[str]:
        return [r.platform for r in self.results if not r.success and not r.skipped]
