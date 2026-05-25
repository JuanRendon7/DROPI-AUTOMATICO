# Plan — Fase 6: Orquestador y Autonomía Completa

**Fase:** 6  
**Objetivo:** Conectar los 4 agentes existentes (Research, Dropi, Campaign, Analytics) en un flujo autónomo y resiliente usando LangGraph, con estado compartido vía Redis, recuperación ante fallos y endpoints de control via FastAPI.  
**Estimación:** 4–5 días  
**Dependencias de fase:** Fases 1–5 completadas (todos los agentes implementados y sus tareas Celery registradas)

---

## Wave 1 — Estado y Nodos del Grafo

### T6.1 — Crear `agents/orchestrator/state.py`

**Archivo:** `agents/orchestrator/state.py`

Estado compartido TypedDict para el grafo LangGraph. Todos los valores deben ser JSON-serializable (str, int, float, bool, list, dict, None) para compatibilidad con el Redis checkpointer.

```python
from __future__ import annotations
from typing import TypedDict


class OrchestratorState(TypedDict):
    # Identidad del ciclo
    run_id: str                            # UUID único por ciclo
    trigger_source: str                    # "scheduled" | "manual" | "api"
    started_at: str                        # ISO 8601 UTC

    # Research Agent
    research_status: str                   # "pending" | "success" | "failed" | "skipped"
    research_error: str | None
    research_top_product_id: str | None    # Product.id (UUID str) del top producto
    research_top_product_name: str | None  # keyword del producto top

    # Dropi Agent
    dropi_status: str                      # "pending" | "success" | "failed"
    dropi_error: str | None
    dropi_synced_count: int                # productos sincronizados

    # Campaign Agent
    campaign_status: str                   # "pending" | "success" | "failed" | "skipped"
    campaign_error: str | None
    campaign_platforms: list[str]          # plataformas donde se creó campaña

    # Analytics Agent
    analytics_collect_status: str          # "pending" | "success" | "failed"
    analytics_collect_error: str | None
    analytics_optimize_status: str         # "pending" | "success" | "failed"
    analytics_optimize_error: str | None
    analytics_actions_count: int           # acciones de optimización ejecutadas

    # Control general
    errors: list[str]                      # log acumulado de todos los errores del ciclo
    completed_at: str | None               # ISO 8601 UTC cuando se terminó el ciclo


def initial_state(trigger_source: str = "scheduled", run_id: str | None = None) -> OrchestratorState:
    """Crea un estado inicial con todos los campos requeridos."""
    import uuid
    from datetime import datetime, timezone

    return {
        "run_id": run_id or str(uuid.uuid4()),
        "trigger_source": trigger_source,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "research_status": "pending",
        "research_error": None,
        "research_top_product_id": None,
        "research_top_product_name": None,
        "dropi_status": "pending",
        "dropi_error": None,
        "dropi_synced_count": 0,
        "campaign_status": "pending",
        "campaign_error": None,
        "campaign_platforms": [],
        "analytics_collect_status": "pending",
        "analytics_collect_error": None,
        "analytics_optimize_status": "pending",
        "analytics_optimize_error": None,
        "analytics_actions_count": 0,
        "errors": [],
        "completed_at": None,
    }
```

**Criterio:** `from agents.orchestrator.state import OrchestratorState, initial_state` importa sin error. `initial_state()` retorna dict con exactamente los campos de `OrchestratorState`.

---

### T6.2 — Crear `agents/orchestrator/nodes.py`

**Archivo:** `agents/orchestrator/nodes.py`

5 funciones async (nodos del grafo). Cada nodo:
1. Crea su propia sesión DB con `AsyncSessionLocal()`
2. Instancia el agente correspondiente
3. Ejecuta con `_with_backoff()` (hasta 3 reintentos con backoff exponencial)
4. Captura excepciones — NUNCA propagar al grafo
5. Retorna `dict` con los campos del estado a actualizar

