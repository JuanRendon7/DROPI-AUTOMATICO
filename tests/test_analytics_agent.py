"""
Tests del Analytics Agent.
Usan mocks de httpx (respx) y SQLite en memoria — sin APIs reales.
"""
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import respx
from httpx import Response

from agents.analytics.models import MetricSnapshot, OptimizationAction
from agents.analytics.optimizer import OptimizerConfig, ProductionOptimizer


# ── Fixtures ────────────────────────────────────────────────────────────────────


def _make_snapshot(
    campaign_db_id: str = "camp-001",
    external_id: str = "ext-001",
    platform: str = "meta",
    spend: float = 10.0,
    revenue: float = 20.0,
    impressions: int = 1000,
    clicks: int = 50,
    target_date: date | None = None,
) -> MetricSnapshot:
    return MetricSnapshot(
        campaign_db_id=campaign_db_id,
        external_id=external_id,
        platform=platform,
        date=target_date or date.today() - timedelta(days=1),
        impressions=impressions,
        clicks=clicks,
        spend_usd=spend,
        revenue_usd=revenue,
    )


def _make_7day_metrics(roas_value: float, campaign_db_id: str = "camp-001") -> list[MetricSnapshot]:
    """Genera 7 días de métricas con un ROAS fijo."""
    metrics = []
    for i in range(7):
        spend = 10.0
        revenue = spend * roas_value
        metrics.append(_make_snapshot(
            campaign_db_id=campaign_db_id,
            spend=spend,
            revenue=revenue,
            target_date=date.today() - timedelta(days=7 - i),
        ))
    return metrics


# ── Tests: MetricSnapshot ────────────────────────────────────────────────────────


def test_metric_snapshot_roas_calculation():
    s = _make_snapshot(spend=10.0, revenue=30.0)
    assert s.roas == 3.0


def test_metric_snapshot_ctr_calculation():
    s = _make_snapshot(impressions=1000, clicks=50)
    assert s.ctr == pytest.approx(0.05, abs=1e-4)


def test_metric_snapshot_cpc_calculation():
    s = _make_snapshot(spend=50.0, clicks=25)
    assert s.cpc == pytest.approx(2.0, abs=1e-4)


def test_metric_snapshot_zero_spend_returns_zero_roas():
    s = _make_snapshot(spend=0.0, revenue=100.0)
    assert s.roas == 0.0


def test_metric_snapshot_zero_impressions_returns_zero_ctr():
    s = _make_snapshot(impressions=0, clicks=0)
    assert s.ctr == 0.0


# ── Tests: ProductionOptimizer ───────────────────────────────────────────────────


def test_optimizer_no_action_during_learning_period():
    """Con < 7 días de datos, no hay acciones."""
    optimizer = ProductionOptimizer()
    metrics = [_make_snapshot(spend=5.0, revenue=3.0) for _ in range(3)]
    actions = optimizer.evaluate_campaign("id", "ext", "meta", metrics, 10.0, 10.0)
    assert actions == []


def test_optimizer_pauses_low_roas_campaign():
    """ROAS < 1.5 durante 7 días → acción pause."""
    optimizer = ProductionOptimizer()
    metrics = _make_7day_metrics(roas_value=1.0)  # ROAS = 1.0 < 1.5
    actions = optimizer.evaluate_campaign("id", "ext-001", "meta", metrics, 10.0, 10.0)
    pause_actions = [a for a in actions if a.action == "pause"]
    assert len(pause_actions) == 1
    assert pause_actions[0].platform == "meta"


def test_optimizer_scales_high_roas_campaign():
    """ROAS > 3.0 durante 7 días → acción scale_budget."""
    optimizer = ProductionOptimizer()
    metrics = _make_7day_metrics(roas_value=4.0)  # ROAS = 4.0 > 3.0
    actions = optimizer.evaluate_campaign("id", "ext-001", "meta", metrics, 10.0, 10.0)
    scale_actions = [a for a in actions if a.action == "scale_budget"]
    assert len(scale_actions) == 1
    assert scale_actions[0].new_value == pytest.approx(12.0, abs=0.01)  # +20%


