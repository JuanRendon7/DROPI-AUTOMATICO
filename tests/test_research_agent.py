"""
Tests del Research Agent.
Usan mocks de httpx (respx) y mocks de Anthropic — sin APIs reales.
"""
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from httpx import Response

from agents.research.models import ProductResearch, ProductSignal, ResearchShortlist
from agents.research.scorer import ProductScorer
from app.models import Product


# ── Fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_signals() -> list[ProductSignal]:
    return [
        ProductSignal(source="google_trends", keyword="audífonos bluetooth", trend_score=85.0),
        ProductSignal(source="amazon", keyword="audífonos bluetooth", trend_score=70.0, rank=3),
        ProductSignal(source="mercadolibre", keyword="audífonos bluetooth", trend_score=60.0),
        ProductSignal(source="tiktok", keyword="audífonos", trend_score=55.0),
    ]


@pytest.fixture
def sample_research(sample_signals) -> ProductResearch:
    return ProductResearch(
        keyword="audífonos bluetooth",
        signals=sample_signals,
        composite_score=72.5,
    )


# ── Tests: ProductSignal / ProductResearch ──────────────────────────────────────


def test_product_signal_score_bounds():
    s = ProductSignal(source="google_trends", keyword="test", trend_score=100.0)
    assert 0.0 <= s.trend_score <= 100.0


def test_product_research_sources_present(sample_signals):
    r = ProductResearch(keyword="test", signals=sample_signals)
    sources = r.sources_present
    assert "google_trends" in sources
    assert "amazon" in sources
    assert "mercadolibre" in sources


# ── Tests: ProductScorer ────────────────────────────────────────────────────────


def test_scorer_calculates_composite_score(sample_signals):
    scorer = ProductScorer()
    score = scorer.calculate_score(sample_signals)
    assert 0.0 <= score <= 100.0
    assert score > 50.0  # señales fuertes deberían dar score alto


def test_scorer_returns_zero_for_empty_signals():
    scorer = ProductScorer()
    score = scorer.calculate_score([])
    assert score == 0.0


def test_scorer_includes_margin_when_prices_available(sample_signals):
    scorer = ProductScorer()
    score_without_margin = scorer.calculate_score(sample_signals)
    score_with_good_margin = scorer.calculate_score(
        sample_signals,
        price_buy=Decimal("25000"),
        price_sell=Decimal("75000"),  # 66% margen
    )
    assert score_with_good_margin > score_without_margin


def test_scorer_ranks_by_score_desc():
    scorer = ProductScorer()
    researches = [
        ProductResearch(keyword="a", signals=[], composite_score=30.0),
        ProductResearch(keyword="b", signals=[], composite_score=90.0),
        ProductResearch(keyword="c", signals=[], composite_score=60.0),
    ]
    ranked = scorer.rank_products(researches, top_n=3)
    assert ranked[0].keyword == "b"
    assert ranked[1].keyword == "c"
    assert ranked[2].keyword == "a"


def test_scorer_rank_returns_top_n():
    scorer = ProductScorer()
    researches = [
        ProductResearch(keyword=f"prod_{i}", signals=[], composite_score=float(i * 10))
        for i in range(20)
    ]
    top_5 = scorer.rank_products(researches, top_n=5)
    assert len(top_5) == 5


def test_scorer_aggregate_signals_groups_by_keyword():
    scorer = ProductScorer()
    signals = [
        ProductSignal(source="google_trends", keyword="camiseta", trend_score=80.0),
        ProductSignal(source="amazon", keyword="camiseta", trend_score=60.0),
        ProductSignal(source="google_trends", keyword="zapatos", trend_score=70.0),
    ]
    researches = scorer.aggregate_signals(signals)
    keywords = {r.keyword for r in researches}
    assert "camiseta" in keywords
    assert "zapatos" in keywords
    assert len(researches) == 2


def test_scorer_enrich_with_dropi_marks_catalog_products():
    scorer = ProductScorer()
    researches = [
        ProductResearch(keyword="audífonos bluetooth", signals=[], composite_score=70.0),
        ProductResearch(keyword="zapatos deportivos", signals=[], composite_score=50.0),
    ]
    dropi_catalog = [
        {
            "dropi_id": "P001",
            "name": "Audífonos Bluetooth Pro",
            "price_buy": 25000,
            "price_sell": 75000,
            "stock": 10,
            "category": "Tecnología",
        }
    ]
    enriched = scorer.enrich_with_dropi(researches, dropi_catalog)
    audio = next(r for r in enriched if "audífonos" in r.keyword)
    assert audio.in_dropi_catalog is True
    assert audio.estimated_margin > 0


