from agents.campaign.models import AdCopy, CampaignRequest, PlatformCampaignResult
from agents.campaign.platforms.base import AbstractAdsPlatform
from app.logger import get_logger

log = get_logger("campaign.google_ads")


class GoogleAdsClient(AbstractAdsPlatform):
    """
    Crea campañas Performance Max en Google Ads via google-ads Python SDK.

    IMPORTANTE: Requiere:
    - google_ads_customer_id configurado (no vacío)
    - google_ads_developer_token aprobado por Google
    - OAuth2 credentials (client_id, client_secret, refresh_token)
    - Landing page propia verificada en Google Merchant Center

    Si customer_id está vacío, retorna skipped=True sin error.
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
        self._ads_client = None

    def _is_configured(self) -> bool:
        return bool(
            self._customer_id
            and self._developer_token
            and self._client_id
            and self._client_secret
            and self._refresh_token
        )

    def _build_client(self):
        from google.ads.googleads.client import GoogleAdsClient as _GAClient

        return _GAClient.load_from_dict(
            {
                "developer_token": self._developer_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "login_customer_id": self._customer_id,
                "use_proto_plus": True,
            }
        )

    async def upload_image(self, image_bytes: bytes, filename: str) -> str:
        """Sube imagen como Asset. Retorna resource name."""
        if not self._is_configured():
            return ""
        try:
            client = self._build_client()
            asset_service = client.get_service("AssetService")
            asset_operation = client.get_type("AssetOperation")
            asset = asset_operation.create
            asset.name = filename
            asset.type_ = client.enums.AssetTypeEnum.IMAGE
            asset.image_asset.data = image_bytes

            response = asset_service.mutate_assets(
                customer_id=self._customer_id,
                operations=[asset_operation],
            )
            resource_name = response.results[0].resource_name
            log.info("Google Ads: imagen subida", resource_name=resource_name)
            return resource_name
        except Exception as exc:
            log.warning("Google Ads: fallo upload imagen", error=str(exc))
            return ""

    async def create_campaign(
        self, request: CampaignRequest, uploaded_image_ids: list[str]
    ) -> PlatformCampaignResult:
        if not self._is_configured():
            log.info("Google Ads no configurado — omitiendo", customer_id=self._customer_id[:4] + "..." if self._customer_id else "vacío")
            return PlatformCampaignResult(platform="google", success=False, skipped=True)

        copy = request.ad_copies.get("google")
        if not copy:
            copy = AdCopy(
                platform="google",
                headline=request.product_name[:30],
                body=request.product_name[:90],
                cta="Ver oferta",
            )

        try:
            client = self._build_client()
            campaign_id = await self._create_pmax_campaign(client, request, copy, uploaded_image_ids)
            log.info("Google Ads: campaña Performance Max creada", campaign_id=campaign_id)
            return PlatformCampaignResult(
                platform="google",
                success=True,
                campaign_id=campaign_id,
            )
        except Exception as exc:
            log.error("Error creando campaña Google Ads", error=str(exc))
            return PlatformCampaignResult(platform="google", success=False, error=str(exc))

    async def _create_pmax_campaign(
        self,
        client,
        request: CampaignRequest,
        copy: AdCopy,
        image_resource_names: list[str],
    ) -> str:
        """
        Crea Campaign (PERFORMANCE_MAX) + CampaignBudget + AssetGroup.
        Todas las mutaciones se envían en una sola llamada.
        """
        google_ads_service = client.get_service("GoogleAdsService")
        campaign_service = client.get_service("CampaignService")

        # ── Presupuesto ─────────────────────────────────────────────────────────
        budget_op = client.get_type("CampaignBudgetOperation")
        budget = budget_op.create
        budget.name = f"[AUTO] Budget {request.product_name}"
        budget.amount_micros = int(request.daily_budget_usd * 1_000_000)
        budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD

        # ── Campaña ─────────────────────────────────────────────────────────────
        campaign_op = client.get_type("CampaignOperation")
        campaign = campaign_op.create
        campaign.name = f"[AUTO] {request.product_name}"
        campaign.advertising_channel_type = (
            client.enums.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
        )
        campaign.status = client.enums.CampaignStatusEnum.PAUSED
        campaign.bidding_strategy_type = (
            client.enums.BiddingStrategyTypeEnum.MAXIMIZE_CONVERSION_VALUE
        )

        # ── AssetGroup ──────────────────────────────────────────────────────────
        asset_group_op = client.get_type("AssetGroupOperation")
        asset_group = asset_group_op.create
        asset_group.name = f"[AUTO] AssetGroup {request.product_name}"
        asset_group.final_urls.append(request.product_url)
        asset_group.status = client.enums.AssetGroupStatusEnum.PAUSED

        # Assets de texto
        headlines = [copy.headline, request.product_name[:30], "Oferta Colombia"]
        for headline in headlines[:3]:
            text_asset_op = client.get_type("AssetOperation")
            text_asset_op.create.text_asset.text = headline
            asset_group.assets.append(text_asset_op.create.resource_name)

        # Mutación combinada
        response = google_ads_service.mutate(
            customer_id=self._customer_id,
            mutate_operations=[
                {"campaign_budget_operation": budget_op},
                {"campaign_operation": campaign_op},
                {"asset_group_operation": asset_group_op},
            ],
        )

        # Extraer campaign_id del resource name
        for result in response.mutate_operation_responses:
            if result.HasField("campaign_result"):
                resource_name = result.campaign_result.resource_name
                # resource_name format: customers/{customer_id}/campaigns/{campaign_id}
                return resource_name.split("/")[-1]

        raise RuntimeError("Google Ads no retornó campaign resource_name")
