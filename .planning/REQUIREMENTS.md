# Requisitos — Dropi Autonomous Sales Machine

## Resumen ejecutivo
Sistema multi-agente autónomo que opera el ciclo completo de dropshipping: investigación de productos → publicación en Dropi → lanzamiento de campañas de ads → análisis → optimización, sin intervención humana.

---

## RF-01: Agente de Investigación de Mercado (Research Agent)
- **RF-01.1** Rastrear tendencias de productos en múltiples fuentes (Amazon Best Sellers, AliExpress, Google Trends, TikTok trending)
- **RF-01.2** Puntuar productos según criterios: volumen de búsqueda, competencia, margen estimado, tendencia de crecimiento
- **RF-01.3** Filtrar productos ya listados en Dropi del catálogo disponible
- **RF-01.4** Generar un shortlist diario de los TOP 10 productos recomendados con justificación
- **RF-01.5** Ejecutarse en ciclo automático cada 24 horas

## RF-02: Agente Dropi (Dropi Agent)
- **RF-02.1** Automatizar login en el panel de Dropi vía Playwright
- **RF-02.2** Scraping del catálogo de productos disponibles (nombre, precio, imágenes, descripción, stock)
- **RF-02.3** Publicar/activar productos seleccionados por el Research Agent
- **RF-02.4** Monitorear órdenes entrantes y actualizar estado
- **RF-02.5** Detectar productos sin stock y pausarlos automáticamente
- **RF-02.6** Extraer precios de compra para calcular márgenes reales

## RF-03: Agente de Campañas (Campaign Agent)
- **RF-03.1** Crear campañas en Meta Ads (Facebook + Instagram) vía Graph API
  - RF-03.1.1 Generar copy de anuncios con Claude API
  - RF-03.1.2 Seleccionar audiencias target basadas en el producto
  - RF-03.1.3 Configurar presupuesto inicial conservador ($5–15/día)
- **RF-03.2** Crear campañas en TikTok Ads vía Marketing API
  - RF-03.2.1 Adaptar creativos al formato vertical / short-video
  - RF-03.2.2 Targeting por intereses y comportamiento
- **RF-03.3** Crear campañas en Google Ads vía API
  - RF-03.3.1 Google Shopping con feed de productos
  - RF-03.3.2 Search ads con keywords generadas por IA
- **RF-03.4** Asignar presupuesto inicial por plataforma según scoring del producto

## RF-04: Agente de Analítica y Optimización (Analytics Agent)
- **RF-04.1** Recolectar métricas diarias: impresiones, clics, conversiones, gasto, ingresos, ROAS
- **RF-04.2** Calcular ROAS por campaña, conjunto de anuncios y anuncio individual
- **RF-04.3** Tomar decisiones autónomas:
  - Pausar campañas con ROAS < 1.5 después de 3 días
  - Escalar presupuesto en 20% para campañas con ROAS > 3.0
  - Rotar creativos de anuncios con CTR < 0.8%
- **RF-04.4** Generar reporte semanal de performance (PDF/Markdown)
- **RF-04.5** Detectar anomalías de gasto (spike inesperado > 2x presupuesto diario)

## RF-05: Orquestador (Orchestrator)
- **RF-05.1** Coordinar el flujo completo: Research → Dropi → Campaigns → Analytics
- **RF-05.2** Gestionar el estado compartido entre agentes vía Redis
- **RF-05.3** Manejar errores y reintentos automáticos (max 3 reintentos con backoff)
- **RF-05.4** Scheduling de tareas:
  - Research: diario a las 06:00 (hora Colombia)
  - Analytics: diario a las 08:00
  - Optimización: diario a las 09:00
  - Monitor de órdenes: cada 2 horas
- **RF-05.5** Sistema de alertas por Telegram/email ante fallos críticos

## RF-06: Dashboard de Monitoreo
- **RF-06.1** Panel web FastAPI con métricas en tiempo real
- **RF-06.2** Tabla de productos activos con ROAS por campaña
- **RF-06.3** Log de decisiones del orquestador (qué pausó, escalar, etc.)
- **RF-06.4** Estado de cada agente (activo, idle, error)
- **RF-06.5** Histórico de gastos vs ingresos por semana/mes

---

## Requisitos No Funcionales

### RNF-01: Costo
- Infraestructura total < $200 USD/mes (servidor + APIs + DB)
- Usar tiers gratuitos donde sea posible (Cloudflare, Supabase free tier, etc.)

### RNF-02: Autonomía
- El sistema debe operar 24/7 sin intervención humana
- Auto-recovery ante caídas de servicios externos

### RNF-03: Seguridad
- Credenciales en variables de entorno (nunca hardcodeadas)
- Rate limiting en todas las APIs para evitar baneos
- Rotación de User-Agent en Playwright

### RNF-04: Observabilidad
- Logs estructurados en JSON
- Registro de cada decisión del orquestador
- Métricas de uptime por agente

### RNF-05: Escalabilidad
- Arquitectura que permita agregar nuevas plataformas de ads sin refactoring mayor
- Soporte para múltiples cuentas de Dropi en el futuro

---

## Exclusiones (fuera de scope v1)
- App móvil o panel nativo (solo web dashboard básico)
- Integración con plataformas adicionales (Shopify, WooCommerce)
- Soporte multi-idioma del sistema (solo español)
- Facturación automática a clientes

---

## Dependencias Externas
| Servicio | Tipo de acceso | Notas |
|---|---|---|
| Dropi | Playwright scraping | Sin API oficial confirmada |
| Meta Graph API | API key + token | Requiere Business Manager |
| TikTok Marketing API | API key | Requiere cuenta Business |
| Google Ads API | OAuth2 | Developer token requerido |
| Claude API (Anthropic) | API key | claude-sonnet-4-6 |
| PostgreSQL | Self-hosted o Supabase free | Persistencia de datos |
| Redis | Upstash free tier | Cola de tareas y estado |
