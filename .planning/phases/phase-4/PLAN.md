# Plan — Fase 4: Campaign Agent

**Fase:** 4  
**Objetivo:** Crear campañas publicitarias automáticamente en Meta, TikTok y Google Ads usando Claude para el copy  
**Estimación:** 5–7 días  
**Dependencias de fase:** Fase 3 completada (Research Agent + LLMAnalyst.suggest_ad_copy disponible)

---

## Wave 1 — Modelos y Capa Base

### T4.1 — Crear `agents/campaign/models.py`

**Archivo:** `agents/campaign/models.py`

```python
from decimal import Decimal
from pydantic import BaseModel, Field


class AdCopy(BaseModel):
    platform: str
    headline: str
    body: str
    cta: str


class CampaignRequest(BaseModel):
    product_id: str
    dropi_id: str
    product_name: str
    product_url: str          # URL del producto en la tienda Dropi del vendedor
    image_urls: list[str]
    price_sell: Decimal
    category: str
    daily_budget_usd: float = Field(default=10.0, ge=1.0, le=50.0)
    ad_copies: dict[str, AdCopy] = Field(default_factory=dict)


class PlatformCampaignResult(BaseModel):
    platform: str             # "meta" | "tiktok" | "google"
    success: bool
    campaign_id: str | None = None
    adset_id: str | None = None
    ad_id: str | None = None
    error: str | None = None
    skipped: bool = False     # True si la plataforma no está configurada


class CampaignResult(BaseModel):
    product_id: str
    results: list[PlatformCampaignResult]

    @property
    def successful_platforms(self) -> list[str]:
        return [r.platform for r in self.results if r.success]

    @property
    def failed_platforms(self) -> list[str]:
        return [r.platform for r in self.results if not r.success and not r.skipped]
```

**Criterio:** `from agents.campaign.models import CampaignRequest` importa sin error.

---

### T4.2 — Crear `agents/campaign/platforms/base.py`

**Archivo:** `agents/campaign/platforms/base.py`

```python
from abc import ABC, abstractmethod
from agents.campaign.models import CampaignRequest, PlatformCampaignResult


class AbstractAdsPlatform(ABC):

    @abstractmethod
    async def upload_image(self, image_bytes: bytes, filename: str) -> str:
        """Sube imagen a la plataforma. Retorna el ID/hash específico de la plataforma."""
        ...

    @abstractmethod
    async def create_campaign(
        self, request: CampaignRequest, uploaded_image_ids: list[str]
    ) -> PlatformCampaignResult:
        """Crea campaña completa (campaign + adset/adgroup + ad). Retorna resultado."""
        ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass
```

**Criterio:** Importa sin error. Otras plataformas lo heredan.

---

### T4.3 — Crear `agents/campaign/image_handler.py`

**Archivo:** `agents/campaign/image_handler.py`

Descarga imágenes de URLs (productos de Dropi) y retorna bytes para subir a cada plataforma.

```python
import httpx
from app.logger import get_logger

log = get_logger("campaign.image_handler")

MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB — límite seguro para Meta y TikTok
SUPPORTED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


class ImageHandler:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self):
        if self._owns_client:
            self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        return self

    async def __aexit__(self, *_):
        if self._owns_client and self._client:
            await self._client.aclose()

    async def download(self, url: str) -> tuple[bytes, str]:
        """Descarga imagen. Retorna (bytes, filename)."""
        response = await self._client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(
            content_type, "jpg"
        )
        image_bytes = response.content
        if len(image_bytes) > MAX_IMAGE_BYTES:
            log.warning("Imagen muy grande, puede fallar el upload", url=url, size=len(image_bytes))
        return image_bytes, f"product.{ext}"

    async def download_first_valid(self, urls: list[str]) -> tuple[bytes, str] | None:
        """Intenta descargar URLs en orden hasta conseguir una válida."""
        for url in urls:
            try:
                return await self.download(url)
            except Exception as exc:
                log.warning("Fallo descarga de imagen", url=url, error=str(exc))
        return None
```

**Criterio:** `ImageHandler` descarga bytes de una URL de imagen real.

---

## Wave 2 — Clientes de Plataformas (paralelo)

