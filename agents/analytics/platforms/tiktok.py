from datetime import date

import httpx

from agents.analytics.models import MetricSnapshot
from app.logger import get_logger

log = get_logger("analytics.tiktok")

BASE_URL = "https://business-api.tiktok.com/open_api/v1.3"


class TikTokReportClient:
    """
    Lee métricas y ejecuta acciones de optimización en TikTok Ads (v1.3).
    """

    def __init__(self, access_token: str, advertiser_id: str) -> None:
        self._token = access_token
        self._advertiser_id = advertiser_id
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "TikTokReportClient":
        self._client = httpx.AsyncClient(
            headers={"Access-Token": self._token},
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    def _check(self, data: dict, action: str) -> None:
        code = data.get("code", 0)
        if code != 0:
            raise RuntimeError(f"TikTok [{action}] code={code}: {data.get('message', '')}")

    async def get_campaign_metrics(
        self, campaign_id: str, campaign_db_id: str, target_date: date
    ) -> MetricSnapshot | None:
        """Obtiene métricas de una campaña para un día específico."""
        date_str = target_date.strftime("%Y-%m-%d")
        body = {
            "advertiser_id": self._advertiser_id,
            "report_type": "BASIC",
            "data_level": "AUCTION_CAMPAIGN",
            "dimensions": ["campaign_id", "stat_time_day"],
            "metrics": ["spend", "impressions", "clicks", "conversions", "total_purchase_value"],
            "filters": [
                {
                    "field_name": "campaign_ids",
                    "filter_type": "IN",
                    "filter_value": f'["{campaign_id}"]',
                }
            ],
            "start_date": date_str,
            "end_date": date_str,
            "page_size": 10,
        }
        try:
            response = await self._client.post(f"{BASE_URL}/report/integrated/get/", json=body)
            response.raise_for_status()
            data = response.json()
            self._check(data, "get_campaign_metrics")
        except Exception as exc:
            log.warning("TikTok Report: error obteniendo métricas", campaign_id=campaign_id, error=str(exc))
            return None

        rows = data.get("data", {}).get("list", [])
        if not rows:
            log.info("TikTok Report: sin datos para la fecha", campaign_id=campaign_id, date=date_str)
            return None

        m = rows[0].get("metrics", {})
        spend = float(m.get("spend", 0.0))
        return MetricSnapshot(
            campaign_db_id=campaign_db_id,
            external_id=campaign_id,
            platform="tiktok",
            date=target_date,
            impressions=int(m.get("impressions", 0)),
            clicks=int(m.get("clicks", 0)),
            conversions=int(m.get("conversions", 0)),
            spend_usd=spend,
            revenue_usd=float(m.get("total_purchase_value", 0.0)),
        )

    async def pause_campaign(self, campaign_id: str) -> bool:
        """Pausa una campaña en TikTok."""
        try:
            response = await self._client.post(
                f"{BASE_URL}/campaign/update/",
                json={
                    "advertiser_id": self._advertiser_id,
                    "campaign_id": campaign_id,
                    "operation_status": "DISABLE",
                },
            )
            response.raise_for_status()
            data = response.json()
            self._check(data, "pause_campaign")
            log.info("TikTok: campaña pausada", campaign_id=campaign_id)
            return True
        except Exception as exc:
            log.error("TikTok: fallo al pausar campaña", campaign_id=campaign_id, error=str(exc))
            return False

    async def update_adgroup_budget(self, adgroup_id: str, daily_budget_usd: float) -> bool:
        """Actualiza presupuesto diario de un adgroup."""
        try:
            response = await self._client.post(
                f"{BASE_URL}/adgroup/update/",
                json={
                    "advertiser_id": self._advertiser_id,
                    "adgroup_id": adgroup_id,
                    "budget": daily_budget_usd,
                },
            )
            response.raise_for_status()
            data = response.json()
            self._check(data, "update_adgroup_budget")
            log.info("TikTok: budget actualizado", adgroup_id=adgroup_id, budget=daily_budget_usd)
            return True
        except Exception as exc:
            log.error("TikTok: fallo al actualizar budget", adgroup_id=adgroup_id, error=str(exc))
            return False