# ── Tests: MercadoLibreSource ───────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_mercadolibre_returns_trending():
    from agents.research.sources.mercadolibre import MercadoLibreSource

    respx.get("https://api.mercadolibre.com/trends/MCO").mock(
        return_value=Response(
            200,
            json=[
                {"keyword": "audífonos bluetooth", "url": "..."},
                {"keyword": "zapatos Nike", "url": "..."},
                {"keyword": "cámara gopro", "url": "..."},
            ],
        )
    )

    async with MercadoLibreSource() as ml:
        keywords = await ml.get_trending_searches()

    assert len(keywords) == 3
    assert "audífonos bluetooth" in keywords


@pytest.mark.asyncio
@respx.mock
async def test_mercadolibre_returns_signals():
    from agents.research.sources.mercadolibre import MercadoLibreSource

    respx.get("https://api.mercadolibre.com/trends/MCO").mock(
        return_value=Response(
            200,
            json=[{"keyword": "audífonos", "url": "..."}],
        )
    )

    async with MercadoLibreSource() as ml:
        signals = await ml.get_trending_signals()

    assert len(signals) == 1
    assert signals[0].source == "mercadolibre"
    assert signals[0].trend_score == 100.0  # único ítem → score máximo


# ── Tests: AmazonSource ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_amazon_source_parses_serpapi_response():
    from agents.research.sources.amazon import AmazonSource

    respx.get("https://serpapi.com/search").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {"title": "Wireless Earbuds", "rank": 1},
                    {"title": "Portable Charger", "rank": 2},
                ]
            },
        )
    )

    async with AmazonSource(api_key="test-key") as amazon:
        signals = await amazon.get_best_sellers("electronics")

    assert len(signals) == 2
    assert signals[0].trend_score == 100.0  # rank 1
    assert signals[0].source == "amazon"


@pytest.mark.asyncio
async def test_amazon_source_skips_when_no_key():
    from agents.research.sources.amazon import AmazonSource

    async with AmazonSource(api_key="") as amazon:
        signals = await amazon.get_best_sellers()

    assert signals == []


# ── Tests: ResearchAgent (integración con mocks) ────────────────────────────────


@pytest.mark.asyncio
async def test_research_agent_runs_with_one_source_failing(db_session):
    """El pipeline no debe crashear si una fuente falla."""
    from agents.research.agent import ResearchAgent

    settings = MagicMock()
    settings.anthropic_api_key = "sk-test"
    settings.claude_model = "claude-sonnet-4-6"
    settings.serpapi_key = ""
    settings.reddit_client_id = ""
    settings.reddit_client_secret = ""
    settings.reddit_user_agent = "test"

    agent = ResearchAgent(settings)

    # Google Trends lanza excepción
    agent.google.get_trending_keywords = AsyncMock(side_effect=Exception("Rate limited"))

    # MercadoLibre funciona
    async def mock_ml_signals():
        return [ProductSignal(source="mercadolibre", keyword="audífonos", trend_score=80.0)]

    agent._run_mercadolibre = mock_ml_signals
    agent._run_amazon = AsyncMock(return_value=[])
    agent.tiktok.get_trending_products = AsyncMock(return_value=[])
    agent.reddit.get_trending_signals = AsyncMock(return_value=[])

    # Mock del analista LLM
    agent.analyst.generate_shortlist_analysis = AsyncMock(
        return_value="Análisis de test"
    )

    result = await agent.run(db_session)

    assert isinstance(result, ResearchShortlist)
    assert len(result.top_products) >= 1
    assert "mercadolibre" in result.sources_used


@pytest.mark.asyncio
async def test_research_agent_persists_agent_log(db_session):
    """El Research Agent debe guardar un AgentLog al completar."""
    from sqlalchemy import select as sa_select

    from agents.research.agent import ResearchAgent

    settings = MagicMock()
    settings.anthropic_api_key = "sk-test"
    settings.claude_model = "claude-sonnet-4-6"
    settings.serpapi_key = ""
    settings.reddit_client_id = ""
    settings.reddit_client_secret = ""
    settings.reddit_user_agent = "test"

    agent = ResearchAgent(settings)

    # Todas las fuentes retornan señales mock
    mock_signals = [ProductSignal(source="mercadolibre", keyword="test_product", trend_score=75.0)]
    agent._collect_all_signals = AsyncMock(return_value=mock_signals)
    agent.analyst.generate_shortlist_analysis = AsyncMock(return_value="Test analysis")

    await agent.run(db_session)

    from app.models import AgentLog

    log_entry = await db_session.scalar(
        sa_select(AgentLog).where(AgentLog.agent == "research")
    )
    assert log_entry is not None
    assert log_entry.action == "daily_research"
    assert log_entry.status == "success"
