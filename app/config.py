from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Infraestructura ────────────────────────────────────────────
    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # ── Anthropic / Claude ─────────────────────────────────────────
    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-6"

    # ── Meta (Facebook / Instagram Ads) ───────────────────────────
    meta_access_token: str = ""
    meta_ad_account_id: str = ""
    meta_app_id: str = ""
    meta_app_secret: str = ""

    # ── TikTok Ads ─────────────────────────────────────────────────
    tiktok_access_token: str = ""
    tiktok_advertiser_id: str = ""
    tiktok_app_id: str = ""

    # ── Google Ads ─────────────────────────────────────────────────
    google_ads_developer_token: str = ""
    google_ads_client_id: str = ""
    google_ads_client_secret: str = ""
    google_ads_refresh_token: str = ""
    google_ads_customer_id: str = ""

    # ── Dropi ──────────────────────────────────────────────────────
    dropi_email: str
    dropi_password: str
    dropi_base_url: str = "https://app.dropi.co"
    dropi_api_url: str = "https://api.dropi.co"
    dropi_integration_key: str = ""  # Generado en Dropi → Configuración → Integraciones
    playwright_headless: bool = True
    playwright_state_dir: str = "playwright_state"

    # ── Research Agent — APIs de tendencias ───────────────────────
    serpapi_key: str = ""           # SerpAPI: Google/Amazon SERP ($75/mes)
    reddit_client_id: str = ""      # Reddit API (gratuito)
    reddit_client_secret: str = ""  # Reddit API
    reddit_user_agent: str = "dropi-sales-machine/1.0"
    research_cache_ttl_hours: int = 6  # TTL de cache en Redis para resultados

    # ── Campaign Agent ─────────────────────────────────────────────
    meta_page_id: str = ""          # Facebook Page ID para crear creativos
    campaign_daily_budget_usd: float = 10.0  # Presupuesto diario por plataforma (USD)

    # ── Notificaciones ─────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── App ────────────────────────────────────────────────────────
    environment: Literal["development", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    app_version: str = "0.1.0"

    # ── Dashboard ──────────────────────────────────────────────────
    dashboard_username: str = "admin"
    dashboard_password: str = "changeme"

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        # Railway provee postgres:// o postgresql:// — convertir al driver asyncpg
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if not v.startswith(("postgresql+asyncpg://", "sqlite")):
            raise ValueError("DATABASE_URL debe ser postgresql o sqlite")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
