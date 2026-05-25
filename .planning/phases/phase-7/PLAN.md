# Plan — Fase 7: Dashboard y Monitoreo

**Fase:** 7  
**Objetivo:** Panel web de monitoreo sobre la FastAPI existente. Server-rendered con Jinja2, autenticación HTTP Basic, datos reales de DB (Products, Campaigns, Metrics, AgentLog), y gráfico de gasto vs ingresos con Chart.js.  
**Estimación:** 3–4 días  
**Dependencias de fase:** Fases 1–6 completadas — todos los modelos, agentes y orquestador implementados.

---

## Wave 1 — Config y Dependencias

### T7.1 — Actualizar `app/config.py` — credenciales del dashboard

**Archivo:** `app/config.py`  
**Cambio:** Agregar 2 campos al final de la sección `# ── App` de la clase `Settings`.

```python
# ── Dashboard ──────────────────────────────────────────────────────────────
dashboard_username: str = "admin"
dashboard_password: str = "changeme"
```

Colocar después de `app_version: str = "0.1.0"` y antes del `@field_validator`.

**Criterio:** `get_settings().dashboard_username` devuelve `"admin"` por defecto. El valor se puede sobreescribir vía env var `DASHBOARD_USERNAME`.

---

### T7.2 — Actualizar `pyproject.toml` — agregar jinja2

**Archivo:** `pyproject.toml`  
**Cambio:** En `[project.dependencies]`, agregar `"jinja2>=3.1"` junto al resto de dependencias de infraestructura.

Agregar después de `"httpx>=0.27"`:
```toml
"jinja2>=3.1",
```

**Criterio:** `pip install -e .` instala Jinja2. `from jinja2 import Environment` importa sin error.

---

## Wave 2 — Router, Auth y Queries

### T7.3 — Crear `app/api/dashboard.py`

**Archivo:** `app/api/dashboard.py`

Router FastAPI con autenticación HTTP Basic, 5 funciones de consulta a DB, y el endpoint principal que renderiza el template.

