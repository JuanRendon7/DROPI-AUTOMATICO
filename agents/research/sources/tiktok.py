from datetime import datetime

from agents.research.models import ProductSignal
from app.logger import get_logger

log = get_logger("research.tiktok")


class TikTokSource:
    """
    Fuente de tendencias de TikTok usando TikTokApi (librería no oficial).
    Falla de forma elegante — TikTok puede cambiar su API en cualquier momento.
    """

    async def get_trending_products(self, count: int = 30) -> list[ProductSignal]:
        """
        Intenta obtener productos/videos trending de TikTok.
        Si falla (error de auth, cambio de API, etc.), retorna [] sin bloquear.
        """
        try:
            from TikTokApi import TikTokApi  # type: ignore[import]

            async with TikTokApi() as api:
                await api.create_sessions(headless=True, num_sessions=1, sleep_after=3)
                videos = api.trending.videos(count=count)

                keywords: dict[str, int] = {}
                async for video in videos:
                    desc = getattr(video, "desc", "") or ""
                    # Extraer hashtags del texto del video
                    tags = [
                        word.lstrip("#").lower()
                        for word in desc.split()
                        if word.startswith("#") and len(word) > 3
                    ]
                    for tag in tags:
                        keywords[tag] = keywords.get(tag, 0) + 1

                # Convertir frecuencia a señales
                sorted_kws = sorted(keywords.items(), key=lambda x: x[1], reverse=True)[:20]
                signals: list[ProductSignal] = []
                max_count = sorted_kws[0][1] if sorted_kws else 1

                for kw, freq in sorted_kws:
                    signals.append(
                        ProductSignal(
                            source="tiktok",
                            keyword=kw,
                            trend_score=(freq / max_count) * 100,
                            volume=freq,
                            fetched_at=datetime.now(),
                        )
                    )

                log.info("TikTok: trending obtenido", count=len(signals))
                return signals

        except ImportError:
            log.warning("TikTokApi no instalado — saltando fuente TikTok")
            return []
        except Exception as e:
            log.warning("TikTok: error obteniendo trending (fuente no crítica)", error=str(e))
            return []

    async def get_hashtag_volume(self, hashtag: str) -> int:
        """Número de videos con un hashtag. Retorna 0 si falla."""
        try:
            from TikTokApi import TikTokApi  # type: ignore[import]

            async with TikTokApi() as api:
                await api.create_sessions(headless=True, num_sessions=1, sleep_after=3)
                tag = api.hashtag(name=hashtag)
                info = await tag.info()
                return int(getattr(info, "views", 0) or 0)

        except Exception as e:
            log.debug("TikTok: no se pudo obtener volumen de hashtag", hashtag=hashtag, error=str(e))
            return 0
