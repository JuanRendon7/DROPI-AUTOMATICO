import httpx

from app.logger import get_logger

log = get_logger("analytics.notifier")

TELEGRAM_API = "https://api.telegram.org"

_ACTION_EMOJI = {
    "pause": "⏸",
    "scale_budget": "📈",
    "alert_spike": "🔴",
    "flag_low_ctr": "⚠️",
}


class TelegramNotifier:
    """Envía alertas al Telegram Bot configurado."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)

    async def send(self, message: str) -> bool:
        """Envía un mensaje. Retorna True si exitoso."""
        if not self._enabled:
            log.debug("Telegram no configurado — mensaje omitido", preview=message[:60])
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{TELEGRAM_API}/bot{self._token}/sendMessage",
                    json={
                        "chat_id": self._chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                    },
                )
                response.raise_for_status()
                log.info("Telegram: mensaje enviado")
                return True
        except Exception as exc:
            log.warning("Telegram: fallo al enviar alerta", error=str(exc))
            return False

    async def send_optimization_summary(self, actions: list, date_str: str) -> None:
        """Envía resumen de acciones de optimización del día."""
        if not actions:
            return
        lines = [f"*Optimizaciones automáticas — {date_str}*\n"]
        for action in actions:
            emoji = _ACTION_EMOJI.get(action.action, "ℹ️")
            if action.executed:
                status = "✅ ejecutado"
            elif action.error:
                status = f"❌ error: {action.error[:40]}"
            else:
                status = "📋 registrado"
            lines.append(
                f"{emoji} `{action.platform.upper()}` *{action.action}*\n"
                f"  _{action.reason[:100]}_ | {status}"
            )
        await self.send("\n".join(lines))

    async def send_alert(self, title: str, body: str) -> None:
        """Envía una alerta genérica."""
        message = f"🔔 *{title}*\n{body}"
        await self.send(message)
