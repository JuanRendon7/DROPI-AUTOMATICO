from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import AsyncSessionLocal

router = APIRouter()


@router.get("/health", tags=["health"])
async def health_check():
    settings = get_settings()
    return {"status": "ok", "version": settings.app_version}


@router.get("/api/v1/status", tags=["health"])
async def detailed_status():
    services: dict[str, str] = {}

    # Verificar PostgreSQL
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        services["database"] = "connected"
    except Exception:
        services["database"] = "error"

    # Verificar Redis
    try:
        import redis.asyncio as aioredis

        settings = get_settings()
        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await client.ping()
        await client.aclose()
        services["redis"] = "connected"
    except Exception:
        services["redis"] = "error"

    overall_status = "ok" if all(v == "connected" for v in services.values()) else "degraded"
    status_code = 200 if overall_status == "ok" else 503

    body = {
        "status": overall_status,
        "services": services,
        "agents": {
            "research": "idle",
            "dropi": "idle",
            "campaign": "idle",
            "analytics": "idle",
            "orchestrator": "idle",
        },
    }
    return JSONResponse(content=body, status_code=status_code)
