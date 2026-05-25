# Research — Fase 6: Orquestador y Autonomía Completa

**Fecha:** 2026-05-24  
**Objetivo:** Investigar patrones de orquestación multi-agente con LangGraph para coordinar los 4 agentes existentes (Research, Dropi, Campaign, Analytics) en un flujo autónomo y resiliente.

---

## 1. LangGraph StateGraph (v0.2+, stable)

### Patrón básico para multi-agente

```python
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

class AgentState(TypedDict):
    run_id: str
    research_status: str
    # ... más campos

builder = StateGraph(AgentState)
builder.add_node("research", research_node)
builder.add_edge(START, "research")
builder.add_edge("research", END)
graph = builder.compile(checkpointer=...)
```

**Claves:**
- `StateGraph` usa `TypedDict` como esquema — todos los valores deben ser JSON-serializable
- Cada nodo recibe el estado completo y retorna un `dict` con las claves a actualizar (parcial)
- `.compile(checkpointer=...)` habilita persistencia de estado
- `.ainvoke(state, config={"configurable": {"thread_id": id}})` para ejecución async

### Edges condicionales

```python
def route_fn(state: AgentState) -> str:
    return "node_a" if condition else "node_b"

builder.add_conditional_edges("node_name", route_fn, {"node_a": "node_a", "node_b": "node_b"})
```

---

## 2. Redis Checkpointing — `langgraph-checkpoint-redis` (oficial)

El paquete `langgraph-checkpoint-redis` está disponible en PyPI y es mantenido por el equipo de LangChain. Es el mecanismo oficial para persistir el estado de grafos LangGraph en Redis.

### API actual (async)

```python
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

# Crear y configurar el checkpointer
saver = AsyncRedisSaver.from_conn_string("redis://localhost:6379")
await saver.setup()  # crea índices Redis necesarios

# Compilar el grafo con checkpointer
graph = builder.compile(checkpointer=saver)
```

### Recovery ante crashes

LangGraph replay automáticamente desde el último checkpoint cuando se usa el mismo `thread_id`. Si el proceso crashea a mitad del grafo, se puede reanudar:

```python
# La primera vez crea el thread
result = await graph.ainvoke(state, config={"configurable": {"thread_id": "run-abc"}})

# Si falla y se re-llama con el mismo thread_id, retoma desde el último checkpoint
result = await graph.ainvoke(state, config={"configurable": {"thread_id": "run-abc"}})
```

**Fallback:** Si `langgraph-checkpoint-redis` no está disponible, usar `MemorySaver` (sin persistencia entre reinicios pero funcional para tests).

---

## 3. Error Handling y Auto-Recovery

### Patrón recomendado para nodos con retry

```python
async def _with_backoff(coro_factory, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s
    raise RuntimeError("unreachable")
```

**Claves:**
- Cada nodo captura excepciones internamente y las escribe en el estado (`errors: list[str]`)
- Nunca propagar la excepción hacia LangGraph (el grafo se detendría)
- Usar `_with_backoff` dentro del nodo, no al nivel del grafo
- Circuit breaker simple: si `len(state["errors"]) > MAX_ERRORS` → ir a END

### Auto-recovery de agentes caídos

El sistema de recovery en este proyecto funciona vía:
1. **Celery retry**: cada tarea Celery tiene `max_retries=2-3` con `countdown=300s`
2. **Redis checkpoint**: si el ciclo del orquestador crashea, se puede retomar desde el checkpoint
3. **Nodos independientes**: cada nodo crea su propia sesión DB — un fallo de un nodo no corrompe el estado de otros

---

## 4. Celery + LangGraph — Integración async

### Patrón recomendado

El problema: Celery workers sincronos usan `asyncio.run()` para ejecutar código async. Esto funciona bien siempre que:
- Se use `asyncio.run()` (crea y destruye su propio event loop) ✅
- NO se use `asyncio.get_event_loop()` (no crea loop nuevo) ❌
- NO se use `nest_asyncio` (puede causar problemas con async context managers) ❌

```python
# Funciona correctamente con LangGraph + AsyncRedisSaver:
@celery_app.task(...)
def run_orchestrator_cycle(self):
    asyncio.run(_run_orchestrator_async())  # crea loop limpio cada vez

async def _run_orchestrator_async():
    agent = OrchestratorAgent(settings)
    result = await agent.run_cycle()  # AsyncRedisSaver se crea y cierra dentro del loop
```

**Nota crítica:** `AsyncRedisSaver` crea conexiones Redis async internas. Estas deben crearse y cerrarse dentro del mismo `asyncio.run()` call. Esto es garantizado porque `OrchestratorAgent._ensure_graph()` crea el checkpointer lazy (primera vez) dentro del contexto async.

---

## 5. API LangGraph actual (imports correctos)

```python
# Correcto en langgraph>=0.2:
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver  # requiere langgraph-checkpoint-redis
```

**Versión en pyproject.toml:** El proyecto tiene `langgraph>=0.1` en dependencias opcionales. Se actualizará a `>=0.2` para garantizar la API estable.

---

## 6. FastAPI + LangGraph (endpoints de control)

Para el dashboard de estado y triggering manual:

```python
# Background tasks para no bloquear la respuesta HTTP
@router.post("/trigger")
async def trigger_cycle(background_tasks: BackgroundTasks):
    run_id = str(uuid.uuid4())
    background_tasks.add_task(_run_cycle_background, run_id)
    return {"run_id": run_id, "status": "triggered"}

# Consultar estado via Redis checkpointer
@router.get("/status/{run_id}")
async def get_status(run_id: str):
    state = await orchestrator.get_run_state(run_id)
    return state
```

---

## Decisiones de diseño para Phase 6

| Decisión | Elección | Razón |
|---|---|---|
| Orquestación | LangGraph StateGraph | Nativo async, edges condicionales, checkpointing |
| State schema | TypedDict | JSON-serializable, compatible con Redis checkpointer |
| Checkpointing | AsyncRedisSaver (con fallback MemorySaver) | Persistencia real + fallback para tests |
| DB en nodos | `AsyncSessionLocal()` por nodo | Cada nodo es independiente, evita leaks |
| Retry en nodos | `_with_backoff()` interno | No propaga excepciones al grafo |
| Celery → async | `asyncio.run()` (patrón existente) | Consistente con resto del proyecto |
| API endpoints | FastAPI BackgroundTasks | No bloquea respuesta HTTP |

---

## Dependencias a agregar

```toml
# En pyproject.toml [project.optional-dependencies.agents]:
"langgraph>=0.2",                    # (actualizar de >=0.1)
"langgraph-checkpoint-redis>=0.1",   # checkpointing en Redis
```
