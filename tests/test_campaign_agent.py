"""
Tests del Campaign Agent.
Usan mocks de httpx (respx) y mocks de Anthropic — sin APIs reales.
"""
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from httpx import Response

from agents.campaign.models import AdCopy, CampaignRequest, CampaignResult, PlatformCampaignResult


# ── Fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_product():
    product = MagicMock()
    product.id = uuid.uuid4()
    product.dropi_id = "P001"
    product.name = "Audífonos Bluetooth Pro"
    product.price_buy = Decimal("25000")
    product.price_sell = Decimal("75000")
    product.stock = 10
    product.category = "Tecnología"
    product.images = ["https://example.com/image.jpg"]
    product.status = "active"
    product.updated_at = None
    return product


@pytest.fixture
def sample_request(sample_product):
    return CampaignRequest(
        product_id=str(sample_product.id),
        dropi_id=sample_product.dropi_id,
        product_name=sample_product.name,
        product_url="https://app.dropi.co/productos/P001",
        image_urls=["https://example.com/image.jpg"],
        price_sell=sample_product.price_sell,
        category=sample_product.category,
        daily_budget_usd=10.0,
        ad_copies={
            "meta": AdCopy(platform="meta", headline="¡Audífonos Pro!", body="Sonido premium", cta="Comprar"),
            "tiktok": AdCopy(platform="tiktok", headline="Audífonos 🎧", body="¡El mejor precio!", cta="Ver más"),
            "google": AdCopy(platform="google", headline="Audífonos BT", body="Calidad premium Colombia", cta="Ver oferta"),
        },
    )


# ── Tests: Models ────────────────────────────────────────────────────────────────


def test_campaign_request_budget_bounds():
    """Budget entre 1 y 50 USD es válido."""
    req = CampaignRequest(
        product_id="abc", dropi_id="P001", product_name="Test",
        product_url="https://example.com", image_urls=[],
        price_sell=Decimal("50000"), category="test", daily_budget_usd=15.0,
    )
    assert req.daily_budget_usd == 15.0


def test_campaign_result_successful_platforms():
    results = [
        PlatformCampaignResult(platform="meta", success=True, campaign_id="123"),
        PlatformCampaignResult(platform="tiktok", success=False, error="timeout"),
        PlatformCampaignResult(platform="google", success=False, skipped=True),
    ]
    cr = CampaignResult(product_id="abc", results=results)
    assert cr.successful_platforms == ["meta"]


def test_campaign_result_failed_platforms():
    """failed_platforms excluye skipped."""
    results = [
        PlatformCampaignResult(platform="meta", success=False, error="API error"),
        PlatformCampaignResult(platform="tiktok", success=False, skipped=True),
        PlatformCampaignResult(platform="google", success=True, campaign_id="456"),
    ]
    cr = CampaignResult(product_id="abc", results=results)
    assert cr.failed_platforms == ["meta"]
    assert "tiktok" not in cr.failed_platforms


def test_platform_result_external_id():
    result = PlatformCampaignResult(platform="meta", success=True, campaign_id="camp_123")
    assert result.external_id == "camp_123"


# ── Tests: ImageHandler ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_image_handler_downloads_bytes():
    from agents.campaign.image_handler import ImageHandler

    fake_bytes = b"\xff\xd8\xff" + b"\x00" * 100  # fake JPEG header
    respx.get("https://example.com/image.jpg").mock(
        return_value=Response(200, content=fake_bytes, headers={"content-type": "image/jpeg"})
    )

    async with ImageHandler() as handler:
        data, filename = await handler.download("https://example.com/image.jpg")

    assert data == fake_bytes
    assert filename == "product.jpg"


@pytest.mark.asyncio
@respx.mock
async def test_image_handler_skips_failed_urls():
    from agents.campaign.image_handler import ImageHandler

    fake_bytes = b"\xff\xd8\xff" + b"\x00" * 50
    respx.get("https://broken.example.com/bad.jpg").mock(
        return_value=Response(404)
    )
    respx.get("https://example.com/good.jpg").mock(
        return_value=Response(200, content=fake_bytes, headers={"content-type": "image/jpeg"})
    )

    async with ImageHandler() as handler:
        result = await handler.download_first_valid([
            "https://broken.example.com/bad.jpg",
            "https://example.com/good.jpg",
        ])

    assert result is not None
    data, filename = result
    assert data == fake_bytes


