#!/bin/sh
# Notificar arranque via Telegram antes de iniciar el worker
python -c "
import urllib.request, json, os
token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
if token and chat_id:
    try:
        data = json.dumps({
            'chat_id': chat_id,
            'text': '🟢 *Dropi Worker iniciado* — listo para ejecutar tareas',
            'parse_mode': 'Markdown'
        }).encode()
        req = urllib.request.Request(
            'https://api.telegram.org/bot' + token + '/sendMessage',
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
" || true

exec celery -A app.celery_app worker --loglevel=info --concurrency=2
