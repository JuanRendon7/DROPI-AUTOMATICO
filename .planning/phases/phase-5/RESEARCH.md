# Research — Fase 5: Analytics & Optimization Agent

**Fecha:** 2026-05-24  
**Fuentes:** Meta Developer Docs, TikTok Business API Docs, Google Ads API Docs, investigación directa.

---

## 1. Meta Marketing API — Insights

### Endpoint de métricas
```
GET /v21.0/{campaign_id}/insights
  ?fields=impressions,clicks,spend,actions,action_values
  &date_preset=yesterday
  &level=campaign
```

### Calcular ROAS desde Meta
```python
# action_values es una lista: [{"action_type": "purchase", "value": "150.00"}]
purchase_values = [
    float(av["value"])
    for av in action_values
    if av["action_type"] in ("offsite_conversion.fb_pixel_purchase", "purchase")
]
revenue = sum(purchase_values)
roas = revenue / float(spend) if float(spend) > 0 else 0.0
```

### Pausar campaña
```
POST /v21.0/{campaign_id}
Body: {"status": "PAUSED"}
```

### Escalar presupuesto (en adset)
```
POST /v21.0/{adset_id}
Body: {"daily_budget": 5000}  # en centavos USD
```

### Rate limits
- 200 llamadas/hora por app-token
- Insights: máximo 5 jobs async concurrentes por ad account
- Para datos diarios con `date_preset=yesterday` síncronos — sin problema

---

## 2. TikTok Marketing API v1.3 — Reports

### Endpoint de métricas
```
POST https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/
Headers: Access-Token: {token}
Body:
{
  "advertiser_id": "...",
  "report_type": "BASIC",
  "data_level": "AUCTION_CAMPAIGN",
  "dimensions": ["campaign_id", "stat_time_day"],
  "metrics": ["spend", "impressions", "clicks", "conversions", "total_purchase_value"],
  "start_date": "2025-05-23",
  "end_date": "2025-05-23",
  "page_size": 100
}
```

### Calcular ROAS desde TikTok
```python
roas = float(row["total_purchase_value"]) / float(row["spend"]) if float(row["spend"]) > 0 else 0.0
```
`total_purchase_value` es el campo directo de ingresos por compras atribuidas.

### Pausar campaña
```
POST https://business-api.tiktok.com/open_api/v1.3/campaign/update/
Body: {"advertiser_id": "...", "campaign_id": "...", "operation_status": "DISABLE"}
```

### Actualizar presupuesto (nivel adgroup)
```
POST https://business-api.tiktok.com/open_api/v1.3/adgroup/update/
Body: {"advertiser_id": "...", "adgroup_id": "...", "budget": 50.0}
```

---

## 3. Google Ads API — GAQL Reports

### Query GAQL para Performance Max
```sql
SELECT
  campaign.id,
  campaign.name,
  metrics.impressions,
  metrics.clicks,
  metrics.cost_micros,
  metrics.conversions,
  metrics.conversions_value
FROM campaign
WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
  AND segments.date = '2025-05-23'
```
Ejecutar con `GoogleAdsService.search(customer_id=..., query=...)`

### Calcular ROAS desde Google
```python
cost = metrics.cost_micros / 1_000_000  # cost_micros → USD
roas = metrics.conversions_value / cost if cost > 0 else 0.0
```

### Pausar campaña
```python
op = client.get_type("CampaignOperation")
op.update.resource_name = f"customers/{cid}/campaigns/{campaign_id}"
op.update.status = client.enums.CampaignStatusEnum.PAUSED
campaign_service.mutate_campaigns(customer_id=cid, operations=[op])
```

### Actualizar presupuesto
```python
op = client.get_type("CampaignBudgetOperation")
op.update.resource_name = f"customers/{cid}/campaignBudgets/{budget_id}"
op.update.amount_micros = int(new_budget_usd * 1_000_000)
budget_service.mutate_campaign_budgets(customer_id=cid, operations=[op])
```

---

## 4. Reglas de Optimización Autónoma (estándar industria 2025)

### Fase de aprendizaje
- Los algoritmos de Meta, TikTok y Google necesitan mínimo **7 días** o 50 eventos de conversión antes de estar optimizados
- **No pausar** campañas durante el aprendizaje aunque el ROAS sea bajo

### Reglas implementadas