```python
import asyncio
from datetime import datetime, timezone

from agents.orchestrator.state import OrchestratorState
from app.logger import get_logger

log = get_logger("orchestrator.nodes")
_MAX_RETRIES = 3


async def _with_backoff(coro_factory, max_retries: int = _MAX_RETRIES):
    """
    Ejecuta coro_factory() con backoff exponencial (1s, 2s, 4s).
    coro_factory es un callable sin args que retorna una coroutine.
    Lanza la última excepción si todos los reintentos fallan.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                log.warning(
                    "Reintentando nodo",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    wait_seconds=wait,
                    error=str(exc),
                )
                await asyncio.sleep(wait)
    raise last_exc  # type: ignore[misc]


async def research_node(state: OrchestratorState) -> dict:
    """Ejecuta ResearchAgent.run() y extrae el top producto para el ciclo."""
    from agents.research.agent import ResearchAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    settings = get_settings()

    async def _run():
        async with AsyncSessionLocal() as db:
            agent = ResearchAgent(settings)
            shortlist = await agent.run(db)
            top = shortlist.top_products[0] if shortlist.top_products else None
            top_id = None
            if top and top.dropi_product:
                top_id = str(top.dropi_product.get("id", ""))
            return {
                "research_status": "success",
                "research_top_product_id": top_id or None,
                "research_top_product_name": top.keyword if top else None,
            }

    try:
        return await _with_backoff(_run)
    except Exception as exc:
        msg = f"research: {exc}"
        log.error("research_node falló", error=msg)
        return {
            "research_status": "failed",
            "research_error": str(exc),
            "errors": state.get("errors", []) + [msg],
        }


async def dropi_sync_node(state: OrchestratorState) -> dict:
    """Ejecuta DropiAgent.run_full_sync() para sincronizar catálogo y órdenes."""
    from agents.dropi.agent import DropiAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    settings = get_settings()

    async def _run():
        async with AsyncSessionLocal() as db:
            agent = DropiAgent(settings)
            result = await agent.run_full_sync(db)
            return {
                "dropi_status": "success",
                "dropi_synced_count": result.get("synced", 0),
            }

    try:
        return await _with_backoff(_run)
    except Exception as exc:
        msg = f"dropi: {exc}"
        log.error("dropi_sync_node falló", error=msg)
        return {
            "dropi_status": "failed",
            "dropi_error": str(exc),
            "errors": state.get("errors", []) + [msg],
        }


async def campaign_node(state: OrchestratorState) -> dict:
    """
    Ejecuta CampaignAgent.run() para el top producto de Research.
    Usa research_top_product_id si está disponible; si no, el producto activo más reciente.
    """
    from agents.campaign.agent import CampaignAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal
    from app.models import Product
    from sqlalchemy import select as sa_select

    settings = get_settings()
    top_product_id = state.get("research_top_product_id")

    async def _run():
        async with AsyncSessionLocal() as db:
            product = None
            if top_product_id:
                product = await db.get(Product, top_product_id)
            if product is None:
                product = await db.scalar(
                    sa_select(Product)
                    .where(Product.status == "active")
                    .order_by(Product.updated_at.desc())
                    .limit(1)
                )
            if product is None:
                log.warning("campaign_node: sin productos activos, omitiendo")
                return {"campaign_status": "skipped", "campaign_platforms": []}

            agent = CampaignAgent(settings)
            result = await agent.run(db, product)
            return {
                "campaign_status": "success",
                "campaign_platforms": result.successful_platforms,
            }

    try:
        return await _with_backoff(_run)
    except Exception as exc:
        msg = f"campaign: {exc}"
        log.error("campaign_node falló", error=msg)
        return {
            "campaign_status": "failed",
            "campaign_error": str(exc),
            "errors": state.get("errors", []) + [msg],
        }


async def collect_metrics_node(state: OrchestratorState) -> dict:
    """Ejecuta AnalyticsAgent.collect_metrics() para recolectar métricas del día anterior."""
    from agents.analytics.agent import AnalyticsAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    settings = get_settings()

    async def _run():
        async with AsyncSessionLocal() as db:
            agent = AnalyticsAgent(settings)
            snapshots = await agent.collect_metrics(db)
            log.info("collect_metrics_node completado", snapshots=len(snapshots))
            return {"analytics_collect_status": "success"}

    try:
        return await _with_backoff(_run)
    except Exception as exc:
        msg = f"analytics_collect: {exc}"
        log.error("collect_metrics_node falló", error=msg)
        return {
            "analytics_collect_status": "failed",
            "analytics_collect_error": str(exc),
            "errors": state.get("errors", []) + [msg],
        }


async def optimize_node(state: OrchestratorState) -> dict:
    """Ejecuta AnalyticsAgent.run_optimization() y registra las acciones tomadas."""
    from agents.analytics.agent import AnalyticsAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal

    settings = get_settings()

    async def _run():
        async with AsyncSessionLocal() as db:
            agent = AnalyticsAgent(settings)
            actions = await agent.run_optimization(db)
            executed = sum(1 for a in actions if a.executed)
            return {
                "analytics_optimize_status": "success",
                "analytics_actions_count": executed,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

    try:
        return await _with_backoff(_run)
    except Exception as exc:
        msg = f"analytics_optimize: {exc}"
        log.error("optimize_node falló", error=msg)
        return {
            "analytics_optimize_status": "failed",
            "analytics_optimize_error": str(exc),
            "errors": state.get("errors", []) + [msg],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
```

