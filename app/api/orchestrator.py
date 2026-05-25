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
