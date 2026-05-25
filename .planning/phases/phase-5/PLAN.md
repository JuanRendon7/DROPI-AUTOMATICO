# Plan — Fase 5: Analytics & Optimization Agent

**Fase:** 5  
**Objetivo:** Recolectar métricas diarias de las 3 plataformas, calcular ROAS/CTR/CPC, y tomar decisiones autónomas de optimización (pausar, escalar, alertar)  
**Estimación:** 4–5 días  
**Dependencias de fase:** Fase 4 completada (Campaign Agent — tabla `campaigns` con `external_id` por plataforma)

---

## Wave 1 — Modelos, Optimizer y Notifier

### T5.1 — Crear `agents/analytics/models.py`

**Archivo:** `agents/analytics/models.py`

```python
from datetime import date
from pydantic import BaseModel


class MetricSnapshot(BaseModel):
    """Métricas de una campaña para un día específico."""
    campaign_db_id: str      # UUID de la tabla campaigns
    external_id: str         # ID en la plataforma (campaign_id de Meta/TikTok/Google)
    platform: str            # "meta" | "tiktok" | "google"
    date: date
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    spend_usd: float = 0.0
    revenue_usd: float = 0.0

    @property
    def roas(self) -> float:
        return round(self.revenue_usd / self.spend_usd, 4) if self.spend_usd > 0 else 0.0

    @property
    def ctr(self) -> float:
        return round(self.clicks / self.impressions, 6) if self.impressions > 0 else 0.0

    @property
    def cpc(self) -> float:
        return round(self.spend_usd / self.clicks, 4) if self.clicks > 0 else 0.0


class OptimizationAction(BaseModel):
    """Resultado de una decisión autónoma del optimizer."""
    campaign_db_id: str
    external_id: str
    platform: str
    action: str              # "pause" | "scale_budget" | "alert_spike" | "flag_low_ctr"
    reason: str
    old_value: float | None = None
    new_value: float | None = None
    executed: bool = False
    error: str | None = None


class WeeklyReport(BaseModel):
    """Reporte semanal generado por Claude."""
    week_start: date
    week_end: date
    total_spend_usd: float
    total_revenue_usd: float
    overall_roas: float
    top_campaign: str | None = None
    worst_campaign: str | None = None
    analysis_text: str = ""
    actions_taken: list[OptimizationAction] = []
```

**Criterio:** `from agents.analytics.models import MetricSnapshot, OptimizationAction` importa sin error.

---

### T5.2 — Crear `agents/analytics/optimizer.py`

**Archivo:** `agents/analytics/optimizer.py`

Motor de reglas autónomas. Toma decisiones basadas en métricas históricas.

