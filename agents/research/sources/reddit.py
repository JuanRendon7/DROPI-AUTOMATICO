import re
from datetime import datetime

from agents.research.models import ProductSignal
from app.logger import get_logger

log = get_logger("research.reddit")

_DROPSHIPPING_SUBREDDITS = [
    "dropshipping",
    "Dropship",
    "ecommerce",
    "sidehustle",
    "AmazonFBA",
]

# Palabras a filtrar que no son productos
_STOPWORDS = {
    "dropshipping", "store", "business", "supplier", "product", "sell",
    "customer", "shipping", "order", "payment", "profit", "margin",
    "help", "advice", "question", "review", "think", "anyone",
}


def _extract_keywords(text: str) -> list[str]:
    """Extrae posibles nombres de productos de un texto."""
    words = re.findall(r'\b[A-Za-záéíóúüñÁÉÍÓÚÜÑ]{4,}\b', text.lower())
    return [w for w in words if w not in _STOPWORDS]


class RedditSource:
    """
    Fuente de señales de demanda via Reddit usando asyncpraw.
    Extrae keywords de posts trending en subreddits de dropshipping.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str = "dropi-sales-machine/1.0",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent

    def _is_configured(self) -> bool:
        if not self._client_id or not self._client_secret:
            log.warning("Reddit API no configurada — saltando fuente Reddit")
            return False
        return True

    async def get_hot_posts(
        self, subreddit: str = "dropshipping", limit: int = 25
    ) -> list[str]:
        """Extrae keywords de posts 'hot' del subreddit."""
        if not self._is_configured():
            return []
        try:
            import asyncpraw  # type: ignore[import]

            reddit = asyncpraw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                user_agent=self._user_agent,
            )
            sub = await reddit.subreddit(subreddit)
            keywords: dict[str, int] = {}

            async for post in sub.hot(limit=limit):
                text = f"{post.title} {getattr(post, 'selftext', '')}"
                for kw in _extract_keywords(text):
                    keywords[kw] = keywords.get(kw, 0) + 1

            await reddit.close()
            sorted_kws = sorted(keywords.items(), key=lambda x: x[1], reverse=True)
            return [kw for kw, _ in sorted_kws[:20]]

        except ImportError:
            log.warning("asyncpraw no instalado — saltando fuente Reddit")
            return []
        except Exception as e:
            log.warning("Reddit: error obteniendo posts", subreddit=subreddit, error=str(e))
            return []

    async def get_product_mentions(
        self, keyword: str, limit: int = 25
    ) -> ProductSignal:
        """Cuenta menciones de un keyword en subreddits relevantes."""
        total_mentions = 0
        total_upvotes = 0

        if self._is_configured():
            try:
                import asyncpraw  # type: ignore[import]

                reddit = asyncpraw.Reddit(
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                    user_agent=self._user_agent,
                )
                subreddits = "+".join(_DROPSHIPPING_SUBREDDITS[:3])
                sub = await reddit.subreddit(subreddits)

                async for post in sub.search(keyword, limit=limit, sort="relevance"):
                    if keyword.lower() in post.title.lower():
                        total_mentions += 1
                        total_upvotes += post.score

                await reddit.close()

            except Exception as e:
                log.debug("Reddit: error buscando keyword", keyword=keyword, error=str(e))

        # Score basado en menciones + upvotes (normalizado a 0–100)
        mention_score = min(total_mentions * 10, 60)
        upvote_score = min(total_upvotes / 100, 40)

        return ProductSignal(
            source="reddit",
            keyword=keyword,
            trend_score=mention_score + upvote_score,
            volume=total_mentions,
            fetched_at=datetime.now(),
        )

    async def get_trending_signals(self) -> list[ProductSignal]:
        """Obtiene señales de trending desde múltiples subreddits."""
        all_keywords: dict[str, int] = {}

        for subreddit in _DROPSHIPPING_SUBREDDITS[:3]:
            keywords = await self.get_hot_posts(subreddit)
            for kw in keywords:
                all_keywords[kw] = all_keywords.get(kw, 0) + 1

        if not all_keywords:
            return []

        sorted_kws = sorted(all_keywords.items(), key=lambda x: x[1], reverse=True)[:15]
        max_count = sorted_kws[0][1] if sorted_kws else 1

        signals: list[ProductSignal] = []
        for kw, count in sorted_kws:
            signals.append(
                ProductSignal(
                    source="reddit",
                    keyword=kw,
                    trend_score=(count / max_count) * 100,
                    volume=count,
                    fetched_at=datetime.now(),
                )
            )

        log.info("Reddit: señales obtenidas", count=len(signals))
        return signals
