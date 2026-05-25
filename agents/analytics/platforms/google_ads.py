from datetime import date

from agents.analytics.models import MetricSnapshot
from app.logger import get_logger

log = get_logger("analytics.google_ads")


class GoogleAdsReportClient:
    """
    Lee métricas y ejecuta acciones de optimización en Google Ads (GAQL).
    Si customer_id está vacío, todos los métodos retornan None/False sin error.
    """

    def __init__(
        self,
        developer_token: str,
        customer_id: str,
        client_id: str = "",
        client_secret: str = "",
        refresh_token: str = "",
    ) -> None:
        self._developer_token = developer_token
        self._customer_id = customer_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token

    def _is_configured(self) -> bool:
        return bool(
            self._customer_id
            and self._developer_token
            and self._client_id
            and self._client_secret
            and self._refresh_token
        )

    def _build_client(self):
        from google.ads.googleads.client import GoogleAdsClient

        return GoogleAdsClient.load_from_dict(
            {
                "developer_token": self._developer_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "login_customer_id": self._customer_id,
                "use_proto_plus": True,
            }
        )

    async def get_campaign_metrics(
        self, campaign_id: str, campaign_db_id: str, target_date: date
    ) -> MetricSnapshot | None:
        """Obtiene métricas de Performance Max via GAQL."""
        if not self._is_configured():
            return None

        date_str = target_date.strftime("%Y-%m-%d")
        query = f"""
            SELECT
                campaign.id,
                metrics.impressions,
                metrics.clicks,
                metrics.cost_micros,
                metrics.conversions,
                metrics.conversions_value
            FROM campaign
            WHERE campaign.id = {campaign_id}
              AND segments.date = '{date_str}'
        """
        try:
            client = self._build_client()
            gas = client.get_service("GoogleAdsService")
            response = gas.search(customer_id=self._customer_id, query=query)
            for row in response:
                m = row.metrics
                cost = m.cost_micros / 1_000_000
                return MetricSnapshot(
                    campaign_db_id=campaign_db_id,
                    external_id=campaign_id,
                    platform="google",
                    date=target_date,
                    impressions=m.impressions,
                    clicks=m.clicks,
                    conversions=int(m.conversions),
                    spend_usd=cost,
                    revenue_usd=m.conversions_value,
                )
            log.info("Google Ads: sin datos para la fecha", campaign_id=campaign_id, date=date_str)
            return None
        except Exception as exc:
            log.warning("Google Ads: error obteniendo métricas", campaign_id=campaign_id, error=str(exc))
            return None

    async def pause_campaign(self, campaign_id: str) -> bool:
        """Pausa una campaña Performance Max."""
        if not self._is_configured():
            return False
        try:
            client = self._build_client()
            campaign_service = client.get_service("CampaignService")
            op = client.get_type("CampaignOperation")
            op.update.resource_name = f"customers/{self._customer_id}/campaigns/{campaign_id}"
            op.update.status = client.enums.CampaignStatusEnum.PAUSED
            client.copy_from(
                op.update_mask,
                client.get_pb(op.update)  # field mask
            )
            campaign_service.mutate_campaigns(
                customer_id=self._customer_id, operations=[op]
            )
            log.info("Google Ads: campaña pausada", campaign_id=campaign_id)
            return True
        except Exception as exc:
            log.error("Google Ads: fallo al pausar", campaign_id=campaign_id, error=str(exc))
            return False

    async def update_campaign_budget(self, budget_resource_name: str, daily_budget_usd: float) -> bool:
        """Actualiza el presupuesto diario. budget_resource_name en formato customers/X/campaignBudgets/Y."""
        if not self._is_configured():
            return False
        try:
            client = self._build_client()
            budget_service = client.get_service("CampaignBudgetService")
            op = client.get_type("CampaignBudgetOperation")
            op.update.resource_name = budget_resource_name
            op.update.amount_micros = int(daily_budget_usd * 1_000_000)
            budget_service.mutate_campaign_budgets(
                customer_id=self._customer_id, operations=[op]
            )
            log.info("Google Ads: budget actualizado", budget=daily_budget_usd)
            return True
        except Exception as exc:
            log.error("Google Ads: fallo al actualizar budget", error=str(exc))
            return False
