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
    # Campaign Agent: diario 09:00 COT
    "campaign-creation-daily": {
        "task": "app.tasks.run_campaign_creation",
        "schedule": crontab(hour=9, minute=0),
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