```python
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.logger import get_logger
from app.models import AgentLog, Campaign, Metric, Product

router = APIRouter(tags=["dashboard"])
security = HTTPBasic()
templates = Jinja2Templates(directory="app/templates")
log = get_logger("api.dashboard")


# ── Auth ────────────────────────────────────────────────────────────────────────

def _check_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> str:
    """Verifica credenciales HTTP Basic contra Settings. Usa compare_digest para evitar timing attacks."""
    settings = get_settings()
    ok_user = secrets.compare_digest(
        credentials.username.encode(), settings.dashboard_username.encode()
    )
    ok_pass = secrets.compare_digest(
        credentials.password.encode(), settings.dashboard_password.encode()
    )
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ── Data queries ────────────────────────────────────────────────────────────────

async def _get_global_metrics(db: AsyncSession) -> dict:
    """Totales de gasto, ingresos y ROAS promedio (últimos 30 días) + campañas activas."""
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
    result = await db.execute(
        select(
            func.coalesce(func.sum(Metric.spend_usd), 0).label("total_spend"),
            func.coalesce(func.sum(Metric.revenue_usd), 0).label("total_revenue"),
            func.coalesce(func.avg(Metric.roas), 0).label("avg_roas"),
        ).where(Metric.date >= cutoff)
    )
    row = result.one()
    active_campaigns = await db.scalar(
        select(func.count(Campaign.id)).where(Campaign.status == "active")
    )
    return {
        "total_spend": round(float(row.total_spend), 2),
        "total_revenue": round(float(row.total_revenue), 2),
        "avg_roas": round(float(row.avg_roas), 2),
        "active_campaigns": active_campaigns or 0,
    }


async def _get_products_table(db: AsyncSession) -> list[dict]:
    """Productos activos ordenados por score, con gasto y ROAS de los últimos 7 días."""
    result = await db.execute(
        select(Product)
        .where(Product.status == "active")
        .order_by(Product.score.desc().nullslast())
        .limit(20)
    )
    products = result.scalars().all()
    cutoff_7d = datetime.now(timezone.utc).date() - timedelta(days=7)

    rows = []
    for p in products:
        camp_result = await db.execute(
            select(Campaign.platform).where(
                Campaign.product_id == p.id, Campaign.status == "active"
            )
        )
        platforms = [r[0] for r in camp_result.all()]

        metric_result = await db.execute(
            select(
                func.coalesce(func.sum(Metric.spend_usd), 0).label("spend"),
                func.coalesce(func.avg(Metric.roas), 0).label("roas"),
            )
            .join(Campaign, Metric.campaign_id == Campaign.id)
            .where(Campaign.product_id == p.id, Metric.date >= cutoff_7d)
        )
        m = metric_result.one()
        rows.append({
            "name": p.name[:60],
            "status": p.status,
            "score": float(p.score) if p.score else None,
            "platforms": platforms,
            "spend_7d": round(float(m.spend), 2),
            "roas_7d": round(float(m.roas), 2),
        })
    return rows


_AGENT_INTERVALS: dict[str, timedelta] = {
    "research": timedelta(hours=26),
    "dropi": timedelta(hours=2, minutes=30),
    "campaign": timedelta(hours=26),
    "analytics": timedelta(hours=26),
    "orchestrator": timedelta(hours=26),
}


async def _get_agent_statuses(db: AsyncSession) -> dict:
    """Estado semáforo (green/yellow/red) de cada agente basado en su último AgentLog."""
    agents = ["research", "dropi", "campaign", "analytics", "orchestrator"]
    statuses = {}
    now = datetime.now(timezone.utc)

    for agent in agents:
        last = await db.scalar(
            select(AgentLog)
            .where(AgentLog.agent == agent)
            .order_by(AgentLog.created_at.desc())
            .limit(1)
        )
        if last is None:
            statuses[agent] = {"color": "red", "label": "Sin datos", "last_run": None}
            continue

        last_run = last.created_at
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        age = now - last_run
        interval = _AGENT_INTERVALS.get(agent, timedelta(hours=26))

        if last.status == "failure":
            color, label = "red", "Error"
        elif last.status in ("success", "partial") and age < interval:
            color, label = "green", "OK"
        elif last.status == "retry":
            color, label = "yellow", "Reintentando"
        else:
            color, label = "yellow", "Retrasado"

        statuses[agent] = {
            "color": color,
            "label": label,
            "last_run": last_run.strftime("%Y-%m-%d %H:%M UTC"),
        }
    return statuses


async def _get_orchestrator_log(db: AsyncSession, limit: int = 50) -> list[dict]:
    """Últimos N ciclos del orquestador registrados en AgentLog."""
    result = await db.execute(
        select(AgentLog)
        .where(AgentLog.agent == "orchestrator")
        .order_by(AgentLog.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(entry.id),
            "status": entry.status,
            "created_at": entry.created_at.strftime("%Y-%m-%d %H:%M"),
            "meta": entry.meta or {},
        }
        for entry in result.scalars().all()
    ]


async def _get_chart_data(db: AsyncSession, days: int = 7) -> dict:
    """Datos diarios de gasto e ingresos para Chart.js (últimos N días)."""
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    result = await db.execute(
        select(
            Metric.date,
            func.sum(Metric.spend_usd).label("spend"),
            func.sum(Metric.revenue_usd).label("revenue"),
        )
        .where(Metric.date >= cutoff)
        .group_by(Metric.date)
        .order_by(Metric.date)
    )
    rows = result.all()
    return {
        "labels": [str(r.date) for r in rows],
        "spend": [round(float(r.spend), 2) for r in rows],
        "revenue": [round(float(r.revenue), 2) for r in rows],
    }


# ── Endpoint ────────────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    _username: Annotated[str, Depends(_check_auth)],
):
    """Dashboard principal — renderiza HTML con todos los widgets de monitoreo."""
    global_metrics = await _get_global_metrics(db)
    products = await _get_products_table(db)
    agents = await _get_agent_statuses(db)
    orc_log = await _get_orchestrator_log(db)
    chart_data = await _get_chart_data(db, days=7)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "global_metrics": global_metrics,
            "products": products,
            "agents": agents,
            "orc_log": orc_log,
            "chart_data": chart_data,
        },
    )
```

