import json
from datetime import date

import httpx

from agents.analytics.models import MetricSnapshot
from app.logger import get_logger

log = get_logger("analytics.meta")

BASE_URL = "https://graph.facebook.com/v21.0"

_PURCHASE_TYPES = {"offsite_conversion.fb_pixel_purchase", "purchase", "omni_purchase"}


class MetaInsightsClient:
    """
    Lee métricas y ejecuta acciones de optimización en Meta Ads (Graph API v21.0).
    """

    def __init__(self, access_token: str, ad_account_id: str) -> None:
        self._token = access_token
        self._account = ad_account_id
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MetaInsightsClient":
        self._client = httpx.AsyncClient(
            params={"access_token": self._token},
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def get_campaign_metrics(
        self, campaign_id: str, campaign_db_id: str, target_date: date
    ) -> MetricSnapshot | None:
        """Obtiene métricas de una campaña para un día específico."""
        date_str = target_date.strftime("%Y-%m-%d")
        params = {
            "fields": "impressions,clicks,spend,actions,action_values",
            "time_range": json.dumps({"since": date_str, "until": date_str}),
            "level": "campaign",
        }
        try:
            response = await self._client.get(f"{BASE_URL}/{campaign_id}/insights", params=params)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            log.warning("Meta Insights: error obteniendo métricas", campaign_id=campaign_id, error=str(exc))
            return None

        rows = data.get("data", [])
        if not rows:
            log.info("Meta Insights: sin datos para la fecha", campaign_id=campaign_id, date=date_str)
            return None

        row = rows[0]
        revenue = sum(
            float(av["value"])
            for av in row.get("action_values", [])
            if av.get("action_type") in _PURCHASE_TYPES
        )
        conversions = sum(
            int(a["value"])
            for a in row.get("actions", [])
            if a.get("action_type") in _PURCHASE_TYPES
        )
        return MetricSnapshot(
            campaign_db_id=campaign_db_id,
            external_id=campaign_id,
            platform="meta",
            date=target_date,
            impressions=int(row.get("impressions", 0)),
            clicks=int(row.get("clicks", 0)),
            conversions=conversions,
            spend_usd=float(row.get("spend", 0.0)),
            revenue_usd=revenue,
        )

    async def pause_campaign(self, campaign_id: str) -> bool:
        """Pausa una campaña. Retorna True si exitoso."""
        try:
            response = await self._client.post(
                f"{BASE_URL}/{campaign_id}",
                json={"status": "PAUSED"},
            )
            response.raise_for_status()
            log.info("Meta: campaña pausada", campaign_id=campaign_id)
            return True
        except Exception as exc:
            log.error("Meta: fallo al pausar campaña", campaign_id=campaign_id, error=str(exc))
            return False

    async def update_adset_budget(self, adset_id: str, daily_budget_usd: float) -> bool:
        """Actualiza el presupuesto diario de un adset. Meta usa centavos."""
        daily_budget_cents = int(daily_budget_usd * 100)
        try:
            response = await self._client.post(
                f"{BASE_URL}/{adset_id}",
                json={"daily_budget": daily_budget_cents},
            )
            response.raise_for_status()
            log.info("Meta: budget actualizado", adset_id=adset_id, budget_usd=daily_budget_usd)
            return True
        except Exception as exc:
            log.error("Meta: fallo al actualizar budget", adset_id=adset_id, error=str(exc))
            return False
