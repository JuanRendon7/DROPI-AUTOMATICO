import httpx

from agents.campaign.models import AdCopy, CampaignRequest, PlatformCampaignResult
from agents.campaign.platforms.base import AbstractAdsPlatform
from app.logger import get_logger

log = get_logger("campaign.meta")

BASE_URL = "https://graph.facebook.com/v21.0"

# Mapa de texto CTA a tipo Meta
_CTA_MAP = {
    "comprar": "SHOP_NOW",
    "compra": "SHOP_NOW",
    "ver más": "LEARN_MORE",
    "ver mas": "LEARN_MORE",
    "saber más": "LEARN_MORE",
    "saber mas": "LEARN_MORE",
    "obtener": "GET_OFFER",
    "pedir": "ORDER_NOW",
    "ordenar": "ORDER_NOW",
}

_DEFAULT_CTA = "SHOP_NOW"


def _resolve_cta(cta_text: str) -> str:
    lowered = cta_text.lower().strip()
    for key, value in _CTA_MAP.items():
        if key in lowered:
            return value
    return _DEFAULT_CTA


class MetaAdsClient(AbstractAdsPlatform):
    """
    Crea campañas en Meta Ads (Facebook + Instagram) via Graph API v21.0.
    Usa httpx directamente — sin facebook-business SDK.
    """

    def __init__(self, access_token: str, ad_account_id: str, page_id: str) -> None:
        # ad_account_id debe incluir prefijo "act_"
        self._token = access_token
        self._account = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        self._page_id = page_id
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MetaAdsClient":
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def upload_image(self, image_bytes: bytes, filename: str) -> str:
        """Sube imagen a la cuenta de anuncios. Retorna image_hash."""
        response = await self._client.post(
            f"{BASE_URL}/{self._account}/adimages",
            files={"filename": (filename, image_bytes, "image/jpeg")},
        )
        response.raise_for_status()
        data = response.json()
        images = data.get("images", {})
        # El hash está anidado bajo el nombre del archivo
        for img_data in images.values():
            return img_data["hash"]
        raise ValueError(f"Meta no retornó image_hash: {data}")

    async def _create_campaign(self, name: str) -> str:
        response = await self._client.post(
            f"{BASE_URL}/{self._account}/campaigns",
            json={
                "name": name,
                "objective": "OUTCOME_TRAFFIC",
                "status": "PAUSED",
                "special_ad_categories": [],
            },
        )
        response.raise_for_status()
        return response.json()["id"]

    async def _create_adset(self, campaign_id: str, request: CampaignRequest) -> str:
        # Meta espera el budget en centavos (USD)
        daily_budget_cents = int(request.daily_budget_usd * 100)
        response = await self._client.post(
            f"{BASE_URL}/{self._account}/adsets",
            json={
                "name": f"{request.product_name} — AdSet",
                "campaign_id": campaign_id,
                "billing_event": "IMPRESSIONS",
                "optimization_goal": "LINK_CLICKS",
                "daily_budget": daily_budget_cents,
                "targeting": {
                    "geo_locations": {"countries": ["CO"]},
                    "age_min": 18,
                    "age_max": 55,
                },
                "status": "PAUSED",
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            },
        )
        response.raise_for_status()
        return response.json()["id"]

    async def _create_creative(self, image_hash: str, request: CampaignRequest, copy: AdCopy) -> str:
        cta_type = _resolve_cta(copy.cta)
        response = await self._client.post(
            f"{BASE_URL}/{self._account}/adcreatives",
            json={
                "name": f"{request.product_name} — Creative",
                "object_story_spec": {
                    "page_id": self._page_id,
                    "link_data": {
                        "image_hash": image_hash,
                        "link": request.product_url,
                        "message": copy.body,
                        "name": copy.headline,
                        "call_to_action": {"type": cta_type},
                    },
                },
            },
        )
        response.raise_for_status()
        return response.json()["id"]

    async def _create_ad(self, adset_id: str, creative_id: str, name: str) -> str:
        response = await self._client.post(
            f"{BASE_URL}/{self._account}/ads",
            json={
                "name": name,
                "adset_id": adset_id,
                "creative": {"creative_id": creative_id},
                "status": "PAUSED",
            },
        )
        response.raise_for_status()
        return response.json()["id"]

    async def create_campaign(
        self, request: CampaignRequest, uploaded_image_ids: list[str]
    ) -> PlatformCampaignResult:
        copy = request.ad_copies.get("facebook") or request.ad_copies.get("meta")
        if not copy:
            copy = AdCopy(
                platform="meta",
                headline=request.product_name[:125],
                body=f"¡Consigue {request.product_name} al mejor precio!",
                cta="Comprar",
            )

        image_hash = uploaded_image_ids[0] if uploaded_image_ids else None

        try:
            campaign_name = f"[AUTO] {request.product_name}"
            campaign_id = await self._create_campaign(campaign_name)
            adset_id = await self._create_adset(campaign_id, request)

            ad_id: str | None = None
            if image_hash:
                creative_id = await self._create_creative(image_hash, request, copy)
                ad_id = await self._create_ad(adset_id, creative_id, campaign_name)

            log.info("Campaña Meta creada", campaign_id=campaign_id, ad_id=ad_id)
            return PlatformCampaignResult(
                platform="meta",
                success=True,
                campaign_id=campaign_id,
                adset_id=adset_id,
                ad_id=ad_id,
            )
        except Exception as exc:
            log.error("Error creando campaña Meta", error=str(exc))
            return PlatformCampaignResult(platform="meta", success=False, error=str(exc))