**Criterio:** `GET /dashboard` sin auth → 401. Con auth correcta → 200 HTML. Con auth incorrecta → 401.

---

### T7.4 — Actualizar `app/api/health.py` — estado real de agentes

**Archivo:** `app/api/health.py`  
**Cambio:** En `detailed_status()`, reemplazar el dict hardcodeado de agentes con una consulta real a `AgentLog`.

Reemplazar:
```python
"agents": {
    "research": "idle",
    "dropi": "idle",
    "campaign": "idle",
    "analytics": "idle",
    "orchestrator": "idle",
},
```

Por:
```python
# Importar al inicio del archivo:
from sqlalchemy import select as sa_select
from app.models import AgentLog as AgentLogModel

# Dentro de detailed_status(), antes del return:
agent_statuses: dict[str, str] = {}
agent_names = ["research", "dropi", "campaign", "analytics", "orchestrator"]
try:
    async with AsyncSessionLocal() as db:
        for agent_name in agent_names:
            last = await db.scalar(
                sa_select(AgentLogModel)
                .where(AgentLogModel.agent == agent_name)
                .order_by(AgentLogModel.created_at.desc())
                .limit(1)
            )
            agent_statuses[agent_name] = last.status if last else "never_run"
except Exception:
    agent_statuses = {name: "unknown" for name in agent_names}
```

Y en el `body` del return:
```python
"agents": agent_statuses,
```

**Criterio:** `GET /api/v1/status` retorna `agents` con estado real ("success", "failure", "never_run") en lugar de "idle".

---

### T7.5 — Actualizar `app/main.py` — incluir router del dashboard

**Archivo:** `app/main.py`  
**Cambio:** Importar y registrar el router del dashboard.

```python
# Agregar después del import de orchestrator_router:
from app.api.dashboard import router as dashboard_router

# Agregar después de application.include_router(orchestrator_router):
application.include_router(dashboard_router)
```

**Criterio:** `GET /dashboard` responde 401 (solicita credenciales). La app inicia sin ImportError.

---

## Wave 3 — Template HTML

### T7.6 — Crear `app/templates/dashboard.html`

**Archivo:** `app/templates/dashboard.html`

Template Jinja2 completo con Tailwind CDN y Chart.js CDN. Variables recibidas del endpoint:
- `global_metrics`: dict con `total_spend`, `total_revenue`, `avg_roas`, `active_campaigns`
- `products`: list[dict] con `name`, `score`, `platforms`, `spend_7d`, `roas_7d`
- `agents`: dict de `{agent_name: {color, label, last_run}}`
- `orc_log`: list[dict] con `status`, `created_at`, `meta`
- `chart_data`: dict con `labels`, `spend`, `revenue`

