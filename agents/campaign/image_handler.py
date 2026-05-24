import httpx

from app.logger import get_logger

log = get_logger("campaign.image_handler")

MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB — límite seguro para Meta y TikTok
_EXT_MAP = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class ImageHandler:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "ImageHandler":
        if self._owns_client:
            self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        return self

    async def __aexit__(self, *_) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()

    async def download(self, url: str) -> tuple[bytes, str]:
        """Descarga imagen. Retorna (bytes, filename)."""
        response = await self._client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        ext = _EXT_MAP.get(content_type, "jpg")
        image_bytes = response.content
        if len(image_bytes) > MAX_IMAGE_BYTES:
            log.warning("Imagen supera 4 MB — puede fallar upload", url=url, size_mb=len(image_bytes) // (1024 * 1024))
        return image_bytes, f"product.{ext}"

    async def download_first_valid(self, urls: list[str]) -> tuple[bytes, str] | None:
        """Intenta descargar URLs en orden hasta conseguir una válida."""
        for url in urls:
            try:
                result = await self.download(url)
                log.info("Imagen descargada", url=url, size=len(result[0]))
                return result
            except Exception as exc:
                log.warning("Fallo descarga de imagen — probando siguiente", url=url, error=str(exc))
        log.error("No se pudo descargar ninguna imagen", total_urls=len(urls))
        return None