```python
from dataclasses import dataclass
from datetime import date, timedelta

@dataclass
class OptimizerConfig:
    min_days_active: int = 7        # Días mínimos antes de tomar decisiones
    roas_pause_threshold: float = 1.5   # Pausar si ROAS < este valor
    roas_scale_threshold: float = 3.0   # Escalar si ROAS > este valor
    ctr_low_threshold: float = 0.008    # CTR < 0.8% → marcar para rotar creativos
    scale_factor: float = 1.20          # +20% al escalar presupuesto
    max_budget_multiplier: float = 5.0  # No escalar más de 5x el budget inicial
    spend_spike_multiplier: float = 1.5 # Alerta si gasto > 1.5x promedio 7 días


class ProductionOptimizer:
    def __init__(self, config: OptimizerConfig | None = None):
        self.config = config or OptimizerConfig()

    def evaluate_campaign(
        self,
        campaign_db_id: str,
        external_id: str,
        platform: str,
        daily_metrics: list[MetricSnapshot],  # ordenadas por fecha ASC
        current_budget_usd: float,
        initial_budget_usd: float,
    ) -> list[OptimizationAction]:
        """
        Evalúa una campaña y retorna lista de acciones recomendadas.
        Lista vacía = no hacer nada.
        """
        actions = []
        if len(daily_metrics) < self.config.min_days_active:
            return []  # En periodo de aprendizaje → no tocar

        # Métricas de los últimos 7 días
        recent = daily_metrics[-7:]
        avg_roas = sum(m.roas for m in recent) / len(recent)
        avg_spend = sum(m.spend_usd for m in recent) / len(recent)
        today_spend = daily_metrics[-1].spend_usd if daily_metrics else 0.0

        # Regla 1: Pausar por ROAS bajo
        if avg_roas < self.config.roas_pause_threshold and avg_spend > 0:
            actions.append(OptimizationAction(
                campaign_db_id=campaign_db_id,
                external_id=external_id,
                platform=platform,
                action="pause",
                reason=f"ROAS promedio 7d={avg_roas:.2f} < umbral {self.config.roas_pause_threshold}",
            ))

        # Regla 2: Escalar presupuesto si ROAS alto
        elif avg_roas > self.config.roas_scale_threshold:
            max_budget = initial_budget_usd * self.config.max_budget_multiplier
            new_budget = min(current_budget_usd * self.config.scale_factor, max_budget)
            if new_budget > current_budget_usd:
                actions.append(OptimizationAction(
                    campaign_db_id=campaign_db_id,
                    external_id=external_id,
                    platform=platform,
                    action="scale_budget",
                    reason=f"ROAS promedio 7d={avg_roas:.2f} > umbral {self.config.roas_scale_threshold}",
                    old_value=current_budget_usd,
                    new_value=round(new_budget, 2),
                ))

        # Regla 3: Detectar spike de gasto
        if avg_spend > 0 and today_spend > avg_spend * self.config.spend_spike_multiplier:
            actions.append(OptimizationAction(
                campaign_db_id=campaign_db_id,
                external_id=external_id,
                platform=platform,
                action="alert_spike",
                reason=f"Gasto hoy ${today_spend:.2f} > {self.config.spend_spike_multiplier}x promedio 7d ${avg_spend:.2f}",
            ))

        # Regla 4: CTR bajo → marcar para rotar creativos
        avg_ctr = sum(m.ctr for m in recent) / len(recent)
        if avg_ctr < self.config.ctr_low_threshold and avg_ctr > 0:
            actions.append(OptimizationAction(
                campaign_db_id=campaign_db_id,
                external_id=external_id,
                platform=platform,
                action="flag_low_ctr",
                reason=f"CTR promedio 7d={avg_ctr:.4%} < umbral {self.config.ctr_low_threshold:.4%}",
            ))

        return actions
```

**Criterio:** `ProductionOptimizer().evaluate_campaign(...)` retorna lista de `OptimizationAction`.

---

### T5.3 — Crear `agents/analytics/notifier.py`

**Archivo:** `agents/analytics/notifier.py`

Alertas vía Telegram Bot API.

```python
import httpx
from app.logger import get_logger

log = get_logger("analytics.notifier")
TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)

    async def send(self, message: str) -> bool:
        """Envía mensaje. Retorna True si exitoso."""
        if not self._enabled:
            log.debug("Telegram no configurado — mensaje omitido", message=message[:50])
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{TELEGRAM_API}/bot{self._token}/sendMessage",
                    json={"chat_id": self._chat_id, "text": message, "parse_mode": "Markdown"},
                )
                response.raise_for_status()
                log.info("Telegram: mensaje enviado")
                return True
        except Exception as exc:
            log.warning("Telegram: fallo al enviar alerta", error=str(exc))
            return False

    async def send_optimization_summary(self, actions: list, date_str: str) -> None:
        """Envía resumen de acciones tomadas hoy."""
        if not actions:
            return
        lines = [f"*Optimizaciones automáticas — {date_str}*\n"]
        for action in actions:
            emoji = {"pause": "⏸", "scale_budget": "📈", "alert_spike": "🔴", "flag_low_ctr": "⚠️"}.get(action.action, "ℹ️")
            status = "✅ ejecutado" if action.executed else ("❌ error" if action.error else "📋 pendiente")
            lines.append(f"{emoji} `{action.platform.upper()}` — {action.action}: {action.reason[:80]} | {status}")
        await self.send("\n".join(lines))
```

**Criterio:** `TelegramNotifier` con token vacío retorna `False` sin lanzar excepción.

---

## Wave 2 — Clientes de Plataformas para Métricas

### T5.4 — Crear `agents/analytics/platforms/meta.py`

**Archivo:** `agents/analytics/platforms/meta.py`  
**API:** Meta Graph API v21.0 — Insights endpoint

```python
class MetaInsightsClient:
    BASE_URL = "https://graph.facebook.com/v21.0"

    def __init__(self, access_token: str, ad_account_id: str) -> None:
        self._token = access_token
        self._account = ad_account_id
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self): ...
    async def __aexit__(self, *_): ...
```

