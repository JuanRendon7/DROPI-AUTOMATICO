import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Usar SQLite en memoria para tests (sin necesidad de Docker)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key")
os.environ.setdefault("META_ACCESS_TOKEN", "test-meta-token")
os.environ.setdefault("META_AD_ACCOUNT_ID", "act_123456")
os.environ.setdefault("TIKTOK_ACCESS_TOKEN", "test-tiktok-token")
os.environ.setdefault("TIKTOK_ADVERTISER_ID", "123456789")
os.environ.setdefault("GOOGLE_ADS_DEVELOPER_TOKEN", "test-google-token")
os.environ.setdefault("DROPI_EMAIL", "test@test.com")
os.environ.setdefault("DROPI_PASSWORD", "testpassword")
os.environ.setdefault("ENVIRONMENT", "test")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    from app.database import Base

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncSession:
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db_engine) -> AsyncClient:
    from app.database import AsyncSessionLocal, get_db
    from app.main import app

    # Sobreescribir la dependencia de BD con la de tests
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