def test_optimizer_detects_spend_spike():
    """Gasto hoy > 1.5x promedio 7d → alerta spike."""
    optimizer = ProductionOptimizer()
    # 6 días normales con gasto 10, hoy gasto 20
    metrics = [_make_snapshot(spend=10.0, revenue=25.0, target_date=date.today() - timedelta(days=7 - i)) for i in range(6)]
    metrics.append(_make_snapshot(spend=20.0, revenue=25.0, target_date=date.today() - timedelta(days=1)))

    actions = optimizer.evaluate_campaign("id", "ext-001", "meta", metrics, 10.0, 10.0)
    spike_actions = [a for a in actions if a.action == "alert_spike"]
    assert len(spike_actions) == 1


def test_optimizer_flags_low_ctr():
    """CTR promedio < 0.8% → flag_low_ctr."""
    optimizer = ProductionOptimizer()
    # CTR = 3 / 1000 = 0.3% < 0.8%
    metrics = [
        _make_snapshot(impressions=1000, clicks=3, spend=10.0, revenue=25.0,
                       target_date=date.today() - timedelta(days=7 - i))
        for i in range(7)
    ]
    actions = optimizer.evaluate_campaign("id", "ext-001", "meta", metrics, 10.0, 10.0)
    ctr_actions = [a for a in actions if a.action == "flag_low_ctr"]
    assert len(ctr_actions) == 1


def test_optimizer_respects_max_budget_cap():
    """No escala más de max_budget_multiplier * initial_budget."""
    config = OptimizerConfig(max_budget_multiplier=2.0)
    optimizer = ProductionOptimizer(config)
    metrics = _make_7day_metrics(roas_value=5.0)
    # current_budget ya está en el límite
    actions = optimizer.evaluate_campaign("id", "ext", "meta", metrics, 20.0, 10.0)
    scale_actions = [a for a in actions if a.action == "scale_budget"]
    if scale_actions:
        assert scale_actions[0].new_value <= 20.0  # max = 10 * 2.0 = 20


def test_optimizer_no_scale_when_at_max():
    """Si ya está al máximo (current == initial * multiplier), no genera scale_budget."""
    config = OptimizerConfig(max_budget_multiplier=2.0)
    optimizer = ProductionOptimizer(config)
    metrics = _make_7day_metrics(roas_value=5.0)
    # current_budget YA es el máximo
    actions = optimizer.evaluate_campaign("id", "ext", "meta", metrics, 20.0, 10.0)
    scale_actions = [a for a in actions if a.action == "scale_budget"]
    # new_value no puede ser mayor que current
    for a in scale_actions:
        assert a.new_value <= 20.0


def test_optimizer_no_scale_and_no_pause_for_healthy_campaign():
    """ROAS entre 1.5 y 3.0 → no pause ni scale."""
    optimizer = ProductionOptimizer()
    metrics = _make_7day_metrics(roas_value=2.5)
    actions = optimizer.evaluate_campaign("id", "ext", "meta", metrics, 10.0, 10.0)
    assert not any(a.action in ("pause", "scale_budget") for a in actions)


# ── Tests: TelegramNotifier ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_telegram_notifier_skips_when_unconfigured():
    """Sin token, send() retorna False sin llamar a la API."""
    from agents.analytics.notifier import TelegramNotifier

    notifier = TelegramNotifier(bot_token="", chat_id="")
    result = await notifier.send("Test message")
    assert result is False


@pytest.mark.asyncio
@respx.mock
async def test_telegram_notifier_sends_message():
    """Con token y chat_id, envía el mensaje correctamente."""
    from agents.analytics.notifier import TelegramNotifier

    respx.post("https://api.telegram.org/bottest-token/sendMessage").mock(
        return_value=Response(200, json={"ok": True, "result": {"message_id": 1}})
    )

    notifier = TelegramNotifier(bot_token="test-token", chat_id="-100123456")
    result = await notifier.send("Hola desde tests")
    assert result is True


# ── Tests: MetaInsightsClient ────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_meta_insights_returns_metric_snapshot():
    from agents.analytics.platforms.meta import MetaInsightsClient

    campaign_id = "camp_111"
    target = date(2026, 5, 23)
    respx.get(f"https://graph.facebook.com/v21.0/{campaign_id}/insights").mock(
        return_value=Response(200, json={
            "data": [{
                "impressions": "10000",
                "clicks": "300",
                "spend": "50.00",
                "actions": [{"action_type": "purchase", "value": "5"}],
                "action_values": [{"action_type": "purchase", "value": "150.00"}],
            }]
        })
    )

    async with MetaInsightsClient("tok", "act_123") as client:
        snapshot = await client.get_campaign_metrics(campaign_id, "db-id-001", target)

    assert snapshot is not None
    assert snapshot.impressions == 10000
    assert snapshot.clicks == 300
    assert snapshot.spend_usd == 50.0
    assert snapshot.revenue_usd == 150.0
    assert snapshot.roas == pytest.approx(3.0, abs=0.01)
    assert snapshot.platform == "meta"