**Criterio:** Cada nodo importa y firma correctas. `_with_backoff` captura excepciones y hace máximo 3 intentos. Ningún nodo propaga excepciones hacia el grafo.

---

## Wave 2 — Grafo, OrchestratorAgent e Init

### T6.3 — Crear `agents/orchestrator/graph.py`

**Archivo:** `agents/orchestrator/graph.py`

Define y compila el `StateGraph`. Flujo:
```
START → research → dropi_sync → [campaign? ↓ o skip →] analytics_collect → analytics_optimize → END
```

La única rama condicional: si Research encontró un top producto (o Dropi sync fue exitoso), crear campaña. Sino, ir directamente a analytics.

```python
from typing import Literal

from langgraph.graph import END, START, StateGraph

from agents.orchestrator.nodes import (
    campaign_node,
    collect_metrics_node,
    dropi_sync_node,
    optimize_node,
    research_node,
)
from agents.orchestrator.state import OrchestratorState


def _route_after_dropi(state: OrchestratorState) -> Literal["campaign", "analytics_collect"]:
    """
    Si Research encontró un producto top → crear campaña.
    Si no hay producto identificado → saltar directamente a analytics.
    """
    if state.get("research_top_product_id") or state.get("dropi_status") == "success":
        return "campaign"
    return "analytics_collect"


def build_orchestrator_graph(checkpointer=None):
    """
    Construye y compila el grafo de orquestación.

    checkpointer=None → sin persistencia (para tests)
    checkpointer=AsyncRedisSaver → persistencia en Redis (producción)
    checkpointer=MemorySaver → persistencia en memoria (fallback)
    """
    builder = StateGraph(OrchestratorState)

    # Nodos
    builder.add_node("research", research_node)
    builder.add_node("dropi_sync", dropi_sync_node)
    builder.add_node("campaign", campaign_node)
    builder.add_node("analytics_collect", collect_metrics_node)
    builder.add_node("analytics_optimize", optimize_node)

    # Edges
    builder.add_edge(START, "research")
    builder.add_edge("research", "dropi_sync")
    builder.add_conditional_edges(
        "dropi_sync",
        _route_after_dropi,
        {"campaign": "campaign", "analytics_collect": "analytics_collect"},
    )
    builder.add_edge("campaign", "analytics_collect")
    builder.add_edge("analytics_collect", "analytics_optimize")
    builder.add_edge("analytics_optimize", END)

    return builder.compile(checkpointer=checkpointer)
```

**Criterio:** `build_orchestrator_graph()` retorna un grafo compilado válido. `build_orchestrator_graph(checkpointer=MemorySaver())` idem con checkpointer.