### T4.4 — Crear `agents/campaign/platforms/meta.py`

**Archivo:** `agents/campaign/platforms/meta.py`  
**API:** Meta Graph API v21.0 — httpx  

Implementar `MetaAdsClient(AbstractAdsPlatform)` con:

1. **`upload_image(bytes, filename) → image_hash`**
   - `POST https://graph.facebook.com/v21.0/act_{ad_account_id}/adimages` (multipart)
   - Response: `{"images": {"filename": {"hash": "abc123"}}}`

2. **`_create_campaign(request) → campaign_id`**
   - `POST https://graph.facebook.com/v21.0/act_{ad_account_id}/campaigns`
   - Payload: `{"name": ..., "objective": "OUTCOME_TRAFFIC", "status": "PAUSED", "special_ad_categories": []}`

3. **`_create_adset(campaign_id, request) → adset_id`**
   - `POST https://graph.facebook.com/v21.0/act_{ad_account_id}/adsets`
   - `daily_budget` en centavos: `int(request.daily_budget_usd * 100)`
   - Targeting: Colombia (`{"geo_locations": {"countries": ["CO"]}}`)

4. **`_create_ad_creative(image_hash, request, copy) → creative_id`**
   - `POST https://graph.facebook.com/v21.0/act_{ad_account_id}/adcreatives`
   - Usar `copy.headline` como `name`, `copy.body` como `message`, `copy.cta` mapeado a `call_to_action.type`

5. **`_create_ad(adset_id, creative_id, request) → ad_id`**
   - `POST https://graph.facebook.com/v21.0/act_{ad_account_id}/ads`

6. **`create_campaign(request, image_ids) → PlatformCampaignResult`**
   - Orquesta los 4 pasos anteriores
   - `try/except` → retorna `PlatformCampaignResult(platform="meta", success=False, error=str(exc))`

```python
class MetaAdsClient(AbstractAdsPlatform):
    BASE_URL = "https://graph.facebook.com/v21.0"

    def __init__(self, access_token: str, ad_account_id: str, page_id: str):
        self._token = access_token
        self._account = ad_account_id  # con prefijo "act_"
        self._page_id = page_id
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()
```

**Criterio:** `MetaAdsClient` instancia sin error. Métodos tienen firma correcta.

---

### T4.5 — Crear `agents/campaign/platforms/tiktok.py`

**Archivo:** `agents/campaign/platforms/tiktok.py`  
**API:** TikTok Marketing API v1.3 — httpx  

Implementar `TikTokAdsClient(AbstractAdsPlatform)` con:

1. **`upload_image(bytes, filename) → image_id`**
   - `POST https://business-api.tiktok.com/open_api/v1.3/file/image/ad/upload/` (multipart, campo: `image_file`)
   - Response: `{"data": {"image_id": "...", "image_url": "..."}}`

2. **`_create_campaign(request) → campaign_id`**
   - `POST https://business-api.tiktok.com/open_api/v1.3/campaign/create/`
   - `{"advertiser_id": ..., "campaign_name": ..., "objective_type": "TRAFFIC", "budget_mode": "BUDGET_MODE_DAY", "budget": daily_budget_usd}`

3. **`_create_adgroup(campaign_id, request) → adgroup_id`**
   - `POST https://business-api.tiktok.com/open_api/v1.3/adgroup/create/`
   - Colombia location_ids: `["6252001"]`
   - `"schedule_type": "SCHEDULE_FROM_NOW"`, `"bid_type": "BID_TYPE_NO_BID"`

4. **`_create_ad(adgroup_id, image_id, request, copy) → ad_id`**
   - `POST https://business-api.tiktok.com/open_api/v1.3/ad/create/`
   - `"ad_format": "SINGLE_IMAGE"`, `"ad_text": copy.body`, `"landing_page_url": request.product_url`

5. **`create_campaign(request, image_ids) → PlatformCampaignResult`**

```python
class TikTokAdsClient(AbstractAdsPlatform):
    BASE_URL = "https://business-api.tiktok.com/open_api/v1.3"

    def __init__(self, access_token: str, advertiser_id: str):
        self._token = access_token
        self._advertiser_id = advertiser_id
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            headers={"Access-Token": self._token},
            timeout=30.0,
        )
        return self
```