@pytest.mark.asyncio
@respx.mock
async def test_meta_insights_returns_none_when_no_data():
    from agents.analytics.platforms.meta import MetaInsightsClient

    respx.get("https://graph.facebook.com/v21.0/camp_empty/insights").mock(
        return_value=Response(200, json={"data": []})
    )

    async with MetaInsightsClient("tok", "act_123") as client:
        result = await client.get_campaign_metrics("camp_empty", "db-id", date.today())

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_meta_pause_campaign_success():
    from agents.analytics.platforms.meta import MetaInsightsClient

    campaign_id = "camp_222"
    respx.post(f"https://graph.facebook.com/v21.0/{campaign_id}").mock(
        return_value=Response(200, json={"success": True})
    )

    async with MetaInsightsClient("tok", "act_123") as client:
        result = await client.pause_campaign(campaign_id)

    assert result is True


# ── Tests: TikTokReportClient ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_tiktok_report_returns_metric_snapshot():
    from agents.analytics.platforms.tiktok import TikTokReportClient

    campaign_id = "tt_camp_001"
    target = date(2026, 5, 23)

    respx.post("https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/").mock(
        return_value=Response(200, json={
            "code": 0,
            "data": {
                "list": [{
                    "dimensions": {"campaign_id": campaign_id, "stat_time_day": "2026-05-23"},
                    "metrics": {
                        "spend": "30.00",
                        "impressions": "8000",
                        "clicks": "200",
                        "conversions": "10",
                        "total_purchase_value": "90.00",
                    }
                }]
            }
        })
    )

    async with TikTokReportClient("tok", "adv_123") as client:
        snapshot = await client.get_campaign_metrics(campaign_id, "db-id-002", target)

    assert snapshot is not None
    assert snapshot.spend_usd == 30.0
    assert snapshot.revenue_usd == 90.0
    assert snapshot.roas == pytest.approx(3.0, abs=0.01)
    assert snapshot.platform == "tiktok"


# ── Tests: GoogleAdsReportClient ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_google_report_returns_none_when_unconfigured():
    """Sin customer_id, get_campaign_metrics() retorna None."""
    from agents.analytics.platforms.google_ads import GoogleAdsReportClient

    client = GoogleAdsReportClient(developer_token="dev-tok", customer_id="")
    result = await client.get_campaign_metrics("camp_123", "db-id", date.today())
    assert result is None


@pytest.mark.asyncio
async def test_google_pause_returns_false_when_unconfigured():
    from agents.analytics.platforms.google_ads import GoogleAdsReportClient

    client = GoogleAdsReportClient(developer_token="dev-tok", customer_id="")
    result = await client.pause_campaign("camp_123")
    assert result is False


# ── Tests: AnalyticsAgent (integración con DB) ────────────────────────────────────


