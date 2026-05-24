# ROADMAP — Dropi Autonomous Sales Machine

## Versión 1.0 — MVP Autónomo

---

### Fase 1: Fundación e Infraestructura
**Objetivo:** Base sólida del proyecto — estructura, configuración, modelos de datos, y conexiones básicas.

**Entregables:**
- Estructura de proyecto Python con FastAPI
- Modelos de base de datos (PostgreSQL): Product, Campaign, Order, AgentLog, Metric
- Sistema de configuración con `.env` y validación con Pydantic Settings
- Docker Compose para desarrollo local (app + PostgreSQL + Redis)
- Script de migraciones con Alembic
- Logger estructurado (JSON) con niveles configurables
- Pruebas de conexión a todas las APIs externas (health checks)
- CI básico (GitHub Actions) — lint + type check

**Criterios de aceptación:**
- `docker compose up` levanta el stack completo sin errores
- FastAPI responde en `localhost:8000/health`
- Conexión a PostgreSQL y Redis verificada
- Variables de entorno documentadas en `.env.example`

**Estimación:** 2–3 días

---

### Fase 2: Agente Dropi (Playwright)
**Objetivo:** Automatizar completamente la interacción con el panel de Dropi.

**Entregables:**
- Módulo `agents/dropi_agent.py` con clase `DropiAgent`
- Login automático y manejo de sesión persistente
- Scraping del catálogo completo de productos disponibles
- Publicación/activación de productos seleccionados
- Monitoreo de órdenes entrantes con polling
- Detección de productos sin stock → auto-pausa
- Almacenamiento de catálogo en PostgreSQL
- Tests con Playwright en modo headless

**Criterios de aceptación:**
- Login exitoso y scraping del catálogo sin intervención manual
- Publicación de al menos 1 producto end-to-end verificada
- Productos sin stock se marcan como `inactive` automáticamente

**Estimación:** 3–4 días

---

### Fase 3: Agente de Investigación de Mercado
**Objetivo:** Identificar automáticamente los productos con mayor potencial de venta.

**Entregables:**
- Módulo `agents/research_agent.py`
- Scrapers para tendencias:
  - Google Trends (pytrends)
  - Amazon Best Sellers (scraping)
  - TikTok hashtags trending (API o scraping)
- Algoritmo de scoring de productos (0–100) basado en:
  - Volumen de búsqueda
  - Nivel de competencia
  - Margen estimado
  - Tendencia (creciente/estable/declinando)
- Cruce con catálogo de Dropi → filtrar productos disponibles
- Generación de shortlist TOP 10 con justificación vía Claude API
- Scheduler Celery: ejecución diaria 06:00 COT

**Criterios de aceptación:**
- Genera shortlist de 10 productos con scores en < 30 minutos
- Justificación legible generada por Claude para cada producto
- Resultados persistidos en DB y consultables via API

**Estimación:** 3–4 días

---

### Fase 4: Agente de Campañas Publicitarias
**Objetivo:** Crear y publicar campañas en las 3 plataformas de ads automáticamente.

**Entregables:**
- Módulo `agents/campaign_agent.py`
- Integración Meta Graph API:
  - Crear campaña, ad set, ad (estructura completa)
  - Generación de copy con Claude API (título, descripción, CTA)
  - Upload de imágenes del producto
  - Targeting automático por categoría de producto
- Integración TikTok Marketing API:
  - Campaña con objetivo de conversión
  - Creativos adaptados a formato vertical
- Integración Google Ads API:
  - Google Shopping con feed automático
  - Responsive Search Ads con headlines/descriptions generados por IA
- Lógica de distribución de presupuesto inicial por plataforma
- Almacenamiento de IDs de campaña en DB para tracking

**Criterios de aceptación:**
- Campaña creada en las 3 plataformas para al menos 1 producto end-to-end
- Copy generado por Claude, no hardcodeado
- Presupuesto inicial configurado y activo

**Estimación:** 5–7 días

---

### Fase 5: Agente de Analítica y Optimización
**Objetivo:** Recolectar métricas y tomar decisiones de optimización autónomas.

**Entregables:**
- Módulo `agents/analytics_agent.py`
- Recolección diaria de métricas desde las 3 plataformas
- Cálculo de ROAS, CTR, CPC, CPM, CAC por campaña
- Motor de reglas de optimización:
  - Pausa automática si ROAS < 1.5 tras 3 días
  - Escala presupuesto +20% si ROAS > 3.0
  - Rota creativos si CTR < 0.8%
- Decisiones registradas en `AgentLog` con razonamiento
- Reporte semanal generado en Markdown con Claude
- Alertas Telegram ante anomalías

**Criterios de aceptación:**
- Métricas recolectadas y almacenadas diariamente
- Al menos 1 decisión de optimización ejecutada automáticamente y verificada
- Reporte semanal generado correctamente

**Estimación:** 4–5 días

---

### Fase 6: Orquestador y Autonomía Completa
**Objetivo:** Conectar todos los agentes en un flujo autónomo y resiliente.

**Entregables:**
- Orquestador `agents/orchestrator.py` con LangGraph
- Grafo de estados: Research → Dropi → Campaigns → Analytics → loop
- Manejo de errores y reintentos (exponential backoff)
- Estado compartido entre agentes via Redis
- Scheduling completo con Celery Beat:
  - 06:00 COT: Research
  - 08:00 COT: Analytics collect
  - 09:00 COT: Optimización
  - Cada 2h: Monitor órdenes
- Sistema de auto-recovery: restart de agentes caídos
- Tests de integración del flujo completo

**Criterios de aceptación:**
- Ciclo completo Research → Publicación → Campaña ejecutado sin intervención
- Sistema se recupera de fallos de red y errores de API automáticamente
- Logs muestran cada decisión del orquestador

**Estimación:** 4–5 días

---

### Fase 7: Dashboard y Monitoreo
**Objetivo:** Panel web para visibilidad total del sistema sin necesidad de revisar logs.

**Entregables:**
- Dashboard FastAPI + Jinja2 (o HTMX) con:
  - Métricas globales: gasto total, ingresos, ROAS promedio
  - Tabla de productos activos con performance por campaña
  - Estado en tiempo real de cada agente (verde/amarillo/rojo)
  - Log de decisiones del orquestador (últimas 50)
  - Gráfico de gastos vs ingresos (semana/mes)
- Endpoint `/api/v1/status` para health monitoring externo
- Autenticación básica (usuario/contraseña) para proteger el dashboard

**Criterios de aceptación:**
- Dashboard accesible y funcional en producción
- Todos los widgets muestran datos reales, no mocks
- Autenticación requerida para acceder

**Estimación:** 3–4 días

---

## Resumen de fases

| Fase | Nombre | Estimación | Estado |
|------|--------|-----------|--------|
| 1 | Fundación e Infraestructura | 2–3 días | `pending` |
| 2 | Agente Dropi (Playwright) | 3–4 días | `pending` |
| 3 | Agente de Investigación de Mercado | 3–4 días | `pending` |
| 4 | Agente de Campañas Publicitarias | 5–7 días | `pending` |
| 5 | Agente de Analítica y Optimización | 4–5 días | `pending` |
| 6 | Orquestador y Autonomía Completa | 4–5 días | `pending` |
| 7 | Dashboard y Monitoreo | 3–4 días | `pending` |

**Total estimado: 24–32 días de desarrollo**

---

## Próximo paso
Ejecuta `/gsd:plan-phase 1` para generar el plan detallado de la Fase 1.
