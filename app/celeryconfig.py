from celery.schedules import crontab

from app.config import get_settings

_settings = get_settings()

broker_url = _settings.redis_url
result_backend = _settings.redis_url

task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
timezone = "America/Bogota"
enable_utc = False

# ── Tareas programadas ──────────────────────────────────────────────────────
beat_schedule = {
    # Research Agent: diario 06:00 COT
    "research-daily-06h": {
        "task": "app.tasks.run_daily_research",
        "schedule": crontab(hour=6, minute=0),
    },
    # Analytics collect: diario 08:00 COT
    "analytics-collect-daily": {
        "task": "app.tasks.run_analytics_collect",
        "schedule": crontab(hour=8, minute=0),
    },
    # Campaign Agent: diario 09:00 COT
    "campaign-creation-daily": {
        "task": "app.tasks.run_campaign_creation",
        "schedule": crontab(hour=9, minute=0),
    },
    # Analytics optimize: diario 10:00 COT
    "analytics-optimize-daily": {
        "task": "app.tasks.run_analytics_optimize",
        "schedule": crontab(hour=10, minute=0),
    },
    # Orquestador: ciclo coordinado Research→Dropi→Campaign (06:30 COT)
    # Corre 30 min después del Research standalone para no solapar
    "orchestrator-daily-0630": {
        "task": "app.tasks.run_orchestrator_cycle",
        "schedule": crontab(hour=6, minute=30),
    },
    # Dropi sync: cada 2 horas
    "dropi-sync-every-2h": {
        "task": "app.tasks.run_dropi_sync",
        "schedule": crontab(minute=0, hour="*/2"),
    },
}

# Reintentos automáticos para todas las tareas
task_acks_late = True
task_reject_on_worker_lost = True
task_max_retries = 3