**Método principal: `get_campaign_metrics(campaign_id, target_date) → MetricSnapshot | None`**

```python
async def get_campaign_metrics(self, campaign_id: str, campaign_db_id: str, target_date: date) -> MetricSnapshot | None:
    date_str = target_date.strftime("%Y-%m-%d")
    params = {
        "fields": "impressions,clicks,spend,actions,action_values",
        "time_range": json.dumps({"since": date_str, "until": date_str}),
        "level": "campaign",
        "access_token": self._token,
    }
    response = await self._client.get(f"{BASE_URL}/{campaign_id}/insights", params=params)
    response.raise_for_status()
    data = response.json()
    if not data.get("data"):
        return None  # sin datos para esa fecha
    row = data["data"][0]
    # Calcular revenue desde action_values
    revenue = sum(
        float(av["value"])
        for av in row.get("action_values", [])
        if av["action_type"] in ("offsite_conversion.fb_pixel_purchase", "purchase")
    )
    return MetricSnapshot(
        campaign_db_id=campaign_db_id,
        external_id=campaign_id,
        platform="meta",
        date=target_date,
        impressions=int(row.get("impressions", 0)),
        clicks=int(row.get("clicks", 0)),
        spend_usd=float(row.get("spend", 0)),
        revenue_usd=revenue,
    )
```

**Método: `pause_campaign(campaign_id) → bool`**  
POST `/{campaign_id}` con `{"status": "PAUSED"}`

**Método: `update_adset_budget(adset_id, daily_budget_usd) → bool`**  
POST `/{adset_id}` con `{"daily_budget": int(daily_budget_usd * 100)}`

**Criterio:** `MetaInsightsClient` instancia y métodos tienen firma correcta.

---

### T5.5 — Crear `agents/analytics/platforms/tiktok.py`

**Archivo:** `agents/analytics/platforms/tiktok.py`  
**API:** TikTok Marketing API v1.3 — Report endpoint

```python
class TikTokReportClient:
    BASE_URL = "https://business-api.tiktok.com/open_api/v1.3"

    def __init__(self, access_token: str, advertiser_id: str) -> None: ...
```

**Método: `get_campaign_metrics(campaign_id, campaign_db_id, target_date) → MetricSnapshot | None`**

```python
body = {
    "advertiser_id": self._advertiser_id,
    "report_type": "BASIC",
    "data_level": "AUCTION_CAMPAIGN",
    "dimensions": ["campaign_id", "stat_time_day"],
    "metrics": ["spend", "impressions", "clicks", "conversions", "total_purchase_value"],
    "filters": [{"field_name": "campaign_ids", "filter_type": "IN", "filter_value": f'["{campaign_id}"]'}],
    "start_date": date_str,
    "end_date": date_str,
    "page_size": 10,
}
```
Response: `data.list[0].metrics` → extraer campos directamente.

**Método: `pause_campaign(campaign_id) → bool`**  
POST `/campaign/update/` con `{"operation_status": "DISABLE"}`

**Método: `update_adgroup_budget(adgroup_id, daily_budget_usd) → bool`**  
POST `/adgroup/update/` con `{"budget": daily_budget_usd}`

**Criterio:** `TikTokReportClient` instancia y métodos con firma correcta.

---

### T5.6 — Crear `agents/analytics/platforms/google_ads.py`

**Archivo:** `agents/analytics/platforms/google_ads.py`

```python
class GoogleAdsReportClient:
    def __init__(self, developer_token, customer_id, client_id="", client_secret="", refresh_token=""):
        ...

    def _is_configured(self) -> bool: ...

    async def get_campaign_metrics(self, campaign_id: str, campaign_db_id: str, target_date: date) -> MetricSnapshot | None:
        """Usa GAQL para obtener métricas de la campaña."""
        if not self._is_configured():
            return None
        query = f"""
            SELECT
                campaign.id,
                metrics.impressions,
                metrics.clicks,
                metrics.cost_micros,
                metrics.conversions,
                metrics.conversions_value
            FROM campaign
            WHERE campaign.id = {campaign_id}
              AND segments.date = '{target_date.strftime("%Y-%m-%d")}'
        """
        client = self._build_client()
        gas = client.get_service("GoogleAdsService")
        response = gas.search(customer_id=self._customer_id, query=query)
        for row in response:
            m = row.metrics
            cost = m.cost_micros / 1_000_000
            return MetricSnapshot(
                campaign_db_id=campaign_db_id,
                external_id=campaign_id,
                platform="google",
                date=target_date,
                impressions=m.impressions,
                clicks=m.clicks,
                spend_usd=cost,
                revenue_usd=m.conversions_value,
                conversions=int(m.conversions),
            )
        return None  # sin datos

    async def pause_campaign(self, campaign_id: str) -> bool: ...
    async def update_campaign_budget(self, budget_resource_name: str, daily_budget_usd: float) -> bool: ...
```

**Criterio:** Si `customer_id` vacío, todos los métodos retornan `None`/`False` sin error.

---

## Wave 3 — Reporter, Agent Principal e Integración Celery

### T5.7 — Crear `agents/analytics/reporter.py`

**Archivo:** `agents/analytics/reporter.py`

Genera el reporte semanal en Markdown usando Claude.

```python
class AnalyticsReporter:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate_weekly_report(
        self,
        metrics_by_campaign: dict,  # campaign_name → list[MetricSnapshot]
        actions_taken: list[OptimizationAction],
    ) -> str:
        """Genera análisis semanal de performance en Markdown."""
        # Formatear stats
        # Construir prompt con: gastos totales, ROAS por plataforma, acciones tomadas
        # Pedir a Claude: análisis + 3 recomendaciones concretas
        ...
```

**Formato del reporte generado:**
```markdown
# Reporte Semanal — {fecha_inicio} al {fecha_fin}

## Resumen ejecutivo
- Gasto total: $XXX USD
- ROAS global: X.Xx
- Plataforma más rentable: Meta/TikTok/Google

## Performance por plataforma
...

## Acciones tomadas esta semana
...

## Recomendaciones para la próxima semana
1. ...
2. ...
3. ...
```

**Criterio:** `AnalyticsReporter.generate_weekly_report(...)` retorna string con Markdown.

---

### T5.8 — Crear `agents/analytics/agent.py`

**Archivo:** `agents/analytics/agent.py`

```python
class AnalyticsAgent:
    """
    Agente de analítica y optimización autónoma.

    Modo collect (08:00 COT): recolecta métricas del día anterior de las 3 plataformas.
    Modo optimize (10:00 COT): aplica reglas del Optimizer y ejecuta acciones vía API.

    Llamado por el Orchestrator (Fase 6) diariamente.
    """

    def __init__(self, settings: Settings) -> None: ...

    async def collect_metrics(self, db: AsyncSession) -> list[MetricSnapshot]:
        """
        Paso 1 — collect:
        Para cada Campaign activa en DB con external_id:
          → llamar al cliente de su plataforma
          → guardar MetricSnapshot en tabla metrics
        """

    async def run_optimization(self, db: AsyncSession) -> list[OptimizationAction]:
        """
        Paso 2 — optimize:
        Para cada Campaign con >= 7 días de métricas:
          → calcular ROAS promedio 7 días
          → aplicar ProductionOptimizer.evaluate_campaign()
          → ejecutar acciones vía API de la plataforma
          → actualizar Campaign.status en DB si se pausó
          → registrar en AgentLog
          → notificar por Telegram
        """

    async def run_weekly_report(self, db: AsyncSession) -> str:
        """
        Genera reporte semanal (llamado los domingos).
        Retorna el Markdown del reporte.
        """
```

**Detalle de `collect_metrics`:**
```python
async def collect_metrics(self, db: AsyncSession) -> list[MetricSnapshot]:
    yesterday = date.today() - timedelta(days=1)

    # Obtener campañas activas con external_id
    campaigns = await db.execute(
        select(Campaign)
        .where(Campaign.status == "active")
        .where(Campaign.external_id.is_not(None))
    )
    campaigns = campaigns.scalars().all()

    snapshots = []
    for campaign in campaigns:
        snapshot = await self._get_platform_metrics(campaign, yesterday)
        if snapshot:
            db.add(Metric(
                campaign_id=campaign.id,
                date=yesterday,
                impressions=snapshot.impressions,
                clicks=snapshot.clicks,
                conversions=snapshot.conversions,
                spend_usd=snapshot.spend_usd,
                revenue_usd=snapshot.revenue_usd,
                roas=snapshot.roas,
                ctr=snapshot.ctr,
                cpc=snapshot.cpc,
            ))
            snapshots.append(snapshot)

    await db.commit()
    return snapshots
```