```html
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60">
    <title>Dropi Sales Machine — Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen">

<header class="bg-gray-800 border-b border-gray-700 px-6 py-4 flex items-center justify-between">
    <div>
        <h1 class="text-xl font-bold text-white">Dropi Autonomous Sales Machine</h1>
        <p class="text-xs text-gray-400 mt-0.5">Auto-refresh cada 60s</p>
    </div>
    <span class="text-xs text-gray-500">{{ now if now else "" }}</span>
</header>

<main class="max-w-7xl mx-auto px-6 py-6 space-y-6">

    <!-- Global Metrics -->
    <section>
        <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Metricas globales — ultimos 30 dias</h2>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div class="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <p class="text-gray-400 text-xs mb-1">Gasto total</p>
                <p class="text-2xl font-bold text-red-400">${{ "%.2f"|format(global_metrics.total_spend) }}</p>
            </div>
            <div class="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <p class="text-gray-400 text-xs mb-1">Ingresos</p>
                <p class="text-2xl font-bold text-green-400">${{ "%.2f"|format(global_metrics.total_revenue) }}</p>
            </div>
            <div class="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <p class="text-gray-400 text-xs mb-1">ROAS promedio</p>
                <p class="text-2xl font-bold text-blue-400">{{ "%.2f"|format(global_metrics.avg_roas) }}x</p>
            </div>
            <div class="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <p class="text-gray-400 text-xs mb-1">Campanas activas</p>
                <p class="text-2xl font-bold text-yellow-400">{{ global_metrics.active_campaigns }}</p>
            </div>
        </div>
    </section>

    <!-- Agent Status -->
    <section>
        <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Estado de agentes</h2>
        <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
            {% for name, info in agents.items() %}
            <div class="bg-gray-800 rounded-lg p-3 border border-gray-700 flex items-start gap-3">
                <span class="mt-0.5 w-2.5 h-2.5 rounded-full flex-shrink-0
                    {% if info.color == 'green' %}bg-green-400{% elif info.color == 'yellow' %}bg-yellow-400 animate-pulse{% else %}bg-red-400 animate-pulse{% endif %}">
                </span>
                <div class="min-w-0">
                    <p class="text-sm font-medium text-white capitalize">{{ name }}</p>
                    <p class="text-xs font-medium
                        {% if info.color == 'green' %}text-green-400{% elif info.color == 'yellow' %}text-yellow-400{% else %}text-red-400{% endif %}">
                        {{ info.label }}
                    </p>
                    {% if info.last_run %}
                    <p class="text-xs text-gray-500 truncate mt-0.5">{{ info.last_run }}</p>
                    {% else %}
                    <p class="text-xs text-gray-600 mt-0.5">Sin ejecucion</p>
                    {% endif %}
                </div>
            </div>
            {% endfor %}
        </div>
    </section>

    <!-- Chart + Products -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">

        <!-- Revenue vs Spend Chart -->
        <section class="bg-gray-800 rounded-lg p-4 border border-gray-700">
            <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">Gasto vs Ingresos — 7 dias</h2>
            {% if chart_data.labels %}
            <canvas id="revenueChart"></canvas>
            {% else %}
            <div class="flex items-center justify-center h-32 text-gray-600 text-sm">
                Sin datos de metricas aun.
            </div>
            {% endif %}
        </section>

        <!-- Products Table -->
        <section class="bg-gray-800 rounded-lg p-4 border border-gray-700">
            <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">Productos activos (top 20)</h2>
            {% if products %}
            <div class="overflow-x-auto">
                <table class="w-full text-sm">
                    <thead>
                        <tr class="text-gray-500 text-xs border-b border-gray-700">
                            <th class="text-left pb-2 font-medium">Producto</th>
                            <th class="text-right pb-2 font-medium">Score</th>
                            <th class="text-right pb-2 font-medium">ROAS 7d</th>
                            <th class="text-right pb-2 font-medium">Gasto 7d</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-700/50">
                        {% for p in products %}
                        <tr class="hover:bg-gray-700/30">
                            <td class="py-2 pr-2">
                                <div class="text-white text-xs truncate max-w-[180px]" title="{{ p.name }}">{{ p.name }}</div>
                                {% if p.platforms %}
                                <div class="text-gray-500 text-xs">{{ p.platforms | join(", ") }}</div>
                                {% else %}
                                <div class="text-gray-600 text-xs">sin campanas</div>
                                {% endif %}
                            </td>
                            <td class="py-2 text-right text-xs">
                                {% if p.score is not none %}
                                <span class="text-yellow-400 font-medium">{{ "%.0f"|format(p.score) }}</span>
                                {% else %}
                                <span class="text-gray-600">—</span>
                                {% endif %}
                            </td>
                            <td class="py-2 text-right text-xs font-medium
                                {% if p.roas_7d >= 3.0 %}text-green-400
                                {% elif p.roas_7d >= 1.5 %}text-yellow-400
                                {% elif p.roas_7d > 0 %}text-red-400
                                {% else %}text-gray-600{% endif %}">
                                {% if p.roas_7d > 0 %}{{ "%.2f"|format(p.roas_7d) }}x{% else %}—{% endif %}
                            </td>
                            <td class="py-2 text-right text-xs text-gray-300">
                                {% if p.spend_7d > 0 %}${{ "%.2f"|format(p.spend_7d) }}{% else %}—{% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% else %}
            <p class="text-gray-600 text-sm">No hay productos activos.</p>
            {% endif %}
        </section>
    </div>

    <!-- Orchestrator Log -->
    <section class="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">Log del orquestador — ultimos 50 ciclos</h2>
        {% if orc_log %}
        <div class="overflow-x-auto">
            <table class="w-full text-xs">
                <thead>
                    <tr class="text-gray-500 border-b border-gray-700">
                        <th class="text-left pb-2 font-medium">Fecha</th>
                        <th class="text-left pb-2 font-medium">Estado</th>
                        <th class="text-left pb-2 font-medium">Research</th>
                        <th class="text-left pb-2 font-medium">Dropi</th>
                        <th class="text-left pb-2 font-medium">Campaign</th>
                        <th class="text-left pb-2 font-medium">Analytics</th>
                        <th class="text-right pb-2 font-medium">Errores</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-700/50">
                    {% for entry in orc_log %}
                    <tr class="hover:bg-gray-700/30">
                        <td class="py-1.5 text-gray-400 whitespace-nowrap pr-3">{{ entry.created_at }}</td>
                        <td class="py-1.5 pr-3">
                            <span class="inline-flex px-1.5 py-0.5 rounded text-xs font-medium
                                {% if entry.status == 'success' %}bg-green-900/60 text-green-300
                                {% elif entry.status == 'partial' %}bg-yellow-900/60 text-yellow-300
                                {% else %}bg-red-900/60 text-red-300{% endif %}">
                                {{ entry.status }}
                            </span>
                        </td>
                        <td class="py-1.5 pr-3 text-gray-400">{{ entry.meta.get('research_status', '—') }}</td>
                        <td class="py-1.5 pr-3 text-gray-400">{{ entry.meta.get('dropi_status', '—') }}</td>
                        <td class="py-1.5 pr-3 text-gray-400">{{ entry.meta.get('campaign_status', '—') }}</td>
                        <td class="py-1.5 pr-3 text-gray-400">{{ entry.meta.get('analytics_optimize_status', '—') }}</td>
                        <td class="py-1.5 text-right">
                            {% set err_count = entry.meta.get('errors', []) | length %}
                            {% if err_count > 0 %}
                            <span class="text-red-400 font-medium">{{ err_count }}</span>
                            {% else %}
                            <span class="text-gray-600">0</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% else %}
        <p class="text-gray-600 text-sm">Sin ciclos registrados.</p>
        {% endif %}
    </section>

</main>

<script>
(function () {
    var chartData = {{ chart_data | tojson }};
    if (!chartData.labels || chartData.labels.length === 0) return;

    var ctx = document.getElementById('revenueChart');
    if (!ctx) return;

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: chartData.labels,
            datasets: [
                {
                    label: 'Ingresos',
                    data: chartData.revenue,
                    borderColor: 'rgb(74, 222, 128)',
                    backgroundColor: 'rgba(74, 222, 128, 0.08)',
                    borderWidth: 2,
                    pointRadius: 3,
                    tension: 0.3,
                    fill: true,
                },
                {
                    label: 'Gasto',
                    data: chartData.spend,
                    borderColor: 'rgb(248, 113, 113)',
                    backgroundColor: 'rgba(248, 113, 113, 0.08)',
                    borderWidth: 2,
                    pointRadius: 3,
                    tension: 0.3,
                    fill: true,
                }
            ]
        },
        options: {
            responsive: true,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { color: '#9ca3af', font: { size: 11 } } }
            },
            scales: {
                x: {
                    ticks: { color: '#6b7280', font: { size: 10 } },
                    grid: { color: 'rgba(55,65,81,0.5)' }
                },
                y: {
                    ticks: {
                        color: '#6b7280',
                        font: { size: 10 },
                        callback: function(v) { return '$' + v.toFixed(0); }
                    },
                    grid: { color: 'rgba(55,65,81,0.5)' }
                }
            }
        }
    });
})();
</script>

</body>
</html>
```

