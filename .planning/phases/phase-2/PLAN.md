# Plan — Fase 2: Agente Dropi (Playwright + API)

**Fase:** 2 de 7
**Estado:** `pending`
**Estimación:** 3–4 días
**Objetivo:** Automatizar completamente la interacción con Dropi usando una estrategia híbrida: API REST para operaciones transaccionales (órdenes) y Playwright para scraping del catálogo y publicación de productos.

**Descubrimiento de investigación:** Dropi tiene API REST oficial en `api.dropi.co` con autenticación por `dropi-integration-key`. La arquitectura combina ambos métodos.

---

## Estructura de archivos objetivo

```
agents/
├── dropi/
│   ├── __init__.py
│   ├── agent.py            # DropiAgent — orquestador
│   ├── api_client.py       # DropiAPIClient — httpx async
│   ├── browser_client.py   # DropiBrowserClient — Playwright
│   ├── models.py           # Pydantic schemas (DropiProduct, DropiOrder)
│   └── exceptions.py       # Errores específicos de Dropi
└── __init__.py             # ya existe

tests/
└── test_dropi_agent.py     # tests del agente
```

---

## Tareas

### TASK-2-01: Modelos Pydantic del dominio Dropi
**Archivo:** `agents/dropi/models.py`

Definir los schemas de datos que circulan dentro del agente — distintos de los modelos SQLAlchemy de la DB.

```python
class DropiProductRaw(BaseModel):
    dropi_id: str
    name: str
    description: str = ""
    price_buy: Decimal
    price_sell: Decimal
    stock: int
    category: str
    images: list[str] = []
    is_available: bool = True

class DropiOrderRaw(BaseModel):
    dropi_order_id: str
    product_dropi_id: str
    status: str  # pending | confirmed | shipped | delivered | cancelled
    customer_name: str = ""
    created_at: datetime

class DropiCatalogPage(BaseModel):
    products: list[DropiProductRaw]
    page: int
    has_next: bool

class DropiSessionState(BaseModel):
    integration_key: str | None = None
    cookies_path: str | None = None
    last_login: datetime | None = None
    is_valid: bool = False
```

**Criterio:** Todos los modelos serializan/deserializan correctamente con `.model_validate()`.

---

### TASK-2-02: Cliente API REST (`DropiAPIClient`)
**Archivo:** `agents/dropi/api_client.py`

Cliente httpx async para la API oficial de Dropi (`api.dropi.co`).

```python
class DropiAPIClient:
    BASE_URL = "https://api.dropi.co"

    async def get_orders(
        self, status: str | None = None, page: int = 1
    ) -> list[DropiOrderRaw]: ...

    async def get_order(self, order_id: str) -> DropiOrderRaw: ...

    async def confirm_order(self, order_id: str) -> bool: ...

    async def get_shipping_guide(self, order_id: str) -> str: ...  # URL del PDF
```

**Headers requeridos:**
```
dropi-integration-key: {settings.dropi_integration_key}
Content-Type: application/json
```

**Manejo de errores:**
- 401 → `DropiAuthError` (integration key inválida)
- 429 → `RateLimitError` con `retry_after`
- 5xx → `DropiAPIError` con reintento automático (tenacity, max 3)

**Criterio:** `get_orders()` retorna lista de `DropiOrderRaw` o levanta excepción tipada. Todos los métodos tienen timeout de 30s.

---

### TASK-2-03: Cliente Playwright (`DropiBrowserClient`)
**Archivo:** `agents/dropi/browser_client.py`

Gestiona el navegador headless para operaciones que la API no cubre (catálogo y publicación).

```python
class DropiBrowserClient:
    LOGIN_URL = "https://app.dropi.co/auth/login"
    CATALOG_URL = "https://app.dropi.co/dashboard/products"
    SESSION_FILE = "playwright_state/dropi_session.json"

    async def __aenter__(self) -> "DropiBrowserClient": ...
    async def __aexit__(self, *args) -> None: ...

    async def login(self, email: str, password: str) -> bool:
        # 1. Navegar a LOGIN_URL
        # 2. Esperar selector input[type=email] y input[type=password]
        # 3. Fill con delays aleatorios (100–500ms entre chars)
        # 4. Click en submit
        # 5. Esperar navegación a /dashboard/
        # 6. Guardar session_state a SESSION_FILE
        ...

    async def load_session(self) -> bool:
        # Cargar SESSION_FILE si existe y es válido (<8 horas)
        ...

    async def scrape_catalog_page(self, page: int = 1) -> DropiCatalogPage:
        # Navegar a CATALOG_URL?page=N
        # Esperar que carguen las tarjetas de productos
        # Extraer: dropi_id, name, price_buy, price_sell, stock, images
        # Detectar si hay página siguiente
        ...

    async def scrape_full_catalog(self) -> list[DropiProductRaw]:
        # Iterar páginas hasta has_next=False
        # Delay de 1–3s entre páginas (anti-detección)
        ...

    async def activate_product(self, dropi_id: str) -> bool:
        # Navegar a la página del producto
        # Click en botón "Publicar" o "Activar"
        # Verificar que el estado cambia a activo
        ...

    async def get_order_status_from_ui(self, order_id: str) -> str:
        # Fallback si la API no retorna el estado correcto
        ...
```

