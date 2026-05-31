import json
import urllib.request

from celery import Celery
from celery.signals import worker_ready

celery_app = Celery("dropi_sales_machine")
celery_app.config_from_object("app.celeryconfig")


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Notifica por Telegram cuando el worker arranca correctamente."""
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.telegram_bot_token or not s.telegram_chat_id:
            return
        data = json.dumps({
            "chat_id": s.telegram_chat_id,
            "text": "🟢 *Dropi Worker iniciado* — listo para ejecutar tareas",
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
