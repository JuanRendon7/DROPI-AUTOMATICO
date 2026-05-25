from app.config import Settings
from app.logger import get_logger
from agents.orchestrator.graph import build_orchestrator_graph
from agents.orchestrator.state import OrchestratorState, initial_state

log = get_logger("orchestrator")

try:
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver
except ImportError:
    AsyncRedisSaver = None  # type: ignore[assignment,misc]


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

        if AsyncRedisSaver is not None:
            try:
                self._checkpointer = AsyncRedisSaver.from_conn_string(self._settings.redis_url)
                await self._checkpointer.setup()
                log.info("Orchestrator: checkpointer Redis activo")
            except Exception as exc:
                log.warning(
                    "Orchestrator: Redis checkpointer no disponible, usando MemorySaver",
                    reason=str(exc),
                )
                from langgraph.checkpoint.memory import MemorySaver
                self._checkpointer = MemorySaver()
        else:
            log.info("Orchestrator: langgraph-checkpoint-redis no instalado, usando MemorySaver")
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
