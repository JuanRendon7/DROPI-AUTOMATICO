#!/bin/sh
set -e

echo "==> Ejecutando migraciones de base de datos..."
alembic upgrade head

echo "==> Iniciando servidor FastAPI en puerto ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