**Criterio:** Template renderiza sin error Jinja2 con datos reales. Chart.js muestra líneas si hay datos. Con DB vacía, muestra mensajes de "Sin datos" en cada sección.

---

## Wave 4 — Tests

### T7.7 — Crear `tests/test_dashboard.py`

**Archivo:** `tests/test_dashboard.py`

Tests del endpoint `/dashboard` y las funciones de datos con DB en memoria (aiosqlite).

```python
"""
Tests del Dashboard (Phase 7).
Usa httpx AsyncClient con TestClient de FastAPI.
La autenticación se prueba con credenciales correctas e incorrectas.
Las funciones de datos se prueban contra DB en memoria (aiosqlite).
"""
import base64

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock, patch


def _basic_auth_header(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ── T7.7.1 — Auth ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_without_auth_returns_401():
    """GET /dashboard sin Authorization → 401."""
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_with_wrong_credentials_returns_401():
    """GET /dashboard con credenciales incorrectas → 401."""
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/dashboard",
            headers=_basic_auth_header("wrong", "wrong"),
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_with_correct_credentials_returns_200():
    """GET /dashboard con credenciales correctas → 200 HTML."""
    from app.main import app
    from app.config import get_settings

    settings = get_settings()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/dashboard",
            headers=_basic_auth_header(
                settings.dashboard_username, settings.dashboard_password
            ),
        )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ── T7.7.2 — check_auth unit ─────────────────────────────────────────────────────

def test_check_auth_raises_401_on_wrong_password():
    """_check_auth() lanza HTTPException 401 con password incorrecto."""
    from fastapi import HTTPException
    from fastapi.security import HTTPBasicCredentials
    from app.api.dashboard import _check_auth

    creds = HTTPBasicCredentials(username="admin", password="wrongpass")
    with patch("app.api.dashboard.get_settings") as mock_settings:
        mock_settings.return_value.dashboard_username = "admin"
        mock_settings.return_value.dashboard_password = "secret"
        try:
            _check_auth(creds)
            assert False, "Debía lanzar HTTPException"
        except HTTPException as e:
            assert e.status_code == 401


def test_check_auth_returns_username_on_success():
    """_check_auth() retorna el username cuando las credenciales son correctas."""
    from fastapi.security import HTTPBasicCredentials
    from app.api.dashboard import _check_auth

    creds = HTTPBasicCredentials(username="admin", password="secret")
    with patch("app.api.dashboard.get_settings") as mock_settings:
        mock_settings.return_value.dashboard_username = "admin"
        mock_settings.return_value.dashboard_password = "secret"
        result = _check_auth(creds)
    assert result == "admin"


# ── T7.7.3 — Data queries con DB vacía ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_global_metrics_empty_db():
    """_get_global_metrics() retorna ceros con DB vacía."""
    from app.api.dashboard import _get_global_metrics
    from unittest.mock import AsyncMock, MagicMock

    mock_db = AsyncMock()
    mock_row = MagicMock()
    mock_row.total_spend = 0
    mock_row.total_revenue = 0
    mock_row.avg_roas = 0
    mock_result = MagicMock()
    mock_result.one.return_value = mock_row
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.scalar = AsyncMock(return_value=0)

    result = await _get_global_metrics(mock_db)

    assert result["total_spend"] == 0.0
    assert result["total_revenue"] == 0.0
    assert result["avg_roas"] == 0.0
    assert result["active_campaigns"] == 0


@pytest.mark.asyncio
async def test_get_agent_statuses_no_logs():
    """_get_agent_statuses() retorna 'red' / 'Sin datos' para todos los agentes sin logs."""
    from app.api.dashboard import _get_agent_statuses
    from unittest.mock import AsyncMock

    mock_db = AsyncMock()
    mock_db.scalar = AsyncMock(return_value=None)

    statuses = await _get_agent_statuses(mock_db)

    assert set(statuses.keys()) == {"research", "dropi", "campaign", "analytics", "orchestrator"}
    for agent, info in statuses.items():
        assert info["color"] == "red", f"Agente {agent} debería ser rojo sin logs"
        assert info["last_run"] is None


@pytest.mark.asyncio
async def test_get_agent_statuses_green_on_recent_success():
    """_get_agent_statuses() retorna 'green' para agente con log reciente y status success."""
    from app.api.dashboard import _get_agent_statuses
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, MagicMock

    mock_db = AsyncMock()
    mock_log = MagicMock()
    mock_log.status = "success"
    mock_log.created_at = datetime.now(timezone.utc)  # right now = definitely within interval
    mock_db.scalar = AsyncMock(return_value=mock_log)

    statuses = await _get_agent_statuses(mock_db)

    for info in statuses.values():
        assert info["color"] == "green"


@pytest.mark.asyncio
async def test_get_orchestrator_log_empty():
    """_get_orchestrator_log() retorna lista vacía sin logs."""
    from app.api.dashboard import _get_orchestrator_log
    from unittest.mock import AsyncMock, MagicMock

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await _get_orchestrator_log(mock_db)
    assert result == []


@pytest.mark.asyncio
async def test_get_chart_data_empty():
    """_get_chart_data() retorna listas vacías sin métricas."""
    from app.api.dashboard import _get_chart_data
    from unittest.mock import AsyncMock, MagicMock

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await _get_chart_data(mock_db)
    assert result == {"labels": [], "spend": [], "revenue": []}
```

