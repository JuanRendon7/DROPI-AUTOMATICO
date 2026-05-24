from abc import ABC, abstractmethod

from agents.campaign.models import CampaignRequest, PlatformCampaignResult


class AbstractAdsPlatform(ABC):

    @abstractmethod
    async def upload_image(self, image_bytes: bytes, filename: str) -> str:
        """Sube imagen a la plataforma. Retorna el ID/hash específico de la plataforma."""
        ...

    @abstractmethod
    async def create_campaign(
        self, request: CampaignRequest, uploaded_image_ids: list[str]
    ) -> PlatformCampaignResult:
        """Crea campaña completa (campaign + adset/adgroup + ad). Retorna resultado."""
        ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass
