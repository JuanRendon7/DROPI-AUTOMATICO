# Research — Fase 2: Agente Dropi

## Descubrimiento clave: Dropi tiene API REST oficial

Dropi expone una API pública en `https://api.dropi.co/` con autenticación por `dropi-integration-key` (se genera desde el panel en *Configuración → Integraciones*).

**Impacto en el diseño:** La estrategia óptima es **API-first**, con Playwright como fallback para operaciones no disponibles via API.

---

## URLs confirmadas

| Recurso | URL |
|---|---|
| Login web | `https://app.dropi.co/auth/login` |
| Dashboard | `https://app.dropi.co/dashboard/` |
| Productos | `https://app.dropi.co/dashboard/products` |
| Órdenes | `https://app.dropi.co/dashboard/orders` |
| API base | `https://api.dropi.co/` |
| Frontend alt | `https://fe.dropi.co/` |

## Autenticación

- **Panel web:** Email + contraseña (formulario estándar, sin OAuth)
- **API REST:** Header `dropi-integration-key: <key>` (se genera en el panel)
- Playwright puede hacer login web y extraer cookies para reutilizarlas

## Capacidades de la API

Documentadas (parcialmente confirmadas):
- Autenticación de usuario
- Creación de órdenes
- Generación de guías de envío
- Sincronización con Shopify (para nuestros fines: lectura de órdenes y estados)

**No confirmado via API:** Browsing del catálogo de productos disponibles para dropshipping — esta parte probablemente requiere Playwright.

## Estrategia de implementación

```
┌─────────────────────────────────────────────────┐
│              DropiAgent (Fase 2)                │
├────────────────────┬────────────────────────────┤
│   DropiAPIClient   │   DropiPlaywrightClient    │
│   (httpx)          │   (Playwright async)       │
├────────────────────┼────────────────────────────┤
│ • Crear órdenes    │ • Explorar catálogo         │
│ • Leer órdenes     │ • Activar/publicar producto │
│ • Guías de envío   │ • Scraping de precios       │
│ • Estado de orden  │ • Login con sesión persiste │
└────────────────────┴────────────────────────────┘
```

## Consideraciones anti-detección (Playwright)

- User-Agent realista (usar `playwright.chromium.launch` con perfil)
- Delays aleatorios entre acciones (100ms–800ms)
- `storage_state` de Playwright para persistir sesión (cookies + localStorage) → evita login en cada ejecución
- Modo headless en producción, headed para debug
- No hacer demasiadas peticiones en paralelo — throttle a 1 acción por vez

## Modelo de datos de producto (Dropi catalog)

Campos esperados para extraer de la UI:
```
dropi_id: str         # ID interno de Dropi
name: str             # Nombre del producto
description: str      # Descripción
price_buy: Decimal    # Precio de compra (lo que pagas tú)
price_sell: Decimal   # Precio de venta sugerido
images: list[str]     # URLs de imágenes
category: str         # Categoría (ropa, tecnología, hogar, etc.)
stock: int            # Unidades disponibles
```

## Decisión de diseño

Crear dos clases separadas y un `DropiAgent` que las orquesta:
1. `DropiAPIClient` — httpx async, maneja la API REST
2. `DropiBrowserClient` — Playwright async, maneja la UI
3. `DropiAgent` — orquestador de alto nivel (lo que llama el Orchestrator)