---

### T6.4 — Crear `agents/orchestrator/agent.py`

**Archivo:** `agents/orchestrator/agent.py`

Clase principal del orquestador. Gestiona el grafo LangGraph y su checkpointer.

```python
from app.config import Settings
from app.logger import get_logger
from agents.orchestrator.graph import build_orchestrator_graph
from agents.orchestrator.state import OrchestratorState, initial_state

log = get_logger("orchestrator")


class OrchestratorAgent:
    """
    Agente orquestador principal.
    Coordina Research → Dropi → Campaign → Analytics usando LangGraph StateGraph.
    Estado persistido en Redis (AsyncRedisSaver) para recuperación ante fallos.
    Fallback automático a MemorySaver si Redis checkpointer no está disponible.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._checkpointer = None
        self._graph = None

    async def _ensure_graph(self):
        """Inicializa el grafo y checkpointer de forma lazy (una sola vez por instancia)."""
        if self._graph is not None:
            return self._graph

        try:
            from langgraph.checkpoint.redis.aio import AsyncRedisSaver
            self._checkpointer = AsyncRedisSaver.from_conn_string(self._settings.redis_url)
            await self._checkpointer.setup()
            log.info("Orchestrator: checkpointer Redis activo")
        except Exception as exc:
            log.warning("Orchestrator: Redis checkpointer no disponible, usando MemorySaver", reason=str(exc))
            from langgraph.checkpoint.memory import MemorySaver
            self._checkpointer = MemorySaver()

        self._graph = build_orchestrator_graph(checkpointer=self._checkpointer)
        return self._graph

    async def run_cycle(
        self,
        trigger_source: str = "scheduled",
        run_id: str | None = None,
    ) -> OrchestratorState:
        """
        Ejecuta el ciclo completo de orquestación.
        Usa run_id como thread_id del checkpointer — permite reanudar desde el último checkpoint.

        Args:
            trigger_source: "scheduled" | "manual" | "api"
            run_id: UUID del ciclo. Si se provee, LangGraph lo usa como thread_id para recovery.

        Returns:
            Estado final tras completar todos los nodos.
        """
        graph = await self._ensure_graph()
        state = initial_state(trigger_source=trigger_source, run_id=run_id)
        config = {"configurable": {"thread_id": state["run_id"]}}

        log.info(
            "Ciclo de orquestación iniciado",
            run_id=state["run_id"],
            trigger_source=trigger_source,
        )

        final_state: OrchestratorState = await graph.ainvoke(state, config=config)

        log.info(
            "Ciclo de orquestación completado",
            run_id=final_state.get("run_id"),
            research=final_state.get("research_status"),
            dropi=final_state.get("dropi_status"),
            campaign=final_state.get("campaign_status"),
            analytics_optimize=final_state.get("analytics_optimize_status"),
            errors=len(final_state.get("errors", [])),
        )
        return final_state

    async def get_run_state(self, run_id: str) -> OrchestratorState | None:
        """
        Recupera el estado de un ciclo previo desde el checkpointer.
        Retorna None si el run_id no existe o el checkpointer no tiene estado.
        """
        graph = await self._ensure_graph()
        try:
            config = {"configurable": {"thread_id": run_id}}
            snapshot = await graph.aget_state(config)
            return snapshot.values if snapshot else None
        except Exception:
            return None
```

**Criterio:** `OrchestratorAgent(settings).run_cycle()` ejecuta el grafo y retorna `OrchestratorState`. `get_run_state("unknown-id")` retorna `None` sin error.

---

### T6.5 — Crear `agents/orchestrator/__init__.py`

**Archivo:** `agents/orchestrator/__init__.py`

```python
from agents.orchestrator.agent import OrchestratorAgent
from agents.orchestrator.state import OrchestratorState, initial_state

__all__ = ["OrchestratorAgent", "OrchestratorState", "initial_state"]
```

**Criterio:** `from agents.orchestrator import OrchestratorAgent` importa sin error.

---

## Wave 3 — API FastAPI y Tareas Celery

