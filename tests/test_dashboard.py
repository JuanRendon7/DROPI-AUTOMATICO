"""
Tests del Dashboard (Phase 7).
Auth: sin DB — prueba directamente los headers HTTP.
Data queries: mockeadas con AsyncMock.
"""
import base64
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _basic_auth_header(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ── T7.7.1 — Auth HTTP ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_without_auth_returns_401():
    """GET /dashboard sin Authorization → 401."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_with_wrong_credentials_returns_401():
    """GET /dashboard con credenciales incorrectas → 401."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/dashboard",
            headers=_basic_auth_header("wrong", "wrong"),
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_with_correct_credentials_returns_200():
    """GET /dashboard con credenciales correctas → 200 HTML."""
    from httpx import ASGITransport, AsyncClient

    from app.config import get_settings
    from app.main import app

    settings = get_settings()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/dashboard",
            headers=_basic_auth_header(
                settings.dashboard_username, settings.dashboard_password
            ),
        )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ── T7.7.2 — _check_auth unit ────────────────────────────────────────────────────

def test_check_auth_raises_401_on_wrong_password():
    """_check_auth() lanza HTTPException 401 con password incorrecto."""
    from fastapi import HTTPException
    from fastapi.security import HTTPBasicCredentials

    from app.api.dashboard import _check_auth

    creds = HTTPBasicCredentials(username="admin", password="wrongpass")
    with patch("app.api.dashboard.get_settings") as mock_settings:
        mock_settings.return_value.dashboard_username = "admin"
        mock_settings.return_value.dashboard_password = "secret"
        try:
            _check_auth(creds)
            assert False, "Debía lanzar HTTPException"
        except HTTPException as e:
            assert e.status_code == 401


def test_check_auth_returns_username_on_success():
    """_check_auth() retorna el username cuando las credenciales son correctas."""
    from fastapi.security import HTTPBasicCredentials

    from app.api.dashboard import _check_auth

    creds = HTTPBasicCredentials(username="admin", password="secret")
    with patch("app.api.dashboard.get_settings") as mock_settings:
        mock_settings.return_value.dashboard_username = "admin"
        mock_settings.return_value.dashboard_password = "secret"
        result = _check_auth(creds)
    assert result == "admin"


# ── T7.7.3 — Data queries con mocks ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_global_metrics_empty_db():
    """_get_global_metrics() retorna ceros con DB vacía."""
    from app.api.dashboard import _get_global_metrics

    mock_db = AsyncMock()
    mock_row = MagicMock()
    mock_row.total_spend = 0
    mock_row.total_revenue = 0
    mock_row.avg_roas = 0
    mock_result = MagicMock()
    mock_result.one.return_value = mock_row
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.scalar = AsyncMock(return_value=0)

    result = await _get_global_metrics(mock_db)

    assert result["total_spend"] == 0.0
    assert result["total_revenue"] == 0.0
    assert result["avg_roas"] == 0.0
    assert result["active_campaigns"] == 0


@pytest.mark.asyncio
async def test_get_agent_statuses_no_logs():
    """_get_agent_statuses() retorna color='red' y last_run=None sin logs."""
    from app.api.dashboard import _get_agent_statuses

    mock_db = AsyncMock()
    mock_db.scalar = AsyncMock(return_value=None)

    statuses = await _get_agent_statuses(mock_db)

    assert set(statuses.keys()) == {"research", "dropi", "campaign", "analytics", "orchestrator"}
    for agent, info in statuses.items():
        assert info["color"] == "red", f"Agente {agent} debería ser rojo sin logs"
        assert info["last_run"] is None


@pytest.mark.asyncio
async def test_get_agent_statuses_green_on_recent_success():
    """_get_agent_statuses() retorna 'green' para log reciente con status success."""
    from app.api.dashboard import _get_agent_statuses

    mock_log = MagicMock()
    mock_log.status = "success"
    mock_log.created_at = datetime.now(timezone.utc)

    mock_db = AsyncMock()
    mock_db.scalar = AsyncMock(return_value=mock_log)

    statuses = await _get_agent_statuses(mock_db)

    for info in statuses.values():
        assert info["color"] == "green"


@pytest.mark.asyncio
async def test_get_orchestrator_log_empty():
    """_get_orchestrator_log() retorna lista vacía sin logs."""
    from app.api.dashboard import _get_orchestrator_log

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await _get_orchestrator_log(mock_db)
    assert result == []


@pytest.mark.asyncio
async def test_get_chart_data_empty():
    """_get_chart_data() retorna listas vacías sin métricas."""
    from app.api.dashboard import _get_chart_data

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await _get_chart_data(mock_db)
    assert result == {"labels": [], "spend": [], "revenue": []}