| Condición | Acción | Justificación |
|-----------|--------|---------------|
| `days_active >= 7 AND roas < 1.5 AND spend > cpa_target * 2` | Pausar campaña | Break-even negativo confirmado |
| `days_active >= 7 AND roas > 3.0` | Escalar budget +20% | Campaña ganadora, amplificar |
| `days_active >= 7 AND ctr < 0.8%` | Registrar para rotar creativos | Fatiga de anuncio |
| `spend_hoy > avg_7d * 1.5` | Alerta Telegram (no pausar) | Posible spike anómalo |

### Límites de escalado seguros
- Máximo **+20% cada 3–4 días** — más agresivo reinicia el periodo de aprendizaje del algoritmo
- Nunca escalar más de 2x el budget actual en un solo paso
- Cap máximo de budget: `settings.campaign_daily_budget_usd * 5` (configurable)

---

## 5. Telegram Bot API — Alertas

### Endpoint
```
POST https://api.telegram.org/bot{TOKEN}/sendMessage
Content-Type: application/json
Body:
{
  "chat_id": "{CHAT_ID}",
  "text": "🔴 *ALERTA* ROAS bajo en campaña X: 1.2x",
  "parse_mode": "Markdown"
}
```

### Características
- HTTP síncrono — retorna inmediatamente
- Rate limit: 30 msg/seg globales, 1 msg/seg por chat — irrelevante para alertas diarias
- Se puede usar `httpx.AsyncClient` para mantener consistencia con el resto del stack
- `parse_mode: "Markdown"` soporta `*bold*`, `_italic_`, `` `code` ``

---

## 6. Estado del código base — hallazgos

### Ya implementado (reutilizar)
- `app/models/metric.py` — tabla `metrics` ya tiene todos los campos necesarios:
  - `impressions`, `clicks`, `conversions`, `spend_usd`, `revenue_usd`
  - `roas`, `ctr`, `cpc` (calculados)
  - `date` (por día), `campaign_id` (FK a campaigns)
- `app/models/campaign.py` — `external_id` (campaign ID de cada plataforma), `platform`, `status`, `daily_budget_usd`
- `app/config.py` — credenciales de Meta, TikTok, Google, Telegram ya declaradas
- `agents/research/llm_analyst.py` — `LLMAnalyst` reutilizable para reporte semanal
- `agents/campaign/platforms/meta.py`, `tiktok.py`, `google_ads.py` — clientes existentes tienen estructura de `httpx.AsyncClient` reutilizable

### No implementado (crear en Fase 5)
- `agents/analytics/` — directorio completo
- Tareas Celery para colección (08:00 COT) y optimización (10:00 COT)

### Decisión sobre clientes de plataformas
- NO reutilizaremos los clientes de `agents/campaign/platforms/` ya que su interfaz es para creación
- Crearemos clientes nuevos enfocados en **lectura de métricas y acciones de optimización**
- Comparten la misma autenticación httpx pero tienen métodos completamente diferentes

---

## 7. Arquitectura decidida

```
agents/analytics/
├── __init__.py
├── agent.py          # AnalyticsAgent — orquesta collect + optimize
├── models.py         # MetricSnapshot, OptimizationAction, WeeklyReport
├── optimizer.py      # Motor de reglas autónomas (pause / scale / alert)
├── reporter.py       # Reporte semanal con Claude
├── notifier.py       # Alertas Telegram vía httpx
└── platforms/
    ├── __init__.py
    ├── meta.py       # MetaInsightsClient — GET /{campaign_id}/insights
    ├── tiktok.py     # TikTokReportClient — POST /report/integrated/get/
    └── google_ads.py # GoogleAdsReportClient — GAQL search
```

### Flujo del agente
```
1. Para cada Campaign activa en DB:
   a. Obtener external_id y platform
   b. Llamar al cliente de la plataforma → MetricSnapshot
   c. Calcular ROAS, CTR, CPC
   d. Guardar en tabla metrics

2. Para cada Campaign con >= 7 días de métricas:
   a. Calcular ROAS promedio de los últimos 7 días
   b. Aplicar reglas del Optimizer
   c. Si acción → ejecutar vía API de la plataforma
   d. Registrar en AgentLog

3. Si es domingo → generar reporte semanal con Claude
4. Si hay anomalías → notificar por Telegram
```

### Scheduling
| Tarea | Hora COT | Descripción |
|-------|----------|-------------|
| `run_analytics_collect` | 08:00 | Recolectar métricas del día anterior |
| `run_analytics_optimize` | 10:00 | Aplicar reglas de optimización |
| `run_dropi_sync` | Cada 2h | (ya implementado) |