### T6.6 — Crear `app/api/orchestrator.py`

**Archivo:** `app/api/orchestrator.py`

3 endpoints para control del orquestador:

```python
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.logger import get_logger
from app.models import AgentLog

router = APIRouter(prefix="/api/v1/orchestrator", tags=["orchestrator"])
log = get_logger("api.orchestrator")


@router.post("/trigger")
async def trigger_cycle(background_tasks: BackgroundTasks):
    """
    Dispara un ciclo completo de orquestación en background.
    Retorna run_id para consultar el estado via GET /status/{run_id}.
    """
    run_id = str(uuid.uuid4())
    background_tasks.add_task(_run_cycle_background, run_id)
    return {"run_id": run_id, "status": "triggered"}


@router.get("/status/{run_id}")
async def get_cycle_status(run_id: str):
    """
    Consulta el estado de un ciclo específico via Redis checkpointer.
    Retorna el OrchestratorState completo o {"status": "not_found"}.
    """
    from agents.orchestrator.agent import OrchestratorAgent

    settings = get_settings()
    agent = OrchestratorAgent(settings)
    state = await agent.get_run_state(run_id)
    if state is None:
        return {"run_id": run_id, "status": "not_found"}
    return state


@router.get("/history")
async def get_history(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 20,
):
    """
    Retorna los últimos N ciclos registrados en AgentLog.
    limit: 1–100 (default 20).
    """
    limit = max(1, min(limit, 100))
    result = await db.execute(
        select(AgentLog)
        .where(AgentLog.agent == "orchestrator")
        .order_by(desc(AgentLog.created_at))
        .limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id": str(entry.id),
            "action": entry.action,
            "status": entry.status,
            "created_at": entry.created_at.isoformat(),
            "meta": entry.meta,
        }
        for entry in logs
    ]


async def _run_cycle_background(run_id: str) -> None:
    """Función ejecutada en background por BackgroundTasks de FastAPI."""
    from agents.orchestrator.agent import OrchestratorAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal
    from app.models import AgentLog

    settings = get_settings()
    agent = OrchestratorAgent(settings)

    try:
        final_state = await agent.run_cycle(trigger_source="api", run_id=run_id)
        status = "success" if not final_state.get("errors") else "partial"
    except Exception as exc:
        log.error("Background orchestrator cycle failed", run_id=run_id, error=str(exc))
        final_state = {"run_id": run_id, "errors": [str(exc)]}
        status = "failure"

    async with AsyncSessionLocal() as db:
        db.add(AgentLog(
            agent="orchestrator",
            action="run_cycle",
            status=status,
            meta={
                "run_id": run_id,
                "trigger_source": "api",
                "research_status": final_state.get("research_status"),
                "dropi_status": final_state.get("dropi_status"),
                "campaign_status": final_state.get("campaign_status"),
                "analytics_optimize_status": final_state.get("analytics_optimize_status"),
                "campaign_platforms": final_state.get("campaign_platforms", []),
                "errors": final_state.get("errors", []),
            },
        ))
        await db.commit()
```

**Criterio:** Router registrado. `POST /api/v1/orchestrator/trigger` retorna `{"run_id": "...", "status": "triggered"}`. `GET /api/v1/orchestrator/history` retorna lista.

---

### T6.7 — Actualizar `app/main.py` — registrar router del orquestador

**Archivo:** `app/main.py`  
**Cambios:** Agregar import del nuevo router y registrarlo en la aplicación.

```python
# Agregar después del import de health_router:
from app.api.orchestrator import router as orchestrator_router

# Agregar después de application.include_router(health_router):
application.include_router(orchestrator_router)
```

**Criterio:** `GET /api/v1/orchestrator/history` responde 200 con lista vacía al iniciar la app sin datos.

---

### T6.8 — Actualizar `app/tasks.py` — agregar tarea del orquestador

**Archivo:** `app/tasks.py`  
**Cambios:** 1 nueva tarea Celery + 1 función async

