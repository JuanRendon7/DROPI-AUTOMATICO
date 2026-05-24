# Plan — Fase 1: Fundación e Infraestructura

**Fase:** 1 de 7
**Estado:** `pending`
**Estimación:** 2–3 días
**Objetivo:** Montar la base técnica completa del proyecto — estructura de carpetas, configuración, modelos de datos, Docker, y CI — para que todas las fases siguientes tengan un cimiento sólido.

---

## Estructura de archivos objetivo

```
dropi-sales-machine/
├── app/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app factory
│   ├── config.py                 # Pydantic Settings
│   ├── logger.py                 # Structured JSON logger
│   ├── database.py               # SQLAlchemy engine + session
│   ├── models/
│   │   ├── __init__.py
│   │   ├── product.py            # Product model
│   │   ├── campaign.py           # Campaign model
│   │   ├── order.py              # Order model
│   │   ├── agent_log.py          # AgentLog model
│   │   └── metric.py             # Metric model
│   ├── api/
│   │   ├── __init__.py
│   │   └── health.py             # /health + /api/v1/status endpoints
│   └── core/
│       ├── __init__.py
│       └── exceptions.py         # Custom exception classes
├── agents/                       # Agentes (fases 2–6)
│   └── __init__.py
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 001_initial_schema.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   └── test_health.py
├── .github/
│   └── workflows/
│       └── ci.yml
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── alembic.ini
├── .env.example
└── .gitignore
```

---

## Tareas

### TASK-1-01: Inicializar el proyecto Python con uv
**Descripción:** Crear el `pyproject.toml` con todas las dependencias necesarias para Fase 1 y las fases futuras (incluyendo Playwright, LangGraph, etc. como dependencias opcionales).

**Archivo:** `pyproject.toml`

**Dependencias core:**
```toml
[project]
name = "dropi-sales-machine"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "asyncpg>=0.29",         # PostgreSQL async driver
    "redis>=5.0",
    "celery>=5.4",
    "anthropic>=0.28",       # Claude API
    "httpx>=0.27",
    "structlog>=24.1",       # Structured logging
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "mypy>=1.10",
    "httpx>=0.27",           # TestClient
]
```

**Criterio:** `uv sync` o `pip install -e .[dev]` sin errores.

---

### TASK-1-02: Sistema de configuración con Pydantic Settings
**Archivo:** `app/config.py`

**Requisito RF correspondiente:** RNF-03 (credenciales en variables de entorno)

```python
# Variables requeridas:
DATABASE_URL: str          # postgresql+asyncpg://user:pass@host/db
REDIS_URL: str             # redis://localhost:6379/0
ANTHROPIC_API_KEY: str     # sk-ant-...
META_ACCESS_TOKEN: str
META_AD_ACCOUNT_ID: str
TIKTOK_ACCESS_TOKEN: str
TIKTOK_ADVERTISER_ID: str
GOOGLE_ADS_DEVELOPER_TOKEN: str
DROPI_EMAIL: str
DROPI_PASSWORD: str
TELEGRAM_BOT_TOKEN: str    # Opcional para alertas
LOG_LEVEL: str = "INFO"
ENVIRONMENT: str = "development"  # development | production
```

**Criterio:** `Settings()` valida que todas las vars requeridas estén presentes y lanza `ValidationError` con mensaje claro si faltan.

---

### TASK-1-03: Logger estructurado en JSON
**Archivo:** `app/logger.py`

**Requisito RF correspondiente:** RNF-04 (logs estructurados en JSON)

Usar `structlog` configurado para:
- Salida JSON en producción, pretty-print en desarrollo
- Incluir siempre: `timestamp`, `level`, `logger`, `event`, `agent` (opcional)
- Función `get_logger(name: str)` como interfaz única

**Criterio:** `logger.info("test", agent="orchestrator", product_id=123)` produce JSON válido con todos los campos.

---

### TASK-1-04: Modelos SQLAlchemy (base de datos)
**Archivos:** `app/models/*.py`

**Requisito RF correspondiente:** Todos los agentes necesitan persistencia.

**Modelos a crear:**

```
Product:
  id: UUID (PK)
  dropi_id: str (unique)
  name: str
  price_buy: Decimal
  price_sell: Decimal
  stock: int
  status: enum (active, inactive, pending)
  category: str
  images: JSON (list de URLs)
  created_at: datetime
  updated_at: datetime

Campaign:
  id: UUID (PK)
  product_id: UUID (FK → Product)
  platform: enum (meta, tiktok, google)
  external_id: str  # ID en la plataforma de ads
  status: enum (active, paused, ended)
  daily_budget_usd: Decimal
  total_spent_usd: Decimal
  started_at: datetime
  ended_at: datetime | None

Order:
  id: UUID (PK)
  dropi_order_id: str (unique)
  product_id: UUID (FK → Product)
  campaign_id: UUID (FK → Campaign, nullable)
  status: enum (pending, confirmed, shipped, delivered, cancelled)
  revenue_usd: Decimal
  created_at: datetime

AgentLog:
  id: UUID (PK)
  agent: str  # "orchestrator", "research", "dropi", "campaign", "analytics"
  action: str
  reasoning: str | None  # JSON con el razonamiento del LLM
  status: enum (success, failure, retry)
  metadata: JSON
  created_at: datetime

Metric:
  id: UUID (PK)
  campaign_id: UUID (FK → Campaign)
  date: date
  impressions: int
  clicks: int
  conversions: int
  spend_usd: Decimal
  revenue_usd: Decimal
  roas: Decimal  # calculado: revenue / spend
  ctr: Decimal   # clicks / impressions
  cpc: Decimal   # spend / clicks
```

