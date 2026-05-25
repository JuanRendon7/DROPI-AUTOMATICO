"""
Tests del Orchestrator Agent (Phase 6).
Todos los agentes externos están mockeados — sin llamadas reales a APIs ni DB.
Se usa MemorySaver como checkpointer (sin Redis).
"""
import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── T6.11.1 — Estado (state.py) ────────────────────────────────────────────────

def test_initial_state_has_all_required_keys():
    """initial_state() retorna dict con todas las claves de OrchestratorState."""
    from agents.orchestrator.state import OrchestratorState, initial_state

    state = initial_state()
    for key in OrchestratorState.__annotations__:
        assert key in state, f"Falta la clave: {key}"


def test_initial_state_generates_uuid():
    """initial_state() sin run_id genera UUID válido."""
    from agents.orchestrator.state import initial_state

    state = initial_state()
    uuid.UUID(state["run_id"])


def test_initial_state_uses_provided_run_id():
    """initial_state(run_id='custom-id') respeta el run_id proporcionado."""
    from agents.orchestrator.state import initial_state

    state = initial_state(run_id="custom-id")
    assert state["run_id"] == "custom-id"


def test_initial_state_all_statuses_pending():
    """Todos los campos *_status en initial_state() son 'pending'."""
    from agents.orchestrator.state import initial_state

    state = initial_state()
    assert state["research_status"] == "pending"
    assert state["dropi_status"] == "pending"
    assert state["campaign_status"] == "pending"
    assert state["analytics_collect_status"] == "pending"
    assert state["analytics_optimize_status"] == "pending"


def test_initial_state_trigger_source():
    """initial_state() acepta trigger_source personalizado."""
    from agents.orchestrator.state import initial_state

    state = initial_state(trigger_source="api")
    assert state["trigger_source"] == "api"


def test_initial_state_errors_empty_list():
    """initial_state() tiene errors como lista vacía y completed_at None."""
    from agents.orchestrator.state import initial_state

    state = initial_state()
    assert state["errors"] == []
    assert state["completed_at"] is None


# ── T6.11.2 — Routing (graph.py) ───────────────────────────────────────────────

def test_route_after_dropi_goes_to_campaign_with_top_product():
    """Con research_top_product_id → ruta a 'campaign'."""
    from agents.orchestrator.graph import _route_after_dropi
    from agents.orchestrator.state import initial_state

    state = initial_state()
    state["research_top_product_id"] = str(uuid.uuid4())
    assert _route_after_dropi(state) == "campaign"


def test_route_after_dropi_skips_campaign_without_product():
    """Sin research_top_product_id y dropi no exitoso → ruta a 'analytics_collect'."""
    from agents.orchestrator.graph import _route_after_dropi
    from agents.orchestrator.state import initial_state

    state = initial_state()
    state["research_top_product_id"] = None
    state["dropi_status"] = "failed"
    assert _route_after_dropi(state) == "analytics_collect"


def test_route_after_dropi_goes_to_campaign_when_dropi_success():
    """dropi_status='success' sin top_product_id → igual va a 'campaign'."""
    from agents.orchestrator.graph import _route_after_dropi
    from agents.orchestrator.state import initial_state

    state = initial_state()
    state["research_top_product_id"] = None
    state["dropi_status"] = "success"
    assert _route_after_dropi(state) == "campaign"


def test_build_orchestrator_graph_compiles():
    """build_orchestrator_graph() retorna un grafo compilado válido."""
    from agents.orchestrator.graph import build_orchestrator_graph

    graph = build_orchestrator_graph()
    assert graph is not None


def test_build_orchestrator_graph_with_memory_saver():
    """build_orchestrator_graph(MemorySaver()) compila correctamente."""
    from agents.orchestrator.graph import build_orchestrator_graph
    from langgraph.checkpoint.memory import MemorySaver

    graph = build_orchestrator_graph(checkpointer=MemorySaver())
    assert graph is not None