**Criterio:** `TikTokAdsClient` instancia sin error. Métodos con firma correcta.

---

### T4.6 — Crear `agents/campaign/platforms/google_ads.py`

**Archivo:** `agents/campaign/platforms/google_ads.py`  
**SDK:** `google-ads` Python library (Performance Max)  

Implementar `GoogleAdsClient(AbstractAdsPlatform)` con:

1. **Construcción del cliente:**
   ```python
   from google.ads.googleads.client import GoogleAdsClient as _GAClient
   
   self._client = _GAClient.load_from_dict({
       "developer_token": developer_token,
       "client_id": client_id,
       "client_secret": client_secret,
       "refresh_token": refresh_token,
       "login_customer_id": customer_id,
       "use_proto_plus": True,
   })
   ```

2. **`upload_image(bytes, filename) → asset_resource_name`**
   - Usa `AssetService.mutate_assets()` con `ImageAsset`
   - Retorna resource name del asset

3. **`create_campaign(request, uploaded_image_ids) → PlatformCampaignResult`**
   - Si `customer_id` vacío → retorna `PlatformCampaignResult(platform="google", success=False, skipped=True)`
   - Crea: `CampaignBudget` → `Campaign(PERFORMANCE_MAX)` → `AssetGroup` con headlines/descriptions/images
   - Todo en una sola llamada `GoogleAdsService.mutate()`

4. **Manejo de errores:**
   - `GoogleAdsException` → extraer `error.message` y retornar `PlatformCampaignResult(success=False, error=...)`

**Criterio:** Si `customer_id` = `""`, retorna `skipped=True` sin lanzar excepción.

---

## Wave 3 — Agente Principal + Integración Celery

### T4.7 — Crear `agents/campaign/agent.py`

**Archivo:** `agents/campaign/agent.py`

```python
class CampaignAgent:
    def __init__(self, settings):
        self.settings = settings
        self.analyst = LLMAnalyst(settings)  # reutiliza de research phase
        self.log = get_logger("campaign_agent")

    async def run(self, db: AsyncSession, product: Product) -> CampaignResult:
        """
        Flujo:
        1. Generar ad copy con Claude para cada plataforma
        2. Descargar imagen del producto
        3. Subir imagen a cada plataforma en paralelo
        4. Crear campañas en paralelo (Meta + TikTok + Google)
        5. Guardar en DB (tabla campaigns)
        6. Registrar AgentLog
        """
```

**Detalle de implementación:**

```python
async def run(self, db: AsyncSession, product: Product) -> CampaignResult:
    # 1. Generar copies
    copies = await self._generate_copies(product)
    
    # 2. Preparar request
    request = CampaignRequest(
        product_id=str(product.id),
        dropi_id=product.dropi_id,
        product_name=product.name,
        product_url=f"https://app.dropi.co/productos/{product.dropi_id}",
        image_urls=product.images or [],
        price_sell=product.price_sell,
        category=product.category or "general",
        daily_budget_usd=self.settings.campaign_daily_budget_usd,
        ad_copies=copies,
    )
    
    # 3. Descargar imagen
    async with ImageHandler() as handler:
        image_data = await handler.download_first_valid(request.image_urls)
    
    # 4. Crear campañas en paralelo
    results = await self._launch_campaigns(request, image_data)
    
    # 5. Persistir en DB
    await self._save_to_db(db, results, product)
    
    # 6. AgentLog
    await self._persist_log(db, results)
    
    return CampaignResult(product_id=str(product.id), results=results)
```

**`_launch_campaigns` — patrón paralelo:**
```python
async def _launch_campaigns(self, request, image_data) -> list[PlatformCampaignResult]:
    tasks = []
    
    if self.settings.meta_access_token:
        tasks.append(self._run_meta(request, image_data))
    
    if self.settings.tiktok_access_token:
        tasks.append(self._run_tiktok(request, image_data))
    
    if self.settings.google_ads_customer_id:
        tasks.append(self._run_google(request))
    
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    # convertir exceptions a PlatformCampaignResult(success=False, error=...)
    return [r if isinstance(r, PlatformCampaignResult) else _exc_to_result(platform, r)
            for platform, r in zip(["meta", "tiktok", "google"], raw)]
```