**Criterio:** `alembic upgrade head` crea todas las tablas sin errores.

---

### TASK-1-05: Motor de base de datos y sesión async
**Archivo:** `app/database.py`

- `AsyncEngine` con `asyncpg`
- `AsyncSessionLocal` como session factory
- `get_db()` como dependency de FastAPI
- `Base` para todos los modelos

**Criterio:** `async with get_db() as db: await db.execute(...)` funciona correctamente.

---

### TASK-1-06: Migración inicial con Alembic
**Archivos:** `alembic/`, `alembic.ini`

- Configurar Alembic para usar `DATABASE_URL` de las settings
- Generar migración `001_initial_schema.py` con todos los modelos
- La migración debe ser reversible (`downgrade` implementado)

**Criterio:** `alembic upgrade head` y `alembic downgrade -1` ejecutan sin errores.

---

### TASK-1-07: FastAPI app y endpoints de salud
**Archivos:** `app/main.py`, `app/api/health.py`

**Endpoints:**
```
GET /health
→ {"status": "ok", "version": "0.1.0"}

GET /api/v1/status
→ {
    "status": "ok",
    "services": {
        "database": "connected" | "error",
        "redis": "connected" | "error"
    },
    "agents": {
        "research": "idle",
        "dropi": "idle",
        "campaign": "idle",
        "analytics": "idle",
        "orchestrator": "idle"
    }
}
```

**Criterio:** Ambos endpoints responden 200 cuando los servicios están up, 503 si hay fallo de conectividad.

---

### TASK-1-08: Docker Compose para desarrollo local
**Archivos:** `docker-compose.yml`, `Dockerfile`

**Servicios:**
```yaml
services:
  app:        # FastAPI con hot-reload en dev
  postgres:   # postgres:16-alpine, puerto 5432
  redis:      # redis:7-alpine, puerto 6379
```

**Variables del Dockerfile:**
- Python 3.11-slim
- Usuario no-root
- `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`

**Criterio:** `docker compose up` levanta los 3 servicios y `/health` responde 200.

---

### TASK-1-09: Archivo .env.example documentado
**Archivo:** `.env.example`

Todas las variables de `config.py` documentadas con:
- Descripción de dónde obtener el valor
- Valor de ejemplo (nunca real)
- Marcadas `REQUIRED` o `OPTIONAL`

**Criterio:** Un desarrollador nuevo puede completar `.env` leyendo solo `.env.example`.

---

### TASK-1-10: .gitignore
**Archivo:** `.gitignore`

Incluir: `.env`, `__pycache__/`, `*.pyc`, `.venv/`, `*.egg-info/`, `dist/`, `.pytest_cache/`, `htmlcov/`, `.mypy_cache/`, `playwright_state/`, `*.db`

---

### TASK-1-11: Tests de la Fase 1
**Archivos:** `tests/conftest.py`, `tests/test_health.py`

- `conftest.py`: fixture de `AsyncClient` con TestDatabase en SQLite (para CI sin Docker)
- `test_health.py`:
  - `test_health_returns_200`
  - `test_status_returns_services`

**Criterio:** `pytest tests/` pasa con 100% en CI.

---

### TASK-1-12: GitHub Actions CI
**Archivo:** `.github/workflows/ci.yml`

```yaml
on: [push, pull_request]
jobs:
  lint:     ruff check . + mypy app/
  test:     pytest tests/ --cov=app --cov-report=xml
```

**Criterio:** Pipeline verde en el primer push al repo.

---

## Orden de ejecución recomendado

```
1. TASK-1-01  →  pyproject.toml + dependencias
2. TASK-1-02  →  config.py (Settings)
3. TASK-1-03  →  logger.py
4. TASK-1-05  →  database.py (engine + session)
5. TASK-1-04  →  modelos SQLAlchemy
6. TASK-1-06  →  Alembic migración inicial
7. TASK-1-07  →  FastAPI app + /health + /api/v1/status
8. TASK-1-08  →  Docker Compose + Dockerfile
9. TASK-1-09  →  .env.example
10. TASK-1-10 →  .gitignore
11. TASK-1-11 →  Tests
12. TASK-1-12 →  GitHub Actions CI
```

---

## Criterios de aceptación globales (de ROADMAP.md)

- [ ] `docker compose up` levanta el stack sin errores
- [ ] `localhost:8000/health` responde `{"status": "ok"}`
- [ ] `localhost:8000/api/v1/status` muestra conexión DB y Redis como `connected`
- [ ] `alembic upgrade head` crea las 5 tablas correctamente
- [ ] `.env.example` documenta todas las variables requeridas
- [ ] `pytest tests/` pasa al 100%
- [ ] `ruff check .` sin errores
- [ ] `mypy app/` sin errores de tipo

---

## Notas de implementación

- Usar `UUID` como tipo de PK en todos los modelos (no enteros autoincrement) — facilita distribución futura
- Todos los modelos incluyen `created_at` y `updated_at` con `server_default=func.now()` y `onupdate=func.now()`
- El campo `reasoning` en `AgentLog` es JSON para almacenar el output del LLM sin truncar
- `asyncpg` para PostgreSQL async es esencial — los agentes usarán `async/await` en toda la stack