# ── T6.11.3 — Nodos (unit, mockeados) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_node_success():
    """research_node() retorna research_status='success' cuando ResearchAgent funciona."""
    from agents.orchestrator.nodes import research_node
    from agents.orchestrator.state import initial_state

    mock_shortlist = MagicMock()
    mock_shortlist.top_products = [MagicMock(keyword="laptop", dropi_product={"id": "abc-123"})]

    state = initial_state()
    mock_db = AsyncMock()

    with patch("agents.orchestrator.nodes.AsyncSessionLocal") as mock_session_cls, \
         patch("agents.orchestrator.nodes.get_settings"), \
         patch("agents.orchestrator.nodes.ResearchAgent") as MockAgent:
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        MockAgent.return_value.run = AsyncMock(return_value=mock_shortlist)
        result = await research_node(state)

    assert result["research_status"] == "success"
    assert result["research_top_product_id"] == "abc-123"
    assert result["research_top_product_name"] == "laptop"


@pytest.mark.asyncio
async def test_research_node_handles_exception():
    """research_node() captura excepciones y retorna research_status='failed'."""
    from agents.orchestrator.nodes import research_node
    from agents.orchestrator.state import initial_state

    state = initial_state()
    mock_db = AsyncMock()

    with patch("agents.orchestrator.nodes.AsyncSessionLocal") as mock_session_cls, \
         patch("agents.orchestrator.nodes.get_settings"), \
         patch("agents.orchestrator.nodes.ResearchAgent") as MockAgent:
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        MockAgent.return_value.run = AsyncMock(side_effect=RuntimeError("API timeout"))
        result = await research_node(state)

    assert result["research_status"] == "failed"
    assert "API timeout" in result["research_error"]
    assert len(result["errors"]) == 1


@pytest.mark.asyncio
async def test_dropi_sync_node_success():
    """dropi_sync_node() retorna dropi_status='success' con synced_count."""
    from agents.orchestrator.nodes import dropi_sync_node
    from agents.orchestrator.state import initial_state

    state = initial_state()
    mock_db = AsyncMock()

    with patch("agents.orchestrator.nodes.AsyncSessionLocal") as mock_session_cls, \
         patch("agents.orchestrator.nodes.get_settings"), \
         patch("agents.orchestrator.nodes.DropiAgent") as MockAgent:
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        MockAgent.return_value.run_full_sync = AsyncMock(return_value={"synced": 10})
        result = await dropi_sync_node(state)

    assert result["dropi_status"] == "success"
    assert result["dropi_synced_count"] == 10


@pytest.mark.asyncio
async def test_campaign_node_skips_when_no_product():
    """campaign_node() retorna campaign_status='skipped' sin productos activos."""
    from agents.orchestrator.nodes import campaign_node
    from agents.orchestrator.state import initial_state

    state = initial_state()
    state["research_top_product_id"] = None
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=None)
    mock_db.scalar = AsyncMock(return_value=None)

    with patch("agents.orchestrator.nodes.AsyncSessionLocal") as mock_session_cls, \
         patch("agents.orchestrator.nodes.get_settings"), \
         patch("agents.orchestrator.nodes.CampaignAgent"), \
         patch("agents.orchestrator.nodes.sa_select"):
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await campaign_node(state)

    assert result["campaign_status"] == "skipped"
    assert result["campaign_platforms"] == []


@pytest.mark.asyncio
async def test_optimize_node_sets_completed_at():
    """optimize_node() incluye completed_at en el resultado."""
    from agents.orchestrator.nodes import optimize_node
    from agents.orchestrator.state import initial_state

    state = initial_state()
    mock_actions = [MagicMock(executed=True), MagicMock(executed=False)]
    mock_db = AsyncMock()

    with patch("agents.orchestrator.nodes.AsyncSessionLocal") as mock_session_cls, \
         patch("agents.orchestrator.nodes.get_settings"), \
         patch("agents.orchestrator.nodes.AnalyticsAgent") as MockAgent:
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        MockAgent.return_value.run_optimization = AsyncMock(return_value=mock_actions)
        result = await optimize_node(state)

    assert result["analytics_optimize_status"] == "success"
    assert result["analytics_actions_count"] == 1
    assert result["completed_at"] is not None


# ── T6.11.4 — OrchestratorAgent (integración con MemorySaver) ──────────────────

