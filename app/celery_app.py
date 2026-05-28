import asyncio

from celery import Celery
from celery.signals import worker_ready

celery_app = Celery("dropi_sales_machine")
celery_app.config_from_object("app.celeryconfig")


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Notifica por Telegram cuando el worker arranca correctamente."""
    async def _send():
        from agents.analytics.notifier import TelegramNotifier
        from app.config import get_settings
        s = get_settings()
        notifier = TelegramNotifier(s.telegram_bot_token, s.telegram_chat_id)
        await notifier.send("🟢 *Dropi Worker iniciado* — listo para ejecutar tareas")
    try:
        asyncio.run(_send())
    except Exception:
        pass