**`_save_to_db`:** Crea registro en tabla `campaigns` por cada plataforma exitosa:
```python
for result in results:
    if result.success:
        db.add(Campaign(
            product_id=product.id,
            platform=result.platform,
            external_id=result.campaign_id,
            status="active",
            daily_budget_usd=request.daily_budget_usd,
        ))
await db.commit()
```

**Criterio:** `CampaignAgent.run(db, product)` retorna `CampaignResult` con lista de resultados por plataforma.

---

### T4.8 — Crear `agents/campaign/__init__.py`

```python
from agents.campaign.agent import CampaignAgent
from agents.campaign.models import CampaignRequest, CampaignResult

__all__ = ["CampaignAgent", "CampaignRequest", "CampaignResult"]
```

---

### T4.9 — Actualizar `app/config.py` — agregar `campaign_daily_budget_usd`

**Archivo:** `app/config.py`  
**Cambio:** Agregar campo + variable de entorno

```python
# En la clase Settings, agregar después de reddit_user_agent:
campaign_daily_budget_usd: float = Field(default=10.0, ge=1.0, le=50.0)
meta_page_id: str = ""  # Facebook Page ID requerido para crear creativos
```

**Criterio:** `settings.campaign_daily_budget_usd` devuelve `10.0` por defecto.

---

### T4.10 — Actualizar `app/tasks.py` — agregar `run_campaign_creation`

**Archivo:** `app/tasks.py`  
**Cambio:** Agregar tarea Celery para el Campaign Agent

```python
@celery_app.task(name="app.tasks.run_campaign_creation", bind=True, max_retries=2)
def run_campaign_creation(self):
    """Lanza campañas en Meta/TikTok/Google para el top producto del Research. Programado: 09:00 COT diario."""
    try:
        asyncio.run(_run_campaign_async())
    except Exception as exc:
        log.error("run_campaign_creation falló", error=str(exc))
        raise self.retry(exc=exc, countdown=300)


async def _run_campaign_async() -> None:
    from agents.campaign.agent import CampaignAgent
    from app.config import get_settings
    from app.database import AsyncSessionLocal
    from app.models import Product
    from sqlalchemy import select as sa_select

    settings = get_settings()
    agent = CampaignAgent(settings)

    async with AsyncSessionLocal() as db:
        # Obtener el producto con mejor score (más reciente, is_available=True)
        product = await db.scalar(
            sa_select(Product)
            .where(Product.is_available == True)
            .order_by(Product.updated_at.desc())
            .limit(1)
        )
        if not product:
            log.warning("No hay productos disponibles para crear campaña")
            return

        result = await agent.run(db, product)
        log.info(
            "Campaign Agent completado",
            product=product.name,
            platforms=result.successful_platforms,
        )
```

**Criterio:** `from app.tasks import run_campaign_creation` importa sin error.

---

### T4.11 — Actualizar `app/celeryconfig.py` — scheduling a las 09:00 COT

**Archivo:** `app/celeryconfig.py`  
**Cambio:** Agregar `run_campaign_creation` al beat schedule

```python
# Agregar a beat_schedule:
"campaign-creation-daily": {
    "task": "app.tasks.run_campaign_creation",
    "schedule": crontab(hour=9, minute=0),  # 09:00 COT = UTC-5 → 14:00 UTC
},
```

**Criterio:** `celery_app.conf.beat_schedule` incluye `"campaign-creation-daily"`.

---

### T4.12 — Actualizar `.env.example` — agregar `META_PAGE_ID` y `CAMPAIGN_DAILY_BUDGET_USD`

**Archivo:** `.env.example`  
**Cambio:** Agregar bajo sección Meta:

```bash
# REQUIRED | Facebook Page ID para crear creativos (obtener en: fb.com/{tu_pagina}/about)
META_PAGE_ID=1234567890123456

# OPTIONAL | Presupuesto diario por plataforma en USD (default: 10)
CAMPAIGN_DAILY_BUDGET_USD=10
```

---

## Wave 4 — Tests

### T4.13 — Crear `tests/test_campaign_agent.py`