@pytest.mark.asyncio
async def test_analytics_collect_saves_metrics_to_db(db_session):
    """collect_metrics() guarda Metric en la DB para cada campaña activa."""
    from sqlalchemy import select as sa_select

    from agents.analytics.agent import AnalyticsAgent
    from app.models import Campaign, Metric, Product

    # Crear producto y campaña en DB
    product = Product(
        dropi_id="P_ANALYTICS_TEST",
        name="Producto Analytics",
        price_buy=20000.0,
        price_sell=60000.0,
        stock=5,
        category="Test",
        images=[],
        status="active",
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)

    campaign = Campaign(
        product_id=product.id,
        platform="meta",
        external_id="ext_meta_001",
        status="active",
        daily_budget_usd=10.0,
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)

    settings = MagicMock()
    settings.anthropic_api_key = "sk-test"
    settings.claude_model = "claude-sonnet-4-6"
    settings.meta_access_token = "meta-tok"
    settings.meta_ad_account_id = "act_123"
    settings.tiktok_access_token = ""
    settings.tiktok_advertiser_id = ""
    settings.google_ads_customer_id = ""
    settings.google_ads_developer_token = ""
    settings.google_ads_client_id = ""
    settings.google_ads_client_secret = ""
    settings.google_ads_refresh_token = ""
    settings.telegram_bot_token = ""
    settings.telegram_chat_id = ""

    agent = AnalyticsAgent(settings)

    # Mock de _fetch_platform_metrics para evitar llamadas reales
    yesterday = __import__("datetime").date.today() - __import__("datetime").timedelta(days=1)
    agent._fetch_platform_metrics = AsyncMock(
        return_value=MetricSnapshot(
            campaign_db_id=str(campaign.id),
            external_id="ext_meta_001",
            platform="meta",
            date=yesterday,
            impressions=5000,
            clicks=150,
            spend_usd=25.0,
            revenue_usd=75.0,
        )
    )

    snapshots = await agent.collect_metrics(db_session)

    assert len(snapshots) == 1
    metric = await db_session.scalar(
        sa_select(Metric).where(Metric.campaign_id == campaign.id)
    )
    assert metric is not None
    assert float(metric.spend_usd) == pytest.approx(25.0)
    assert float(metric.roas) == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_analytics_optimize_persists_agent_log(db_session):
    """run_optimization() genera AgentLog con agent='analytics'."""
    from sqlalchemy import select as sa_select

    from agents.analytics.agent import AnalyticsAgent
    from app.models import AgentLog

    settings = MagicMock()
    settings.anthropic_api_key = "sk-test"
    settings.claude_model = "claude-sonnet-4-6"
    settings.meta_access_token = ""
    settings.tiktok_access_token = ""
    settings.google_ads_customer_id = ""
    settings.google_ads_developer_token = ""
    settings.google_ads_client_id = ""
    settings.google_ads_client_secret = ""
    settings.google_ads_refresh_token = ""
    settings.telegram_bot_token = ""
    settings.telegram_chat_id = ""

    agent = AnalyticsAgent(settings)
    await agent.run_optimization(db_session)

    log_entry = await db_session.scalar(
        sa_select(AgentLog).where(AgentLog.agent == "analytics")
    )
    assert log_entry is not None
    assert log_entry.action == "run_optimization"
    assert log_entry.status == "success"


@pytest.mark.asyncio
async def test_analytics_optimize_no_action_during_learning(db_session):
    """Campaña con < 7 días de métricas no genera acciones."""
    from sqlalchemy import select as sa_select

    from agents.analytics.agent import AnalyticsAgent
    from app.models import Campaign, Metric, Product

    product = Product(
        dropi_id="P_LEARNING",
        name="En Aprendizaje",
        price_buy=10000.0,
        price_sell=30000.0,
        stock=1,
        category="Test",
        images=[],
        status="active",
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)

    campaign = Campaign(
        product_id=product.id,
        platform="meta",
        external_id="ext_learning_001",
        status="active",
        daily_budget_usd=10.0,
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)

    # Solo 3 días de métricas (periodo de aprendizaje)
    import datetime
    for i in range(3):
        db_session.add(Metric(
            campaign_id=campaign.id,
            date=datetime.date.today() - datetime.timedelta(days=3 - i),
            impressions=1000,
            clicks=10,
            spend_usd=5.0,
            revenue_usd=3.0,  # ROAS bajo, pero en aprendizaje
            roas=0.6,
            ctr=0.01,
            cpc=0.5,
        ))
    await db_session.commit()

    settings = MagicMock()
    settings.meta_access_token = "meta-tok"
    settings.meta_ad_account_id = "act_123"
    settings.tiktok_access_token = ""
    settings.google_ads_customer_id = ""
    settings.google_ads_developer_token = ""
    settings.google_ads_client_id = ""
    settings.google_ads_client_secret = ""
    settings.google_ads_refresh_token = ""
    settings.telegram_bot_token = ""
    settings.telegram_chat_id = ""
    settings.anthropic_api_key = "sk-test"
    settings.claude_model = "claude-sonnet-4-6"

    agent = AnalyticsAgent(settings)
    actions = await agent.run_optimization(db_session)

    # No debe haber acciones de pause o scale — está en aprendizaje
    assert not any(a.action in ("pause", "scale_budget") for a in actions)