@pytest.mark.asyncio
@respx.mock
async def test_image_handler_returns_none_when_all_fail():
    from agents.campaign.image_handler import ImageHandler

    respx.get("https://broken1.example.com/img.jpg").mock(return_value=Response(500))
    respx.get("https://broken2.example.com/img.jpg").mock(return_value=Response(404))

    async with ImageHandler() as handler:
        result = await handler.download_first_valid([
            "https://broken1.example.com/img.jpg",
            "https://broken2.example.com/img.jpg",
        ])

    assert result is None


# ── Tests: MetaAdsClient ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_meta_client_upload_image():
    from agents.campaign.platforms.meta import MetaAdsClient

    respx.post("https://graph.facebook.com/v21.0/act_123456/adimages").mock(
        return_value=Response(200, json={
            "images": {"product.jpg": {"hash": "abc123hash", "url": "https://fbcdn.net/img.jpg"}}
        })
    )

    async with MetaAdsClient("test-token", "act_123456", "page_456") as client:
        image_hash = await client.upload_image(b"fake_image_bytes", "product.jpg")

    assert image_hash == "abc123hash"


@pytest.mark.asyncio
@respx.mock
async def test_meta_client_create_campaign_success(sample_request):
    from agents.campaign.platforms.meta import MetaAdsClient

    account = "act_123456"
    base = f"https://graph.facebook.com/v21.0/{account}"

    respx.post(f"{base}/campaigns").mock(return_value=Response(200, json={"id": "camp_111"}))
    respx.post(f"{base}/adsets").mock(return_value=Response(200, json={"id": "adset_222"}))
    respx.post(f"{base}/adcreatives").mock(return_value=Response(200, json={"id": "creative_333"}))
    respx.post(f"{base}/ads").mock(return_value=Response(200, json={"id": "ad_444"}))

    async with MetaAdsClient("test-token", account, "page_456") as client:
        result = await client.create_campaign(sample_request, ["abc123hash"])

    assert result.success is True
    assert result.platform == "meta"
    assert result.campaign_id == "camp_111"
    assert result.adset_id == "adset_222"
    assert result.ad_id == "ad_444"


@pytest.mark.asyncio
@respx.mock
async def test_meta_client_returns_failure_on_api_error(sample_request):
    from agents.campaign.platforms.meta import MetaAdsClient

    respx.post("https://graph.facebook.com/v21.0/act_123456/campaigns").mock(
        return_value=Response(400, json={"error": {"message": "Invalid token"}})
    )

    async with MetaAdsClient("bad-token", "act_123456", "page_456") as client:
        result = await client.create_campaign(sample_request, [])

    assert result.success is False
    assert result.error is not None


# ── Tests: TikTokAdsClient ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_tiktok_client_create_campaign_success(sample_request):
    from agents.campaign.platforms.tiktok import TikTokAdsClient

    base = "https://business-api.tiktok.com/open_api/v1.3"

    respx.post(f"{base}/file/image/ad/upload/").mock(
        return_value=Response(200, json={"code": 0, "data": {"image_id": "img_tiktok_001"}})
    )
    respx.post(f"{base}/campaign/create/").mock(
        return_value=Response(200, json={"code": 0, "data": {"campaign_id": "tt_camp_111"}})
    )
    respx.post(f"{base}/adgroup/create/").mock(
        return_value=Response(200, json={"code": 0, "data": {"adgroup_id": "tt_adgroup_222"}})
    )
    respx.post(f"{base}/ad/create/").mock(
        return_value=Response(200, json={"code": 0, "data": {"ad_id": "tt_ad_333"}})
    )

    async with TikTokAdsClient("test-token", "advertiser_123") as client:
        img_id = await client.upload_image(b"fake_bytes", "product.jpg")
        result = await client.create_campaign(sample_request, [img_id])

    assert img_id == "img_tiktok_001"
    assert result.success is True
    assert result.platform == "tiktok"
    assert result.campaign_id == "tt_camp_111"


@pytest.mark.asyncio
@respx.mock
async def test_tiktok_client_api_error_code_returns_failure(sample_request):
    from agents.campaign.platforms.tiktok import TikTokAdsClient

    base = "https://business-api.tiktok.com/open_api/v1.3"
    respx.post(f"{base}/campaign/create/").mock(
        return_value=Response(200, json={"code": 40100, "message": "Token expired"})
    )

    async with TikTokAdsClient("expired-token", "advertiser_123") as client:
        result = await client.create_campaign(sample_request, [])

    assert result.success is False
    assert "40100" in result.error or "Token" in result.error