**Archivo:** `tests/test_campaign_agent.py`  
Tests con mocks — sin llamadas reales a APIs.

**Tests a implementar:**

```python
# ── Models ──────────────────────────────────────────────────────────────────────

def test_campaign_request_budget_bounds():
    """Budget no puede ser negativo ni > 50."""

def test_campaign_result_successful_platforms():
    """successful_platforms filtra correctamente."""

def test_campaign_result_failed_platforms():
    """failed_platforms excluye skipped."""

# ── ImageHandler ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_image_handler_downloads_bytes():
    """download() retorna (bytes, filename) correctos."""

@pytest.mark.asyncio
@respx.mock
async def test_image_handler_skips_failed_urls():
    """download_first_valid() pasa URL rota y usa la siguiente."""

# ── MetaAdsClient ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_meta_client_create_campaign_success():
    """Mock de 4 endpoints Meta → retorna PlatformCampaignResult(success=True)."""
    # Mockear: /adimages, /campaigns, /adsets, /adcreatives, /ads

@pytest.mark.asyncio
@respx.mock
async def test_meta_client_returns_failure_on_api_error():
    """Si Meta API retorna 400, retorna PlatformCampaignResult(success=False)."""

# ── TikTokAdsClient ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_tiktok_client_create_campaign_success():
    """Mock de 4 endpoints TikTok → retorna PlatformCampaignResult(success=True)."""

# ── GoogleAdsClient ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_google_client_skips_when_no_customer_id():
    """Si customer_id='', retorna skipped=True sin llamar a la API."""

# ── CampaignAgent (integración con mocks) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_campaign_agent_runs_with_one_platform_failing(db_session):
    """Si Meta falla, TikTok sigue. CampaignResult tiene ambos resultados."""

@pytest.mark.asyncio
async def test_campaign_agent_persists_campaigns_to_db(db_session):
    """Campañas exitosas quedan en la tabla campaigns."""

@pytest.mark.asyncio
async def test_campaign_agent_persists_agent_log(db_session):
    """AgentLog con agent='campaign', action='create_campaigns' se persiste."""

@pytest.mark.asyncio
async def test_campaign_agent_skips_when_no_credentials(db_session):
    """Sin credenciales configuradas, agent.run() retorna result con todos skipped."""
```

**Criterio:** `pytest tests/test_campaign_agent.py` — todos los tests pasan (con mocks).

---

## Resumen de archivos a crear/modificar

| Acción | Archivo |
|--------|---------|
| CREAR | `agents/campaign/__init__.py` |
| CREAR | `agents/campaign/models.py` |
| CREAR | `agents/campaign/image_handler.py` |
| CREAR | `agents/campaign/platforms/__init__.py` |
| CREAR | `agents/campaign/platforms/base.py` |
| CREAR | `agents/campaign/platforms/meta.py` |
| CREAR | `agents/campaign/platforms/tiktok.py` |
| CREAR | `agents/campaign/platforms/google_ads.py` |
| CREAR | `agents/campaign/agent.py` |
| CREAR | `tests/test_campaign_agent.py` |
| MODIFICAR | `app/config.py` (+ 2 campos) |
| MODIFICAR | `app/tasks.py` (+ tarea + función async) |
| MODIFICAR | `app/celeryconfig.py` (+ entrada beat schedule) |
| MODIFICAR | `.env.example` (+ 2 variables) |

**Total archivos: 10 nuevos + 4 modificados**

---

## Criterios de aceptación de la fase

- [ ] `from agents.campaign import CampaignAgent` importa sin error
- [ ] `CampaignAgent.run(db, product)` retorna `CampaignResult` con resultados por plataforma
- [ ] Si una plataforma falla, las demás continúan (patrón `return_exceptions=True`)
- [ ] Si no hay credenciales configuradas para una plataforma, retorna `skipped=True` (no error)
- [ ] Campañas exitosas se persisten en tabla `campaigns` con `external_id`
- [ ] `AgentLog` se crea con `agent="campaign"`, `action="create_campaigns"`, `status="success"`
- [ ] `pytest tests/test_campaign_agent.py` pasa sin llamadas reales a APIs externas
- [ ] Tarea `run_campaign_creation` programada en Celery Beat a las 09:00 COT
