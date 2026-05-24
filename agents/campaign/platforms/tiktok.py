import httpx

from agents.campaign.models import AdCopy, CampaignRequest, PlatformCampaignResult
from agents.campaign.platforms.base import AbstractAdsPlatform
from app.logger import get_logger

log = get_logger("campaign.tiktok")

BASE_URL = "https://business-api.tiktok.com/open_api/v1.3"

# Colombia location ID (ISO 3166 numeric)
COLOMBIA_LOCATION_ID = "6252001"


class TikTokAdsClient(AbstractAdsPlatform):
    """
    Crea campañas en TikTok Ads via Marketing API v1.3.
    Usa httpx directamente — sin SDK de TikTok.
    """

    def __init__(self, access_token: str, advertiser_id: str) -> None:
        self._token = access_token
        self._advertiser_id = advertiser_id
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "TikTokAdsClient":
        self._client = httpx.AsyncClient(
            headers={"Access-Token": self._token},
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    def _check_response(self, data: dict, action: str) -> None:
        """Verifica que la respuesta de TikTok no contenga errores."""
        code = data.get("code", 0)
        if code != 0:
            message = data.get("message", "Error desconocido")
            raise RuntimeError(f"TikTok API [{action}] code={code}: {message}")

    async def upload_image(self, image_bytes: bytes, filename: str) -> str:
        """Sube imagen. Retorna image_id."""
        response = await self._client.post(
            f"{BASE_URL}/file/image/ad/upload/",
            data={"advertiser_id": self._advertiser_id},
            files={"image_file": (filename, image_bytes, "image/jpeg")},
        )
        response.raise_for_status()
        data = response.json()
        self._check_response(data, "upload_image")
        return data["data"]["image_id"]

    async def _create_campaign(self, name: str, budget: float) -> str:
        response = await self._client.post(
            f"{BASE_URL}/campaign/create/",
            json={
                "advertiser_id": self._advertiser_id,
                "campaign_name": name,
                "objective_type": "TRAFFIC",
                "budget_mode": "BUDGET_MODE_DAY",
                "budget": budget,
            },
        )
        response.raise_for_status()
        data = response.json()
        self._check_response(data, "create_campaign")
        return data["data"]["campaign_id"]

    async def _create_adgroup(self, campaign_id: str, name: str, budget: float) -> str:
        response = await self._client.post(
            f"{BASE_URL}/adgroup/create/",
            json={
                "advertiser_id": self._advertiser_id,
                "campaign_id": campaign_id,
                "adgroup_name": name,
                "placement_type": "PLACEMENT_TYPE_AUTOMATIC",
                "location_ids": [COLOMBIA_LOCATION_ID],
                "budget_mode": "BUDGET_MODE_DAY",
                "budget": budget,
                "schedule_type": "SCHEDULE_FROM_NOW",
                "optimize_goal": "CLICK",
                "billing_event": "CPC",
                "bid_type": "BID_TYPE_NO_BID",
            },
        )
        response.raise_for_status()
        data = response.json()
        self._check_response(data, "create_adgroup")
        return data["data"]["adgroup_id"]

    async def _create_ad(
        self, adgroup_id: str, image_id: str, request: CampaignRequest, copy: AdCopy
    ) -> str:
        response = await self._client.post(
            f"{BASE_URL}/ad/create/",
            json={
                "advertiser_id": self._advertiser_id,
                "adgroup_id": adgroup_id,
                "ad_name": f"[AUTO] {request.product_name}",
                "ad_format": "SINGLE_IMAGE",
                "image_ids": [image_id],
                "ad_text": copy.body,
                "landing_page_url": request.product_url,
            },
        )
        response.raise_for_status()
        data = response.json()
        self._check_response(data, "create_ad")
        return data["data"]["ad_id"]

    async def create_campaign(
        self, request: CampaignRequest, uploaded_image_ids: list[str]
    ) -> PlatformCampaignResult:
        copy = request.ad_copies.get("tiktok")
        if not copy:
            copy = AdCopy(
                platform="tiktok",
                headline=request.product_name[:100],
                body=f"¡{request.product_name} al mejor precio! 🔥 Envío gratis.",
                cta="Ver más",
            )

        image_id = uploaded_image_ids[0] if uploaded_image_ids else None

        try:
            campaign_name = f"[AUTO] {request.product_name}"
            campaign_id = await self._create_campaign(campaign_name, request.daily_budget_usd)
            adgroup_id = await self._create_adgroup(campaign_id, f"{request.product_name} — AdGroup", request.daily_budget_usd)

            ad_id: str | None = None
            if image_id:
                ad_id = await self._create_ad(adgroup_id, image_id, request, copy)

            log.info("Campaña TikTok creada", campaign_id=campaign_id, ad_id=ad_id)
            return PlatformCampaignResult(
                platform="tiktok",
                success=True,
                campaign_id=campaign_id,
                adset_id=adgroup_id,
                ad_id=ad_id,
            )
        except Exception as exc:
            log.error("Error creando campaña TikTok", error=str(exc))
            return PlatformCampaignResult(platform="tiktok", success=False, error=str(exc))