# ── Tests: GoogleAdsClient ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_google_client_skips_when_no_customer_id(sample_request):
    """Si customer_id='', retorna skipped=True sin llamar a la API."""
    from agents.campaign.platforms.google_ads import GoogleAdsClient

    client = GoogleAdsClient(
        developer_token="dev-token",
        customer_id="",  # sin configurar
    )
    result = await client.create_campaign(sample_request, [])

    assert result.skipped is True
    assert result.success is False
    assert result.platform == "google"


@pytest.mark.asyncio
async def test_google_client_skips_when_missing_oauth(sample_request):
    """Sin client_id/secret/refresh_token, retorna skipped=True."""
    from agents.campaign.platforms.google_ads import GoogleAdsClient

    client = GoogleAdsClient(
        developer_token="dev-token",
        customer_id="12345",
        client_id="",       # falta OAuth
        client_secret="",
        refresh_token="",
    )
    result = await client.create_campaign(sample_request, [])

    assert result.skipped is True


# ── Tests: CampaignAgent (integración con mocks) ─────────────────────────────────


@pytest.mark.asyncio
async def test_campaign_agent_runs_with_one_platform_failing(db_session, sample_product):
    """Si Meta falla, TikTok sigue. CampaignResult tiene ambos resultados."""
    from agents.campaign.agent import CampaignAgent

    settings = MagicMock()
    settings.anthropic_api_key = "sk-test"
    settings.claude_model = "claude-sonnet-4-6"
    settings.meta_access_token = "meta-token"
    settings.meta_ad_account_id = "act_123"
    settings.meta_page_id = "page_456"
    settings.tiktok_access_token = "tiktok-token"
    settings.tiktok_advertiser_id = "adv_123"
    settings.google_ads_customer_id = ""
    settings.google_ads_developer_token = "dev-token"
    settings.google_ads_client_id = ""
    settings.google_ads_client_secret = ""
    settings.google_ads_refresh_token = ""
    settings.campaign_daily_budget_usd = 10.0
    settings.dropi_base_url = "https://app.dropi.co"

    agent = CampaignAgent(settings)

    # Mock del analyst
    agent._analyst.suggest_ad_copy = AsyncMock(
        return_value={"headline": "Test", "body": "Test body", "cta": "Comprar"}
    )

    # Mock: Meta falla, TikTok éxito
    agent._run_meta = AsyncMock(
        return_value=PlatformCampaignResult(platform="meta", success=False, error="API timeout")
    )
    agent._run_tiktok = AsyncMock(
        return_value=PlatformCampaignResult(platform="tiktok", success=True, campaign_id="tt_camp_999")
    )

    result = await agent.run(db_session, sample_product)

    assert isinstance(result, CampaignResult)
    assert "tiktok" in result.successful_platforms
    assert "meta" in result.failed_platforms
    assert len(result.results) == 2


@pytest.mark.asyncio
async def test_campaign_agent_persists_campaigns_to_db(db_session, sample_product):
    """Campañas exitosas quedan en la tabla campaigns."""
    from sqlalchemy import select as sa_select

    from agents.campaign.agent import CampaignAgent
    from app.models import Campaign

    settings = MagicMock()
    settings.anthropic_api_key = "sk-test"
    settings.claude_model = "claude-sonnet-4-6"
    settings.meta_access_token = "meta-token"
    settings.meta_ad_account_id = "act_123"
    settings.meta_page_id = "page_456"
    settings.tiktok_access_token = ""
    settings.tiktok_advertiser_id = ""
    settings.google_ads_customer_id = ""
    settings.google_ads_developer_token = ""
    settings.google_ads_client_id = ""
    settings.google_ads_client_secret = ""
    settings.google_ads_refresh_token = ""
    settings.campaign_daily_budget_usd = 10.0
    settings.dropi_base_url = "https://app.dropi.co"

    agent = CampaignAgent(settings)
    agent._analyst.suggest_ad_copy = AsyncMock(
        return_value={"headline": "Test", "body": "Test body", "cta": "Comprar"}
    )
    agent._run_meta = AsyncMock(
        return_value=PlatformCampaignResult(platform="meta", success=True, campaign_id="camp_db_test")
    )

    # Insertar producto en DB primero
    from app.models import Product
    db_product = Product(
        dropi_id=sample_product.dropi_id,
        name=sample_product.name,
        price_buy=float(sample_product.price_buy),
        price_sell=float(sample_product.price_sell),
        stock=sample_product.stock,
        category=sample_product.category,
        images=sample_product.images,
        status="active",
    )
    db_session.add(db_product)
    await db_session.commit()
    await db_session.refresh(db_product)

    await agent.run(db_session, db_product)

    campaign = await db_session.scalar(
        sa_select(Campaign).where(Campaign.platform == "meta")
    )
    assert campaign is not None
    assert campaign.external_id == "camp_db_test"
    assert campaign.status == "active"


