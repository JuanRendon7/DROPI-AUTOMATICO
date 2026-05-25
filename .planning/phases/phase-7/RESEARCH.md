# Research — Fase 7: Dashboard y Monitoreo

**Fecha:** 2026-05-24  
**Objetivo:** Investigar el stack mínimo para un dashboard web funcional sobre FastAPI existente, con autenticación básica, datos reales de la DB, y visualizaciones client-side.

---

## 1. FastAPI + Jinja2 Templates

### Setup en FastAPI 0.111+

```python
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

@router.get("/dashboard")
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"key": "value"})
```

**Cambios en FastAPI 0.111:**
- La nueva API pasa `request` como primer argumento posicional a `TemplateResponse`
- La API antigua (`{"request": request}` en el context dict) sigue funcionando pero está deprecada
- `directory` puede ser path relativo — funciona si la app se lanza desde el root del proyecto

**Dependencia a agregar:**
```toml
# En [project.dependencies] de pyproject.toml:
"jinja2>=3.1",
```

Jinja2 no viene incluido en el paquete base `fastapi` — solo en `fastapi[standard]`. El proyecto usa `fastapi>=0.111` (sin extras), así que hay que agregarlo explícitamente.

---

## 2. HTTP Basic Auth con FastAPI

```python
import secrets
from typing import Annotated
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

def check_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> str:
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
```

**Claves:**
- `secrets.compare_digest()` previene timing attacks (crucial para auth)
- Trabajar con `.encode()` para comparación byte-safe
- `headers={"WWW-Authenticate": "Basic"}` hace que el browser muestre el popup nativo
- Las credenciales van en `Settings` como campos con defaults seguros

---

## 3. Stack de Frontend — Decisiones

### Opción elegida: Tailwind CDN + Chart.js CDN + meta refresh

| Tecnología | Decisión | Razón |
|---|---|---|
| CSS | Tailwind CDN | Sin npm, sin build step, suficiente para dashboard de monitoreo |
| Gráfico | Chart.js 4.x CDN | Líneas de tiempo simples, maduro, sin dependencias |
| Actualización | `<meta http-equiv="refresh" content="60">` | Más simple que HTMX, sin JS extra |
| SPA | ❌ No | El dashboard es consulta, no CRUD interactivo |

**URLs de CDN:**
```html
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
```

**Nota:** El CDN de Tailwind incluye el motor JIT completo — carga ~112KB. Para un dashboard interno de monitoreo es completamente aceptable.

---

## 4. Queries de datos reales

### Global metrics (últimos 30 días)
```python
from sqlalchemy import func, select

result = await db.execute(
    select(
        func.coalesce(func.sum(Metric.spend_usd), 0).label("total_spend"),
        func.coalesce(func.sum(Metric.revenue_usd), 0).label("total_revenue"),
        func.coalesce(func.avg(Metric.roas), 0).label("avg_roas"),
    ).where(Metric.date >= cutoff)
)
row = result.one()
```

### Agent status (semáforo verde/amarillo/rojo)
```python
# Último AgentLog por agente
last = await db.scalar(
    select(AgentLog)
    .where(AgentLog.agent == agent_name)
    .order_by(AgentLog.created_at.desc())
    .limit(1)
)
```

**Lógica de semáforo:**
- 🔴 Rojo: `status == "failure"` OR nunca ejecutó
- 🟡 Amarillo: `status == "retry"` OR ejecutó pero está retrasado (>intervalo esperado)
- 🟢 Verde: `status in ("success", "partial")` AND dentro del intervalo esperado

**Intervalos esperados por agente:**
| Agente | Intervalo schedule | Grace period | Max edad verde |
|---|---|---|---|
| research | 24h | +2h | 26h |
| dropi | 2h | +30min | 2.5h |
| campaign | 24h | +2h | 26h |
| analytics | 24h | +2h | 26h |
| orchestrator | 24h | +2h | 26h |

### Chart data (7 días)
```python
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
```

---

## 5. Jinja2 — Filtros útiles para el template

```jinja2
{# Formatear número con 2 decimales #}
{{ value | round(2) }}
{{ "%.2f" | format(value) }}

{# Serializar a JSON para Chart.js #}
{{ chart_data | tojson }}

{# Condicional de Tailwind en template #}
{% if color == 'green' %}text-green-400{% elif color == 'yellow' %}text-yellow-400{% else %}text-red-400{% endif %}

{# Iterar dict #}
{% for name, info in agents.items() %}
```

**`tojson` filter:** Disponible en Jinja2 por defecto. Produce JSON válido y seguro para incrustar en `<script>`.

---

## 6. Actualización del endpoint `/api/v1/status`

El endpoint ya existe en `app/api/health.py`. Actualmente devuelve `"idle"` para todos los agentes. Debe actualizarse para devolver estado real desde AgentLog:

```python
@router.get("/api/v1/status")
async def detailed_status():
    # ... (PostgreSQL + Redis check existentes) ...
    
    # Nuevo: estado real de agentes
    async with AsyncSessionLocal() as db:
        agents = {}
        for agent_name in ["research", "dropi", "campaign", "analytics", "orchestrator"]:
            last = await db.scalar(
                select(AgentLog)
                .where(AgentLog.agent == agent_name)
                .order_by(AgentLog.created_at.desc())
                .limit(1)
            )
            agents[agent_name] = last.status if last else "never_run"
    
    body["agents"] = agents
```

---

## 7. `app/main.py` — No necesita StaticFiles

Dado que usamos CDNs para Tailwind y Chart.js, NO necesitamos montar `StaticFiles`. Esto evita agregar `aiofiles` como dependencia extra.

Solo se necesita:
1. Importar e incluir el router del dashboard
2. (Jinja2Templates se instancia dentro de `dashboard.py`)

---

## Decisiones de diseño para Phase 7

| Decisión | Elección | Razón |
|---|---|---|
| Framework UI | FastAPI + Jinja2 (server-rendered) | Sin build step, mínima complejidad |
| CSS | Tailwind CDN | Velocidad de desarrollo, sin npm |
| Gráfico | Chart.js CDN | Maduro, líneas simples, fácil integración con Jinja2 |
| Auto-refresh | meta http-equiv refresh (60s) | Más simple que WebSockets o HTMX |
| Auth | HTTP Basic via FastAPI HTTPBasic | Nativo en FastAPI, popup browser nativo |
| Credenciales | En Settings (`.env`) | Consistente con el resto del proyecto |
| StaticFiles | ❌ No | Sin archivos estáticos propios — todo CDN |
| Template path | `app/templates/` (relativo) | Estándar de FastAPI |

---

## Archivos a crear/modificar

| Acción | Archivo |
|--------|---------|
| MODIFICAR | `app/config.py` (+ dashboard_username, dashboard_password) |
| MODIFICAR | `pyproject.toml` (+ jinja2>=3.1) |
| CREAR | `app/api/dashboard.py` |
| MODIFICAR | `app/main.py` (+ include router) |
| MODIFICAR | `app/api/health.py` (+ real agent status) |
| CREAR | `app/templates/dashboard.html` |
| CREAR | `tests/test_dashboard.py` |
