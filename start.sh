#!/bin/sh
set -e

echo "==> Esperando base de datos..."
python - <<'PYEOF'
import asyncio, asyncpg, os, sys

async def wait_for_db():
    raw = os.environ.get("DATABASE_URL", "")
    url = raw.replace("postgresql+asyncpg://", "postgresql://")
    for attempt in range(1, 31):
        try:
            conn = await asyncpg.connect(url, timeout=5)
            await conn.close()
            print(f"Base de datos lista (intento {attempt}).")
            return
        except Exception as exc:
            print(f"Intento {attempt}/30: {exc}")
            await asyncio.sleep(2)
    print("ERROR: No se pudo conectar despues de 30 intentos.")
    sys.exit(1)

asyncio.run(wait_for_db())
PYEOF

echo "==> Ejecutando migraciones de base de datos..."
alembic upgrade head

echo "==> Iniciando servidor FastAPI en puerto ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
