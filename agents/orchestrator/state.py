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