```python
@celery_app.task(name="app.tasks.run_orchestrator_cycle", bind=True, max_retries=1)
def run_orchestrator_cycle(self):
    """Ejecuta el ciclo completo de orquestación Research→Dropi→Campaign→Analytics.
    Programado: 06:30 COT diario (después del Research standalone de 06:00)."""
    try:
        asyncio.run(_run_orchestrator_async())
    except Exception as exc:
        log.error("run_orchestrator_cycle falló", error=str(exc))
        raise self.retry(exc=exc, countdown=600)  # reintento en 10 min


async def _run_orchestrator_async() -> None:
    from agents.orchestrator.agent import OrchestratorAgent
    from app.config import get_settings

    settings = get_settings()
    agent = OrchestratorAgent(settings)
    final_state = await agent.run_cycle(trigger_source="scheduled")
    log.info(
        "Orchestrator cycle completado via Celery",
        run_id=final_state.get("run_id"),
        research=final_state.get("research_status"),
        campaign=final_state.get("campaign_status"),
        campaign_platforms=final_state.get("campaign_platforms", []),
        errors=len(final_state.get("errors", [])),
    )
```

**Criterio:** `from app.tasks import run_orchestrator_cycle` importa sin error.

---

### T6.9 — Actualizar `app/celeryconfig.py` — schedule del orquestador

**Archivo:** `app/celeryconfig.py`  
**Cambios:** Agregar entrada al `beat_schedule` para el ciclo coordinado diario.

```python
# Orquestador: ciclo coordinado Research→Dropi→Campaign (06:30 COT)
# Corre 30 min después del Research standalone para no solapar
"orchestrator-daily-0630": {
    "task": "app.tasks.run_orchestrator_cycle",
    "schedule": crontab(hour=6, minute=30),
},
```

**Criterio:** `beat_schedule` tiene 6 entradas. La nueva entrada tiene `hour=6, minute=30`.

---

### T6.10 — Actualizar `pyproject.toml` — dependencias LangGraph

**Archivo:** `pyproject.toml`  
**Cambios:** Actualizar versión de langgraph y agregar `langgraph-checkpoint-redis`.

En `[project.optional-dependencies] agents`:
- Cambiar `"langgraph>=0.1"` → `"langgraph>=0.2"`
- Agregar `"langgraph-checkpoint-redis>=0.1"`

**Criterio:** `pip install -e ".[agents]"` instala `langgraph>=0.2` y `langgraph-checkpoint-redis`.

---

## Wave 4 — Tests

### T6.11 — Crear `tests/test_orchestrator.py`

**Archivo:** `tests/test_orchestrator.py`

Tests con `MemorySaver` como checkpointer (sin Redis real). Nodos mockeados con `AsyncMock` para aislar la lógica del grafo.