**Anti-detección implementada:**
- `launch(headless=True)` en producción, `headless=False` si `ENVIRONMENT=development`
- `random.uniform(0.1, 0.8)` delay entre acciones sensibles
- User-Agent de Chrome real (no el default de Playwright)
- Guardar y reutilizar `storage_state` para evitar login repetitivo

**Criterio:** Login exitoso y scraping del catálogo (al menos 1 página) sin errores en modo headless.

---

### TASK-2-04: Servicio de sincronización de catálogo
**Archivo:** `agents/dropi/agent.py` (método `sync_catalog`)

Combina `DropiBrowserClient.scrape_full_catalog()` con la DB para mantener el catálogo actualizado.

```python
async def sync_catalog(self, db: AsyncSession) -> dict:
    """
    Scrape el catálogo de Dropi y actualiza la tabla products.
    Retorna: {"new": N, "updated": N, "deactivated": N}
    """
    raw_products = await self.browser.scrape_full_catalog()

    new_count = updated_count = deactivated_count = 0

    for raw in raw_products:
        existing = await db.scalar(
            select(Product).where(Product.dropi_id == raw.dropi_id)
        )
        if existing is None:
            db.add(Product(**raw.to_db_dict()))
            new_count += 1
        else:
            # Actualizar precio y stock
            existing.price_buy = raw.price_buy
            existing.price_sell = raw.price_sell
            existing.stock = raw.stock
            existing.status = "active" if raw.is_available else "inactive"
            updated_count += 1

    # Productos en DB que ya no están en Dropi → deactivate
    dropi_ids_in_dropi = {p.dropi_id for p in raw_products}
    result = await db.execute(select(Product).where(Product.status == "active"))
    for product in result.scalars():
        if product.dropi_id not in dropi_ids_in_dropi:
            product.status = "inactive"
            deactivated_count += 1

    await db.commit()
    return {"new": new_count, "updated": updated_count, "deactivated": deactivated_count}
```

**Criterio:** `sync_catalog()` persiste los productos correctamente y el conteo de new/updated/deactivated es correcto.

---

### TASK-2-05: Sincronización de órdenes
**Archivo:** `agents/dropi/agent.py` (método `sync_orders`)

Usa la API REST para mantener las órdenes actualizadas en la DB.

```python
async def sync_orders(self, db: AsyncSession) -> dict:
    """
    Obtiene órdenes nuevas/actualizadas desde la API de Dropi.
    Retorna: {"new": N, "updated": N}
    """
    raw_orders = await self.api_client.get_orders()

    new_count = updated_count = 0
    for raw in raw_orders:
        existing = await db.scalar(
            select(Order).where(Order.dropi_order_id == raw.dropi_order_id)
        )
        product = await db.scalar(
            select(Product).where(Product.dropi_id == raw.product_dropi_id)
        )
        if existing is None and product:
            db.add(Order(
                dropi_order_id=raw.dropi_order_id,
                product_id=product.id,
                status=raw.status,
            ))
            new_count += 1
        elif existing and existing.status != raw.status:
            existing.status = raw.status
            updated_count += 1

    await db.commit()
    return {"new": new_count, "updated": updated_count}
```

**Criterio:** Órdenes se crean y actualizan en DB. Productos sin stock detectados → `status = "inactive"`.

---

### TASK-2-06: Auto-pausa de productos sin stock
**Archivo:** `agents/dropi/agent.py` (método `check_and_pause_out_of_stock`)

```python
async def check_and_pause_out_of_stock(self, db: AsyncSession) -> list[str]:
    """
    Detecta productos activos con stock=0 y los pausa en Dropi.
    Retorna lista de dropi_ids pausados.
    """
    result = await db.execute(
        select(Product).where(Product.status == "active", Product.stock == 0)
    )
    paused = []
    for product in result.scalars():
        success = await self.browser.deactivate_product(product.dropi_id)
        if success:
            product.status = "inactive"
            paused.append(product.dropi_id)

    await db.commit()
    return paused
```

**Criterio:** Productos con stock 0 se marcan `inactive` en DB y se pausan en el panel de Dropi.

