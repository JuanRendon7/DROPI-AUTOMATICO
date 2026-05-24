# Research — Fase 4: Campaign Agent

**Fecha:** 2026-05-24  
**Fuentes:** Meta Developer Docs, TikTok Business API Docs, Google Ads API Docs, google-ads PyPI, investigación directa.

---

## 1. Meta Marketing API

### Versión y autenticación
- **API recomendada:** v21.0 (v20 deprecada mayo 2025; v24 introdujo restricciones en campañas ASC/AAC)
- **Auth:** Header `Authorization: Bearer {access_token}` — token de larga duración del Business Manager
- **SDK Python:** No usaremos `facebook-business` — usaremos `httpx` directamente (ya en el stack) para evitar dependencia extra

### Estructura de creación de campaña (3 pasos)

```
1. POST /act_{ad_account_id}/campaigns
   → Retorna: campaign_id

2. POST /act_{ad_account_id}/adsets
   → Requiere: campaign_id, targeting, budget, schedule
   → Retorna: adset_id

3. POST /act_{ad_account_id}/adcreatives  (primero)
   POST /act_{ad_account_id}/ads          (luego)
   → Requiere: adset_id, creative_id
   → Retorna: ad_id
```

### Payload mínimo funcional
```json
// Campaign
{ "name": "...", "objective": "OUTCOME_TRAFFIC", "status": "PAUSED",
  "special_ad_categories": [] }

// AdSet
{ "name": "...", "campaign_id": "...", "billing_event": "IMPRESSIONS",
  "optimization_goal": "LINK_CLICKS", "daily_budget": 10000,
  "targeting": {"geo_locations": {"countries": ["CO"]}},
  "status": "PAUSED", "start_time": "..." }

// AdCreative
{ "name": "...", "object_story_spec": {
    "page_id": "{page_id}",
    "link_data": { "image_hash": "...", "link": "{product_url}",
                   "message": "...", "name": "...", "call_to_action": {"type": "SHOP_NOW"} }
  }}
```

### Upload de imagen
```
POST /{ad_account_id}/adimages  (multipart, campo: filename + bytes)
→ Retorna: image_hash (no image_id — diferente al resto de APIs)
```

### Rate limits
- Basados en Business Use Case (BUC) — no fijo público
- Monitorear headers de respuesta `X-Business-Use-Case-Usage`
- En tier Development: límites bajos, suficiente para pruebas

### Gotchas importantes
- `daily_budget` se expresa en **centavos** (10000 = $100 USD o $100 de la moneda de la cuenta)
- `status: "PAUSED"` para crear en pausa y activar después (más seguro)
- Requiere Page ID (Facebook Page) para crear creativos — no solo la cuenta de anuncios
- `special_ad_categories: []` es obligatorio aunque sea array vacío
- Verificación de Business Manager puede tardar 1–3 días

---

## 2. TikTok Marketing API

### Versión y autenticación
- **API:** v1.3 (estable, sin breaking changes en 2025-2026)
- **Base URL:** `https://business-api.tiktok.com`
- **Auth:** Header `Access-Token: {access_token}` — expira en 24h, refresh_token dura 365 días
- **SDK Python:** No hay SDK maduro — `httpx` directo es más limpio

### Estructura de creación (3 pasos)

```
1. POST /open_api/v1.3/campaign/create/
   → Retorna: campaign_id

2. POST /open_api/v1.3/adgroup/create/
   → Requiere: campaign_id, location_ids, budget, schedule
   → Retorna: adgroup_id

3. POST /open_api/v1.3/ad/create/
   → Requiere: adgroup_id, image_ids, ad_text, landing_page_url
   → Retorna: ad_id
```

### Payload mínimo funcional
```json
// Campaign
{ "advertiser_id": "...", "campaign_name": "...",
  "objective_type": "TRAFFIC", "budget_mode": "BUDGET_MODE_DAY", "budget": 10 }

// AdGroup
{ "advertiser_id": "...", "campaign_id": "...", "adgroup_name": "...",
  "placement_type": "PLACEMENT_TYPE_AUTOMATIC",
  "location_ids": ["6252001"],
  "budget_mode": "BUDGET_MODE_DAY", "budget": 10,
  "schedule_type": "SCHEDULE_FROM_NOW",
  "optimize_goal": "CLICK", "billing_event": "CPC",
  "bid_type": "BID_TYPE_NO_BID" }

// Ad
{ "advertiser_id": "...", "adgroup_id": "...", "ad_name": "...",
  "ad_format": "SINGLE_IMAGE", "image_ids": ["..."],
  "ad_text": "...", "landing_page_url": "..." }
```

### Upload de imagen
```
POST /open_api/v1.3/file/image/ad/upload/  (multipart, campo: image_file)
→ Retorna: image_id (string)
```

### Sandbox disponible
- Portal de desarrolladores TikTok — activar sandbox por proyecto
- Permite crear campañas sin gastar dinero real
- `location_ids`: Colombia = "6252001" (ISO 3166 numeric)