@pytest.mark.asyncio
async def test_campaign_agent_persists_agent_log(db_session, sample_product):
    """AgentLog con agent='campaign' se persiste al completar."""
    from sqlalchemy import select as sa_select

    from agents.campaign.agent import CampaignAgent
    from app.models import AgentLog, Product

    settings = MagicMock()
    settings.anthropic_api_key = "sk-test"
    settings.claude_model = "claude-sonnet-4-6"
    settings.meta_access_token = "meta-token"
    settings.meta_ad_account_id = "act_123"
    settings.meta_page_id = "page_456"
    settings.tiktok_access_token = ""
    settings.tiktok_advertiser_id = ""
    settings.google_ads_customer_id = ""
    settings.google_ads_developer_token = ""
    settings.google_ads_client_id = ""
    settings.google_ads_client_secret = ""
    settings.google_ads_refresh_token = ""
    settings.campaign_daily_budget_usd = 10.0
    settings.dropi_base_url = "https://app.dropi.co"

    agent = CampaignAgent(settings)
    agent._analyst.suggest_ad_copy = AsyncMock(
        return_value={"headline": "Test", "body": "Body", "cta": "CTA"}
    )
    agent._run_meta = AsyncMock(
        return_value=PlatformCampaignResult(platform="meta", success=True, campaign_id="log_test_camp")
    )

    db_product = Product(
        dropi_id="P_LOG_TEST",
        name="Producto Log Test",
        price_buy=20000.0,
        price_sell=60000.0,
        stock=5,
        category="Test",
        images=[],
        status="active",
    )
    db_session.add(db_product)
    await db_session.commit()
    await db_session.refresh(db_product)

    await agent.run(db_session, db_product)

    log_entry = await db_session.scalar(
        sa_select(AgentLog).where(AgentLog.agent == "campaign")
    )
    assert log_entry is not None
    assert log_entry.action == "create_campaigns"
    assert log_entry.status in ("success", "partial")


@pytest.mark.asyncio
async def test_campaign_agent_skips_when_no_credentials(db_session, sample_product):
    """Sin credenciales, agent.run() retorna result vacío (no error)."""
    from agents.campaign.agent import CampaignAgent
    from app.models import Product

    settings = MagicMock()
    settings.anthropic_api_key = "sk-test"
    settings.claude_model = "claude-sonnet-4-6"
    settings.meta_access_token = ""          # sin credenciales
    settings.meta_ad_account_id = ""
    settings.tiktok_access_token = ""
    settings.tiktok_advertiser_id = ""
    settings.google_ads_customer_id = ""
    settings.google_ads_developer_token = ""
    settings.google_ads_client_id = ""
    settings.google_ads_client_secret = ""
    settings.google_ads_refresh_token = ""
    settings.campaign_daily_budget_usd = 10.0
    settings.dropi_base_url = "https://app.dropi.co"

    agent = CampaignAgent(settings)
    agent._analyst.suggest_ad_copy = AsyncMock(
        return_value={"headline": "Test", "body": "Body", "cta": "CTA"}
    )

    db_product = Product(
        dropi_id="P_NOCREDS",
        name="Sin Credenciales",
        price_buy=10000.0,
        price_sell=30000.0,
        stock=1,
        category="Test",
        images=[],
        status="active",
    )
    db_session.add(db_product)
    await db_session.commit()
    await db_session.refresh(db_product)

    result = await agent.run(db_session, db_product)

    assert isinstance(result, CampaignResult)
    assert result.successful_platforms == []
    assert result.results == []