**Criterio:** `pytest tests/test_dashboard.py` pasa. Tests de auth sin necesidad de DB real. Tests de data queries con mocks.

---

## Resumen de archivos a crear/modificar

| Acción | Archivo |
|--------|---------|
| MODIFICAR | `app/config.py` (+ 2 campos: dashboard_username, dashboard_password) |
| MODIFICAR | `pyproject.toml` (+ jinja2>=3.1) |
| CREAR | `app/api/dashboard.py` |
| MODIFICAR | `app/api/health.py` (estado real de agentes desde AgentLog) |
| MODIFICAR | `app/main.py` (+ include dashboard router) |
| CREAR | `app/templates/dashboard.html` |
| CREAR | `tests/test_dashboard.py` |

**Total: 3 nuevos + 4 modificados**

---

## Criterios de aceptación de la fase

- [ ] `GET /dashboard` sin auth → 401 con header `WWW-Authenticate: Basic`
- [ ] `GET /dashboard` con credenciales correctas → 200 HTML con todos los widgets
- [ ] `GET /dashboard` con credenciales incorrectas → 401
- [ ] Métricas globales muestran datos reales de la tabla `metrics` (o ceros si vacía)
- [ ] Tabla de productos muestra los productos activos con ROAS y gasto 7d
- [ ] Semáforo de agentes muestra verde/amarillo/rojo según último AgentLog
- [ ] Log del orquestador muestra hasta 50 ciclos con su status y detalles
- [ ] Gráfico de Chart.js renderiza líneas de gasto e ingresos (o mensaje si sin datos)
- [ ] Auto-refresh cada 60s via `<meta http-equiv="refresh">`
- [ ] `GET /api/v1/status` devuelve estado real de agentes (no "idle")
- [ ] `pytest tests/test_dashboard.py` pasa sin DB real ni Redis
- [ ] `DASHBOARD_USERNAME` y `DASHBOARD_PASSWORD` configurables via `.env`
