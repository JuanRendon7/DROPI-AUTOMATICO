import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_status_returns_services(client: AsyncClient):
    response = await client.get("/api/v1/status")
    # En tests el Redis puede no estar disponible, esperamos 200 o 503
    assert response.status_code in (200, 503)
    data = response.json()
    assert "status" in data
    assert "services" in data
    assert "agents" in data
    assert "database" in data["services"]
    assert "redis" in data["services"]


@pytest.mark.asyncio
async def test_status_has_all_agents(client: AsyncClient):
    response = await client.get("/api/v1/status")
    data = response.json()
    agents = data["agents"]
    for agent in ["research", "dropi", "campaign", "analytics", "orchestrator"]:
        assert agent in agents


@pytest.mark.asyncio
async def test_health_version_matches_config(client: AsyncClient):
    response = await client.get("/health")
    data = response.json()
    assert data["version"] == "0.1.0"