### Gotchas importantes
- Token de acceso expira en **24 horas** — necesita flujo de refresh automático
- `bid_type: "BID_TYPE_NO_BID"` para optimización automática (recomendado para inicio)
- Requiere cuenta de TikTok For Business aprobada (no instantáneo)
- Para formato vertical/story usar `ad_format: "VIDEO"` con video propio; para imagen usar `"SINGLE_IMAGE"`

---

## 3. Google Ads API

### Versión y autenticación
- **API versión:** v23.1 (feb 2026) — biblioteca Python `google-ads>=24.0` (que instalará v26.x)
- **Auth:** OAuth2 + Developer Token obligatorio + `google-ads.yaml` config file
- **Tipo de campaña recomendado:** Performance Max (PMax) — único formato óptimo para e-commerce 2026

### Estructura de creación (diferente a los otros)

```
Campaign (PERFORMANCE_MAX)
  └── AssetGroup (reemplaza AdGroup en PMax)
       └── Assets (images, headlines, descriptions, logos)
```

```python
# Requiere MutateOperations en una sola llamada:
1. CampaignBudgetOperation
2. CampaignOperation (PERFORMANCE_MAX + MAXIMIZE_CONVERSION_VALUE)
3. AssetGroupOperation (headlines x3+, descriptions x2+, images)
4. AssetGroupAssetOperation (vincular assets al AssetGroup)
```

### Gotcha CRÍTICO para dropshipping
- Performance Max **requiere landing page** con dominio propio verificado en Google
- No acepta links directos a tiendas de terceros sin verificación del merchant
- **Solución para v1:** Implementar pero marcar como OPTIONAL — se activa solo si `GOOGLE_ADS_CUSTOMER_ID` está configurado y se tiene merchant center listo
- Para dropshipping puro, Google Ads es el canal más difícil de implementar sin landing propia

### Decisión de implementación
Google Ads se implementa como cliente funcional completo pero:
- Configuración guarded por `settings.google_ads_customer_id != ""`
- En ausencia de credenciales: CampaignAgent registra `skipped` en el log y continúa con Meta + TikTok
- Esto permite lanzar campañas reales en Meta y TikTok mientras se resuelve el setup de Google

---

## 4. Estado del código base — hallazgos

### Ya implementado (reutilizar)
- `app/models/campaign.py` — tabla `campaigns` con `platform`, `external_id`, `status`, `daily_budget_usd`, `product_id` FK
- `app/config.py` — credenciales de Meta, TikTok y Google Ads ya declaradas
- `agents/research/llm_analyst.py` → `suggest_ad_copy(product, platform)` — genera `{headline, body, cta}` por plataforma
- `.env.example` — variables de entorno Meta/TikTok/Google ya documentadas

### No implementado (crear en Fase 4)
- `agents/campaign/` — directorio completo (no existe)
- Tarea Celery para creación de campañas
- Scheduling de campañas post-research

### Dependencia a agregar en pyproject.toml
- No se agregan dependencias nuevas — Meta y TikTok via `httpx` (ya en deps), Google via `google-ads>=24.0` (ya en agents extras)

---

## 5. Arquitectura decidida

```
agents/campaign/
├── __init__.py
├── agent.py              # CampaignAgent — orquesta Meta + TikTok + Google
├── models.py             # CampaignRequest, PlatformCampaignResult, CampaignResult
├── image_handler.py      # Descarga imágenes de URLs, las prepara para upload
└── platforms/
    ├── __init__.py
    ├── base.py           # AbstractAdsPlatform (ABC)
    ├── meta.py           # MetaAdsClient (httpx + Graph API v21.0)
    ├── tiktok.py         # TikTokAdsClient (httpx + v1.3)
    └── google_ads.py     # GoogleAdsClient (google-ads SDK + PMax)
```

### Patrón de ejecución paralela
```python
results = await asyncio.gather(
    meta_client.create_campaign(request, meta_image_ids),
    tiktok_client.create_campaign(request, tiktok_image_ids),
    google_client.create_campaign(request, []),
    return_exceptions=True  # mismo patrón que Research Agent
)
```

### Integración con Research Agent
- CampaignAgent se dispara después del Research Agent (Celery chain)
- Recibe el `top_products[0]` del shortlist como input
- Reutiliza `LLMAnalyst.suggest_ad_copy()` para generar copy por plataforma
- Guarda `external_id` (campaign ID de cada plataforma) en tabla `campaigns`

---

## 6. Scheduling

| Tarea | Hora COT | Descripción |
|-------|----------|-------------|
| `run_daily_research` | 06:00 | Research Agent (ya implementado) |
| `run_campaign_creation` | 09:00 | Campaign Agent — lanza campañas del top producto |
| `run_dropi_sync` | Cada 2h | Sync catálogo (ya implementado) |