@pytest.mark.asyncio
async def test_orchestrator_run_cycle_completes():
    """run_cycle() ejecuta el grafo completo con todos los nodos mockeados."""
    from agents.orchestrator.agent import OrchestratorAgent

    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"
    agent = OrchestratorAgent(settings)

    async def mock_research(state):
        return {"research_status": "success", "research_top_product_id": None}

    async def mock_dropi(state):
        return {"dropi_status": "success", "dropi_synced_count": 5}

    async def mock_campaign(state):
        return {"campaign_status": "skipped", "campaign_platforms": []}

    async def mock_collect(state):
        return {"analytics_collect_status": "success"}

    async def mock_optimize(state):
        return {
            "analytics_optimize_status": "success",
            "analytics_actions_count": 0,
            "completed_at": "2026-05-24T10:00:00+00:00",
        }

    with patch("agents.orchestrator.graph.research_node", mock_research), \
         patch("agents.orchestrator.graph.dropi_sync_node", mock_dropi), \
         patch("agents.orchestrator.graph.campaign_node", mock_campaign), \
         patch("agents.orchestrator.graph.collect_metrics_node", mock_collect), \
         patch("agents.orchestrator.graph.optimize_node", mock_optimize), \
         patch("agents.orchestrator.agent.AsyncRedisSaver", new=None):
        final_state = await agent.run_cycle(trigger_source="test")

    assert final_state["research_status"] == "success"
    assert final_state["dropi_status"] == "success"
    assert final_state["analytics_optimize_status"] == "success"


@pytest.mark.asyncio
async def test_orchestrator_get_run_state_returns_none_for_unknown():
    """get_run_state() retorna None para run_id desconocido."""
    from agents.orchestrator.agent import OrchestratorAgent

    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"
    agent = OrchestratorAgent(settings)

    async def mock_research(state):
        return {"research_status": "success", "research_top_product_id": None}

    async def mock_dropi(state):
        return {"dropi_status": "failed", "dropi_synced_count": 0}

    async def mock_collect(state):
        return {"analytics_collect_status": "success"}

    async def mock_optimize(state):
        return {
            "analytics_optimize_status": "success",
            "analytics_actions_count": 0,
            "completed_at": "2026-05-24T10:00:00+00:00",
        }

    with patch("agents.orchestrator.graph.research_node", mock_research), \
         patch("agents.orchestrator.graph.dropi_sync_node", mock_dropi), \
         patch("agents.orchestrator.graph.collect_metrics_node", mock_collect), \
         patch("agents.orchestrator.graph.optimize_node", mock_optimize), \
         patch("agents.orchestrator.agent.AsyncRedisSaver", new=None):
        result = await agent.get_run_state("non-existent-run-id")

    assert result is None


@pytest.mark.asyncio
async def test_orchestrator_errors_accumulated_in_state():
    """Si un nodo falla, run_cycle() incluye el error en la lista errors."""
    from agents.orchestrator.agent import OrchestratorAgent

    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"
    agent = OrchestratorAgent(settings)

    async def mock_research_fail(state):
        return {
            "research_status": "failed",
            "research_error": "timeout",
            "errors": ["research: timeout"],
        }

    async def mock_dropi(state):
        return {"dropi_status": "success", "dropi_synced_count": 0}

    async def mock_campaign(state):
        return {"campaign_status": "skipped", "campaign_platforms": []}

    async def mock_collect(state):
        return {"analytics_collect_status": "success"}

    async def mock_optimize(state):
        return {
            "analytics_optimize_status": "success",
            "analytics_actions_count": 0,
            "completed_at": "2026-05-24T10:00:00+00:00",
        }

    with patch("agents.orchestrator.graph.research_node", mock_research_fail), \
         patch("agents.orchestrator.graph.dropi_sync_node", mock_dropi), \
         patch("agents.orchestrator.graph.campaign_node", mock_campaign), \
         patch("agents.orchestrator.graph.collect_metrics_node", mock_collect), \
         patch("agents.orchestrator.graph.optimize_node", mock_optimize), \
         patch("agents.orchestrator.agent.AsyncRedisSaver", new=None):
        final_state = await agent.run_cycle()

    assert final_state["research_status"] == "failed"
    assert "research: timeout" in final_state.get("errors", [])