**Detalle de `run_optimization`:**
```python
async def run_optimization(self, db: AsyncSession) -> list[OptimizationAction]:
    # Para cada campaña: cargar últimos 30 días de métricas de la DB
    # Llamar a ProductionOptimizer.evaluate_campaign()
    # Para cada acción:
    #   - "pause" → llamar a {platform}_client.pause_campaign()
    #   - "scale_budget" → llamar a {platform}_client.update_budget()
    #   - "alert_spike" / "flag_low_ctr" → solo registrar y notificar
    # Actualizar Campaign.status = "paused" si se pausó
    # Enviar summary por Telegram
    # Persistir en AgentLog
```

**Criterio:** `AnalyticsAgent.collect_metrics(db)` retorna lista de `MetricSnapshot` y guarda en DB.

---

### T5.9 — Crear `agents/analytics/__init__.py`

```python
from agents.analytics.agent import AnalyticsAgent
from agents.analytics.models import MetricSnapshot, OptimizationAction

__all__ = ["AnalyticsAgent", "MetricSnapshot", "OptimizationAction"]
```

---

### T5.10 — Actualizar `app/tasks.py` — agregar tareas de analytics

**Archivo:** `app/tasks.py`  
**Cambios:** 2 nuevas tareas Celery

```python
@celery_app.task(name="app.tasks.run_analytics_collect", bind=True, max_retries=2)
def run_analytics_collect(self):
    """Recolecta métricas del día anterior. Programado: 08:00 COT diario."""
    try:
        asyncio.run(_run_analytics_collect_async())
    except Exception as exc:
        log.error("run_analytics_collect falló", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.tasks.run_analytics_optimize", bind=True, max_retries=2)
def run_analytics_optimize(self):
    """Aplica reglas de optimización. Programado: 10:00 COT diario."""
    try:
        asyncio.run(_run_analytics_optimize_async())
    except Exception as exc:
        log.error("run_analytics_optimize falló", error=str(exc))
        raise self.retry(exc=exc, countdown=300)
```

Más sus correspondientes funciones `async`:
- `_run_analytics_collect_async()` → crea `AnalyticsAgent`, llama `collect_metrics(db)`
- `_run_analytics_optimize_async()` → crea `AnalyticsAgent`, llama `run_optimization(db)`

**Criterio:** `from app.tasks import run_analytics_collect, run_analytics_optimize` importa sin error.

---

### T5.11 — Actualizar `app/celeryconfig.py` — scheduling analytics

**Archivo:** `app/celeryconfig.py`  
**Cambios:** 2 nuevas entradas en beat_schedule

```python
# Analytics collect: diario 08:00 COT
"analytics-collect-daily": {
    "task": "app.tasks.run_analytics_collect",
    "schedule": crontab(hour=8, minute=0),
},
# Analytics optimize: diario 10:00 COT
"analytics-optimize-daily": {
    "task": "app.tasks.run_analytics_optimize",
    "schedule": crontab(hour=10, minute=0),
},
```

**Criterio:** `beat_schedule` tiene 5 entradas total (research, campaign, collect, optimize, dropi-sync).

---

## Wave 4 — Tests

### T5.12 — Crear `tests/test_analytics_agent.py`

**Archivo:** `tests/test_analytics_agent.py`

