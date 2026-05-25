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
    Si Research encontró un producto top o Dropi sync fue exitoso → crear campaña.
    Si no hay producto identificado y Dropi falló → saltar directamente a analytics.
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

    builder.add_node("research", research_node)
    builder.add_node("dropi_sync", dropi_sync_node)
    builder.add_node("campaign", campaign_node)
    builder.add_node("analytics_collect", collect_metrics_node)
    builder.add_node("analytics_optimize", optimize_node)

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
