"""
Tests del Agente Dropi.
Usan mocks de httpx (respx) y mocks de Playwright (pytest-mock).
No requieren conexión real a Dropi.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from agents.dropi.api_client import DropiAPIClient
from agents.dropi.agent import DropiAgent
from agents.dropi.models import DropiOrderRaw, DropiProductRaw
from app.models import AgentLog, Order, Product


# ── Fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_product() -> DropiProductRaw:
    return DropiProductRaw(
        dropi_id="prod-001",
        name="Audífonos Bluetooth",
        price_buy=Decimal("25000"),
        price_sell=Decimal("59000"),
        stock=15,
        category="Tecnología",
        images=["https://dropi.co/img/prod1.jpg"],
        is_available=True,
    )


@pytest.fixture
def sample_order() -> DropiOrderRaw:
    return DropiOrderRaw(
        dropi_order_id="ORD-123",
        product_dropi_id="prod-001",
        status="pending",
        customer_name="Juan Pérez",
        revenue_usd=Decimal("59000"),
        created_at=datetime.now(),
    )


# ── Tests: DropiProductRaw ──────────────────────────────────────────────────────


def test_product_raw_parses_colombian_price():
    p = DropiProductRaw(
        dropi_id="x",
        name="Test",
        price_buy="$ 25.000",
        price_sell="$ 59.000",
        stock=5,
    )
    assert p.price_buy == Decimal("25000")
    assert p.price_sell == Decimal("59000")


def test_product_raw_to_db_dict(sample_product):
    d = sample_product.to_db_dict()
    assert d["dropi_id"] == "prod-001"
    assert d["status"] == "active"
    assert d["stock"] == 15


def test_product_raw_inactive_when_no_stock():
    p = DropiProductRaw(
        dropi_id="x", name="T", price_buy="10000", price_sell="20000", stock=0
    )
    assert p.to_db_dict()["status"] == "inactive"


# ── Tests: DropiOrderRaw ────────────────────────────────────────────────────────


def test_order_raw_normalizes_spanish_status():
    order = DropiOrderRaw(
        dropi_order_id="ORD-1",
        product_dropi_id="prod-1",
        status="Confirmado",
        created_at=datetime.now(),
    )
    assert order.status == "confirmed"


# ── Tests: DropiAPIClient ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_api_client_get_orders_success(sample_order):
    respx.get("https://api.dropi.co/orders").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": "ORD-123",
                    "product_id": "prod-001",
                    "status": "pending",
                    "customer_name": "Juan Pérez",
                    "total": "59000",
                    "created_at": datetime.now().isoformat(),
                }
            ],
        )
    )

    async with DropiAPIClient("test-key") as client:
        orders = await client.get_orders()

    assert len(orders) == 1
    assert orders[0].dropi_order_id == "ORD-123"
    assert orders[0].status == "pending"


@pytest.mark.asyncio
@respx.mock
async def test_api_client_handles_rate_limit():
    from agents.dropi.exceptions import RateLimitError  # noqa: F811

    respx.get("https://api.dropi.co/orders").mock(
        return_value=Response(429, headers={"Retry-After": "30"})
    )

    from app.core.exceptions import RateLimitError as BaseRateLimit

    async with DropiAPIClient("test-key") as client:
        with pytest.raises(BaseRateLimit) as exc_info:
            await client.get_orders()

    assert exc_info.value.retry_after == 30


@pytest.mark.asyncio
@respx.mock
async def test_api_client_raises_auth_error():
    from agents.dropi.exceptions import DropiAuthError

    respx.get("https://api.dropi.co/orders").mock(return_value=Response(401))

    async with DropiAPIClient("invalid-key") as client:
        with pytest.raises(DropiAuthError):
            await client.get_orders()


# ── Tests: DropiAgent.sync_catalog ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_catalog_creates_new_products(db_session, sample_product):
    settings = MagicMock()
    settings.dropi_email = "test@test.com"
    settings.dropi_password = "pass"
    settings.dropi_integration_key = "key"
    settings.dropi_api_url = "https://api.dropi.co"
    settings.playwright_headless = True
    settings.playwright_state_dir = "playwright_state"

    agent = DropiAgent(settings)

    # Mock del browser
    agent.browser = AsyncMock()
    agent.browser.scrape_full_catalog = AsyncMock(return_value=[sample_product])

    result = await agent.sync_catalog(db_session)

    assert result["new"] == 1
    assert result["updated"] == 0

    # Verificar que el producto está en DB
    from sqlalchemy import select
    product = await db_session.scalar(
        select(Product).where(Product.dropi_id == "prod-001")
    )
    assert product is not None
    assert product.name == "Audífonos Bluetooth"
    assert product.status == "active"


@pytest.mark.asyncio
async def test_sync_catalog_updates_existing_product(db_session, sample_product):
    # Insertar producto existente en DB
    existing = Product(
        dropi_id="prod-001",
        name="Viejo nombre",
        price_buy=Decimal("20000"),
        price_sell=Decimal("40000"),
        stock=5,
        status="active",
    )
    db_session.add(existing)
    await db_session.commit()

    settings = MagicMock()
    settings.dropi_email = "test@test.com"
    settings.dropi_password = "pass"
    settings.dropi_integration_key = "key"
    settings.dropi_api_url = "https://api.dropi.co"
    settings.playwright_headless = True
    settings.playwright_state_dir = "playwright_state"

    agent = DropiAgent(settings)
    agent.browser = AsyncMock()
    agent.browser.scrape_full_catalog = AsyncMock(return_value=[sample_product])

    result = await agent.sync_catalog(db_session)

    assert result["new"] == 0
    assert result["updated"] == 1

    await db_session.refresh(existing)
    assert existing.stock == 15
    assert existing.price_buy == Decimal("25000")


@pytest.mark.asyncio
async def test_sync_catalog_deactivates_missing_products(db_session):
    # Producto activo en DB que ya no aparece en el catálogo
    old_product = Product(
        dropi_id="prod-old",
        name="Producto eliminado",
        price_buy=Decimal("10000"),
        price_sell=Decimal("20000"),
        stock=3,
        status="active",
    )
    db_session.add(old_product)
    await db_session.commit()

    settings = MagicMock()
    settings.dropi_email = "test@test.com"
    settings.dropi_password = "pass"
    settings.dropi_integration_key = ""
    settings.dropi_api_url = "https://api.dropi.co"
    settings.playwright_headless = True
    settings.playwright_state_dir = "playwright_state"

    agent = DropiAgent(settings)
    agent.browser = AsyncMock()
    agent.browser.scrape_full_catalog = AsyncMock(return_value=[])  # catálogo vacío

    result = await agent.sync_catalog(db_session)

    assert result["deactivated"] == 1
    await db_session.refresh(old_product)
    assert old_product.status == "inactive"


# ── Tests: DropiAgent.sync_orders ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_orders_creates_new_orders(db_session, sample_product, sample_order):
    # Insertar el producto al que hace referencia la orden
    product = Product(
        dropi_id="prod-001",
        name="Audífonos Bluetooth",
        price_buy=Decimal("25000"),
        price_sell=Decimal("59000"),
        stock=15,
        status="active",
    )
    db_session.add(product)
    await db_session.commit()

    settings = MagicMock()
    settings.dropi_email = "test@test.com"
    settings.dropi_password = "pass"
    settings.dropi_integration_key = "key"
    settings.dropi_api_url = "https://api.dropi.co"
    settings.playwright_headless = True
    settings.playwright_state_dir = "playwright_state"

    agent = DropiAgent(settings)
    agent.api_client = AsyncMock()
    agent.api_client.__aenter__ = AsyncMock(return_value=agent.api_client)
    agent.api_client.__aexit__ = AsyncMock(return_value=None)
    agent.api_client.get_all_orders = AsyncMock(return_value=[sample_order])

    result = await agent.sync_orders(db_session)

    assert result["new"] == 1
    assert result["updated"] == 0

    from sqlalchemy import select as sa_select
    order = await db_session.scalar(
        sa_select(Order).where(Order.dropi_order_id == "ORD-123")
    )
    assert order is not None
    assert order.status == "pending"


@pytest.mark.asyncio
async def test_sync_orders_updates_status(db_session, sample_product, sample_order):
    # Insertar producto y orden existente
    product = Product(
        dropi_id="prod-001",
        name="Test",
        price_buy=Decimal("10000"),
        price_sell=Decimal("20000"),
        stock=5,
        status="active",
    )
    db_session.add(product)
    await db_session.flush()

    existing_order = Order(
        dropi_order_id="ORD-123",
        product_id=product.id,
        status="pending",
    )
    db_session.add(existing_order)
    await db_session.commit()

    # La API devuelve la orden con estado actualizado
    updated_order = DropiOrderRaw(
        dropi_order_id="ORD-123",
        product_dropi_id="prod-001",
        status="shipped",
        created_at=datetime.now(),
    )

    settings = MagicMock()
    settings.dropi_integration_key = "key"
    settings.dropi_api_url = "https://api.dropi.co"
    settings.dropi_email = "test@test.com"
    settings.dropi_password = "pass"
    settings.playwright_headless = True
    settings.playwright_state_dir = "playwright_state"

    agent = DropiAgent(settings)
    agent.api_client = AsyncMock()
    agent.api_client.__aenter__ = AsyncMock(return_value=agent.api_client)
    agent.api_client.__aexit__ = AsyncMock(return_value=None)
    agent.api_client.get_all_orders = AsyncMock(return_value=[updated_order])

    result = await agent.sync_orders(db_session)

    assert result["updated"] == 1
    await db_session.refresh(existing_order)
    assert existing_order.status == "shipped"


# ── Tests: DropiAgent.check_and_pause_out_of_stock ─────────────────────────────


@pytest.mark.asyncio
async def test_check_and_pause_sets_inactive(db_session):
    out_of_stock = Product(
        dropi_id="prod-empty",
        name="Sin Stock",
        price_buy=Decimal("10000"),
        price_sell=Decimal("20000"),
        stock=0,
        status="active",
    )
    db_session.add(out_of_stock)
    await db_session.commit()

    settings = MagicMock()
    settings.dropi_email = "test@test.com"
    settings.dropi_password = "pass"
    settings.dropi_integration_key = ""
    settings.dropi_api_url = "https://api.dropi.co"
    settings.playwright_headless = True
    settings.playwright_state_dir = "playwright_state"

    agent = DropiAgent(settings)
    agent.browser = AsyncMock()
    agent.browser.deactivate_product = AsyncMock(return_value=True)

    paused = await agent.check_and_pause_out_of_stock(db_session)

    assert "prod-empty" in paused
    await db_session.refresh(out_of_stock)
    assert out_of_stock.status == "inactive"


@pytest.mark.asyncio
async def test_check_and_pause_skips_browser_on_failure(db_session):
    out_of_stock = Product(
        dropi_id="prod-fail",
        name="Sin Stock Fail",
        price_buy=Decimal("10000"),
        price_sell=Decimal("20000"),
        stock=0,
        status="active",
    )
    db_session.add(out_of_stock)
    await db_session.commit()

    settings = MagicMock()
    settings.dropi_email = "test@test.com"
    settings.dropi_password = "pass"
    settings.dropi_integration_key = ""
    settings.dropi_api_url = "https://api.dropi.co"
    settings.playwright_headless = True
    settings.playwright_state_dir = "playwright_state"

    agent = DropiAgent(settings)
    agent.browser = AsyncMock()
    # El browser falla al desactivar → producto NO debe marcarse inactive
    agent.browser.deactivate_product = AsyncMock(return_value=False)

    paused = await agent.check_and_pause_out_of_stock(db_session)

    assert "prod-fail" not in paused
    await db_session.refresh(out_of_stock)
    assert out_of_stock.status == "active"  # No cambió porque falló