```python
"""
Tests del Orchestrator Agent (Phase 6).
Todos los agentes externos están mockeados — sin llamadas reales a APIs ni DB.
Se usa MemorySaver como checkpointer (sin Redis).
"""
import uuid
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch


# ── T6.11.1 — Estado (state.py) ───────────────────────────────────────────────

def test_initial_state_has_all_required_keys():
    """initial_state() retorna dict con todas las claves de OrchestratorState."""
    from agents.orchestrator.state import initial_state, OrchestratorState
    state = initial_state()
    for key in OrchestratorState.__annotations__:
        assert key in state, f"Falta la clave: {key}"

def test_initial_state_generates_uuid():
    """initial_state() sin run_id genera UUID válido."""
    from agents.orchestrator.state import initial_state
    state = initial_state()
    # Debe ser un UUID válido (no lanzar ValueError)
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


# ── T6.11.2 — Routing (graph.py) ──────────────────────────────────────────────

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


# ── T6.11.3 — Nodos (unit, mockeados) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_node_success():
    """research_node() retorna research_status='success' cuando ResearchAgent funciona."""
    from agents.orchestrator.nodes import research_node
    from agents.orchestrator.state import initial_state

    mock_shortlist = MagicMock()
    mock_shortlist.top_products = [MagicMock(keyword="laptop", dropi_product={"id": "abc-123"})]

    with patch("agents.orchestrator.nodes.AsyncSessionLocal"), \
         patch("agents.orchestrator.nodes.get_settings"), \
         patch("agents.research.agent.ResearchAgent") as MockAgent:
        MockAgent.return_value.run = AsyncMock(return_value=mock_shortlist)
        # Simular directamente el resultado del nodo
        state = initial_state()
        # Nodo usa AsyncSessionLocal internamente, así que mockeamos a nivel de import
        with patch("agents.orchestrator.nodes.ResearchAgent", MockAgent):
            # La sesión DB es mockeada a través del context manager
            mock_db = AsyncMock()
            MockAgent.return_value.run = AsyncMock(return_value=mock_shortlist)
            with patch("agents.orchestrator.nodes.AsyncSessionLocal") as mock_session_cls:
                mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
                mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await research_node(state)
    assert result["research_status"] == "success"

@pytest.mark.asyncio
async def test_research_node_handles_exception():
    """research_node() captura excepciones y retorna research_status='failed'."""
    from agents.orchestrator.nodes import research_node
    from agents.orchestrator.state import initial_state

    state = initial_state()
    with patch("agents.orchestrator.nodes.AsyncSessionLocal") as mock_session_cls, \
         patch("agents.orchestrator.nodes.get_settings"), \
         patch("agents.orchestrator.nodes.ResearchAgent") as MockAgent:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        MockAgent.return_value.run = AsyncMock(side_effect=RuntimeError("API timeout"))
        result = await research_node(state)

    assert result["research_status"] == "failed"
    assert "API timeout" in result["research_error"]
    assert len(result["errors"]) == 1

@pytest.mark.asyncio
async def test_campaign_node_skips_when_no_product():
    """campaign_node() retorna campaign_status='skipped' sin productos activos."""
    from agents.orchestrator.nodes import campaign_node
    from agents.orchestrator.state import initial_state

    state = initial_state()
    state["research_top_product_id"] = None

    with patch("agents.orchestrator.nodes.AsyncSessionLocal") as mock_session_cls, \
         patch("agents.orchestrator.nodes.get_settings"), \
         patch("agents.orchestrator.nodes.CampaignAgent"), \
         patch("agents.orchestrator.nodes.sa_select"):
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)
        mock_db.scalar = AsyncMock(return_value=None)
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

    with patch("agents.orchestrator.nodes.AsyncSessionLocal") as mock_session_cls, \
         patch("agents.orchestrator.nodes.get_settings"), \
         patch("agents.orchestrator.nodes.AnalyticsAgent") as MockAgent:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        MockAgent.return_value.run_optimization = AsyncMock(return_value=mock_actions)
        result = await optimize_node(state)

    assert result["analytics_optimize_status"] == "success"
    assert result["analytics_actions_count"] == 1
    assert result["completed_at"] is not None


# ── T6.11.4 — OrchestratorAgent (integración con MemorySaver) ─────────────────

@pytest.mark.asyncio
async def test_orchestrator_run_cycle_completes():
    """run_cycle() ejecuta el grafo completo con todos los nodos mockeados."""
    from agents.orchestrator.agent import OrchestratorAgent
    from agents.orchestrator.state import initial_state
    from langgraph.checkpoint.memory import MemorySaver

    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"
    agent = OrchestratorAgent(settings)

    # Mockear todos los nodos del grafo
    async def mock_research(state): return {"research_status": "success", "research_top_product_id": None}
    async def mock_dropi(state): return {"dropi_status": "success", "dropi_synced_count": 5}
    async def mock_campaign(state): return {"campaign_status": "skipped", "campaign_platforms": []}
    async def mock_collect(state): return {"analytics_collect_status": "success"}
    async def mock_optimize(state): return {"analytics_optimize_status": "success", "analytics_actions_count": 0, "completed_at": "2026-05-24T10:00:00+00:00"}

    with patch("agents.orchestrator.graph.research_node", mock_research), \
         patch("agents.orchestrator.graph.dropi_sync_node", mock_dropi), \
         patch("agents.orchestrator.graph.campaign_node", mock_campaign), \
         patch("agents.orchestrator.graph.collect_metrics_node", mock_collect), \
         patch("agents.orchestrator.graph.optimize_node", mock_optimize), \
         patch("agents.orchestrator.agent.AsyncRedisSaver", side_effect=ImportError):
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

    with patch("agents.orchestrator.agent.AsyncRedisSaver", side_effect=ImportError):
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
        return {"research_status": "failed", "research_error": "timeout", "errors": ["research: timeout"]}
    async def mock_dropi(state): return {"dropi_status": "success", "dropi_synced_count": 0}
    async def mock_campaign(state): return {"campaign_status": "skipped", "campaign_platforms": []}
    async def mock_collect(state): return {"analytics_collect_status": "success"}
    async def mock_optimize(state): return {"analytics_optimize_status": "success", "analytics_actions_count": 0, "completed_at": "2026-05-24T10:00:00+00:00"}

    with patch("agents.orchestrator.graph.research_node", mock_research_fail), \
         patch("agents.orchestrator.graph.dropi_sync_node", mock_dropi), \
         patch("agents.orchestrator.graph.campaign_node", mock_campaign), \
         patch("agents.orchestrator.graph.collect_metrics_node", mock_collect), \
         patch("agents.orchestrator.graph.optimize_node", mock_optimize), \
         patch("agents.orchestrator.agent.AsyncRedisSaver", side_effect=ImportError):
        final_state = await agent.run_cycle()

    assert final_state["research_status"] == "failed"
    assert "research: timeout" in final_state.get("errors", [])
```