```python
# ── Tests: MetricSnapshot ──────────────────────────────────────────────────────

def test_metric_snapshot_roas_calculation():
    """ROAS = revenue / spend."""

def test_metric_snapshot_ctr_calculation():
    """CTR = clicks / impressions."""

def test_metric_snapshot_zero_spend_returns_zero_roas():
    """Sin gasto, ROAS = 0.0 (no división por cero)."""

# ── Tests: ProductionOptimizer ─────────────────────────────────────────────────

def test_optimizer_no_action_during_learning_period():
    """Con < 7 días de datos, no hay acciones."""

def test_optimizer_pauses_low_roas_campaign():
    """ROAS < 1.5 durante 7 días → acción "pause"."""

def test_optimizer_scales_high_roas_campaign():
    """ROAS > 3.0 durante 7 días → acción "scale_budget"."""

def test_optimizer_detects_spend_spike():
    """Gasto hoy > 1.5x promedio 7d → acción "alert_spike"."""

def test_optimizer_flags_low_ctr():
    """CTR < 0.8% promedio → acción "flag_low_ctr"."""

def test_optimizer_respects_max_budget_cap():
    """No escala más de 5x el budget inicial."""

def test_optimizer_no_scale_when_at_max():
    """Si ya está al máximo, no genera scale_budget."""

# ── Tests: TelegramNotifier ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_telegram_notifier_skips_when_unconfigured():
    """Sin token, send() retorna False sin llamar a la API."""

@pytest.mark.asyncio
@respx.mock
async def test_telegram_notifier_sends_message():
    """Con token y chat_id, envía mensaje correctamente."""

# ── Tests: MetaInsightsClient ──────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_meta_insights_returns_metric_snapshot():
    """Mock de GET /insights → retorna MetricSnapshot con ROAS calculado."""

@pytest.mark.asyncio
@respx.mock
async def test_meta_insights_returns_none_when_no_data():
    """Si Meta retorna data=[], retorna None."""

@pytest.mark.asyncio
@respx.mock
async def test_meta_pause_campaign_success():
    """pause_campaign() hace POST con status=PAUSED."""

# ── Tests: TikTokReportClient ──────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_tiktok_report_returns_metric_snapshot():
    """Mock de POST /report/integrated/get/ → MetricSnapshot."""

# ── Tests: GoogleAdsReportClient ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_google_report_returns_none_when_unconfigured():
    """Sin customer_id, get_campaign_metrics() retorna None."""

# ── Tests: AnalyticsAgent (integración) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_collect_saves_metrics_to_db(db_session):
    """collect_metrics() guarda Metric en la DB para cada campaña activa."""

@pytest.mark.asyncio
async def test_analytics_optimize_pauses_bad_campaign(db_session):
    """Con ROAS < 1.5 durante 7+ días, campaign queda status='paused' en DB."""

@pytest.mark.asyncio
async def test_analytics_optimize_persists_agent_log(db_session):
    """run_optimization() genera AgentLog con agent='analytics'."""

@pytest.mark.asyncio
async def test_analytics_optimize_no_action_during_learning(db_session):
    """Campaña con < 7 días de métricas no genera acciones."""
```

**Criterio:** `pytest tests/test_analytics_agent.py` — todos los tests pasan sin llamadas reales.

---

## Resumen de archivos a crear/modificar

| Acción | Archivo |
|--------|---------|
| CREAR | `agents/analytics/__init__.py` |
| CREAR | `agents/analytics/models.py` |
| CREAR | `agents/analytics/optimizer.py` |
| CREAR | `agents/analytics/notifier.py` |
| CREAR | `agents/analytics/reporter.py` |
| CREAR | `agents/analytics/agent.py` |
| CREAR | `agents/analytics/platforms/__init__.py` |
| CREAR | `agents/analytics/platforms/meta.py` |
| CREAR | `agents/analytics/platforms/tiktok.py` |
| CREAR | `agents/analytics/platforms/google_ads.py` |
| CREAR | `tests/test_analytics_agent.py` |
| MODIFICAR | `app/tasks.py` (+ 2 tareas + 2 funciones async) |
| MODIFICAR | `app/celeryconfig.py` (+ 2 entradas beat_schedule) |

**Total: 11 nuevos + 2 modificados**

---

## Criterios de aceptación de la fase

- [ ] `from agents.analytics import AnalyticsAgent` importa sin error
- [ ] `AnalyticsAgent.collect_metrics(db)` guarda registros en tabla `metrics` por cada campaña activa
- [ ] `AnalyticsAgent.run_optimization(db)` aplica reglas y ejecuta acciones vía API
- [ ] `ProductionOptimizer` no toma acciones durante los primeros 7 días (periodo de aprendizaje)
- [ ] Campañas pausadas actualizan `Campaign.status = "paused"` en DB
- [ ] `AgentLog` se crea con `agent="analytics"` para collect y optimize
- [ ] `TelegramNotifier` sin credenciales retorna `False` sin error
- [ ] `pytest tests/test_analytics_agent.py` pasa sin llamadas reales a APIs externas
- [ ] `beat_schedule` en celeryconfig tiene 5 entradas (research, campaign, collect, optimize, dropi-sync)
