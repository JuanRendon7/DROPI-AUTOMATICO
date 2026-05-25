from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.logger  # noqa: F401 — configura structlog al importar
from app.api.health import router as health_router
from app.api.dashboard import router as dashboard_router
from app.api.orchestrator import router as orchestrator_router
from app.config import get_settings


@asynccontextmanager
async def lifespan(application: FastAPI):
    from app.logger import get_logger

    log = get_logger("startup")
    settings = get_settings()
    log.info("Iniciando Dropi Sales Machine", environment=settings.environment)
    yield
    log.info("Apagando Dropi Sales Machine")


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title="Dropi Autonomous Sales Machine",
        description="Máquina autónoma de ventas dropshipping",
        version=settings.app_version,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "development" else [],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    application.include_router(health_router)
    application.include_router(orchestrator_router)
    application.include_router(dashboard_router)
    return application


app = create_app()