**Criterio:** `pytest tests/test_orchestrator.py` — todos los tests pasan sin llamadas reales a APIs, Redis ni DB.

---

## Resumen de archivos a crear/modificar

| Acción | Archivo |
|--------|---------|
| CREAR | `agents/orchestrator/__init__.py` |
| CREAR | `agents/orchestrator/state.py` |
| CREAR | `agents/orchestrator/nodes.py` |
| CREAR | `agents/orchestrator/graph.py` |
| CREAR | `agents/orchestrator/agent.py` |
| CREAR | `app/api/orchestrator.py` |
| CREAR | `tests/test_orchestrator.py` |
| MODIFICAR | `app/main.py` (+ 1 import + 1 include_router) |
| MODIFICAR | `app/tasks.py` (+ 1 tarea + 1 función async) |
| MODIFICAR | `app/celeryconfig.py` (+ 1 entrada beat_schedule) |
| MODIFICAR | `pyproject.toml` (actualizar langgraph, agregar langgraph-checkpoint-redis) |

**Total: 7 nuevos + 4 modificados**

---

## Criterios de aceptación de la fase

- [ ] `from agents.orchestrator import OrchestratorAgent` importa sin error
- [ ] `build_orchestrator_graph()` retorna grafo compilado con 5 nodos
- [ ] `_route_after_dropi()` enruta a "campaign" si hay `research_top_product_id`
- [ ] `_route_after_dropi()` enruta a "analytics_collect" si no hay producto
- [ ] `OrchestratorAgent.run_cycle()` ejecuta todos los nodos en orden correcto
- [ ] Si un nodo falla, el error se acumula en `state["errors"]` y el grafo continúa
- [ ] `OrchestratorAgent.get_run_state("unknown-id")` retorna `None` sin error
- [ ] `POST /api/v1/orchestrator/trigger` retorna `{"run_id": "...", "status": "triggered"}`
- [ ] `GET /api/v1/orchestrator/history` retorna lista de AgentLogs
- [ ] `beat_schedule` en celeryconfig tiene 6 entradas (incluye `orchestrator-daily-0630`)
- [ ] `pytest tests/test_orchestrator.py` pasa sin llamadas reales a APIs ni Redis
- [ ] `pyproject.toml` tiene `langgraph>=0.2` y `langgraph-checkpoint-redis>=0.1`