---

### TASK-2-07: Activación de productos seleccionados
**Archivo:** `agents/dropi/agent.py` (método `activate_products`)

```python
async def activate_products(
    self, dropi_ids: list[str], db: AsyncSession
) -> dict:
    """
    Activa en Dropi los productos del shortlist del Research Agent.
    Retorna: {"activated": list[str], "failed": list[str]}
    """
```

**Criterio:** Al menos 1 producto activado end-to-end (visible en el panel de Dropi).

---

### TASK-2-08: Clase orquestadora `DropiAgent`
**Archivo:** `agents/dropi/agent.py`

```python
class DropiAgent:
    def __init__(self, settings: Settings):
        self.api_client = DropiAPIClient(settings)
        self.browser = DropiBrowserClient(settings)
        self.log = get_logger("dropi")

    async def run_full_sync(self, db: AsyncSession) -> dict:
        """
        Ciclo completo: login → sync_catalog → sync_orders → check_stock
        Usado por el Orchestrator.
        """
        await self.browser.load_or_login()
        catalog_result = await self.sync_catalog(db)
        orders_result = await self.sync_orders(db)
        paused = await self.check_and_pause_out_of_stock(db)

        # Log a AgentLog
        await self._log_action(db, "run_full_sync", {
            "catalog": catalog_result,
            "orders": orders_result,
            "paused": paused,
        })
        return {"catalog": catalog_result, "orders": orders_result, "paused_products": paused}

    async def _log_action(self, db: AsyncSession, action: str, metadata: dict) -> None:
        db.add(AgentLog(agent="dropi", action=action, meta=metadata))
        await db.commit()
```

**Criterio:** `agent.run_full_sync(db)` ejecuta el ciclo completo y persiste un `AgentLog`.

---

### TASK-2-09: Actualizar `config.py` con variables de Dropi API
**Archivo:** `app/config.py`

Agregar:
```python
dropi_integration_key: str = ""  # OPTIONAL hasta que el usuario la genere
```

Y actualizar `.env.example` con la nueva variable.

---

### TASK-2-10: Tests del Agente Dropi
**Archivo:** `tests/test_dropi_agent.py`

Tests usando mocks de Playwright y httpx — no dependen de Dropi real:

```python
# test_api_client_handles_rate_limit
# test_api_client_retries_on_5xx
# test_sync_catalog_creates_new_products
# test_sync_catalog_updates_existing
# test_sync_orders_creates_new_orders
# test_sync_orders_updates_status
# test_check_and_pause_sets_inactive
```

Usar `respx` para mockear httpx y `pytest-mock` para Playwright.

**Criterio:** Todos los tests pasan sin necesitar conexión a Dropi real.

---

## Orden de ejecución

```
1. TASK-2-01  →  models.py (Pydantic)
2. TASK-2-09  →  config.py actualizado
3. TASK-2-02  →  api_client.py
4. TASK-2-03  →  browser_client.py
5. TASK-2-04  →  agent.py sync_catalog
6. TASK-2-05  →  agent.py sync_orders
7. TASK-2-06  →  agent.py check_and_pause_out_of_stock
8. TASK-2-07  →  agent.py activate_products
9. TASK-2-08  →  agent.py DropiAgent (orquestador)
10. TASK-2-10 →  tests/test_dropi_agent.py
```

---

## Dependencias nuevas

Agregar a `pyproject.toml` en `[project.optional-dependencies]` → `agents`:
```
"playwright>=1.44",
"respx>=0.21",     # mock de httpx para tests
"pytest-mock>=3.14",
```

Y ejecutar: `playwright install chromium`

---

## Criterios de aceptación (de ROADMAP.md)

- [ ] Login automático exitoso en `app.dropi.co` sin intervención manual
- [ ] `sync_catalog()` scrapea y persiste el catálogo completo en DB
- [ ] Activación de al menos 1 producto end-to-end verificada en el panel
- [ ] Productos con stock=0 se marcan como `inactive` automáticamente
- [ ] `sync_orders()` sincroniza órdenes desde la API REST
- [ ] Todos los tests pasan: `pytest tests/test_dropi_agent.py`

---

## Notas de implementación

- La sesión de Playwright se guarda en `playwright_state/dropi_session.json` — este directorio está en `.gitignore`
- La sesión dura ~8 horas; `load_or_login()` comprueba la antigüedad antes de decidir si hacer login
- En CI, los tests de Playwright usan mocks — Playwright real solo en integración manual
- El `integration_key` de la API se obtiene manualmente desde el panel de Dropi (una sola vez) y se pone en `.env`
- Si la API no tiene endpoint para catálogo, el 100% del catálogo viene de Playwright
