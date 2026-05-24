# Dropi Autonomous Sales Machine

## Vision
Una máquina autónoma de ventas de dropshipping basada en múltiples agentes de IA que investiga productos top del mercado, lanza campañas publicitarias automáticamente, gestiona el inventario vía Dropi, y optimiza el gasto publicitario de forma continua — todo sin intervención humana.

## Objetivo de negocio
Generar ventas rentables en piloto automático mediante:
1. Identificación de productos con alta demanda y márgenes atractivos
2. Publicación automática en Dropi
3. Campañas en Meta Ads, TikTok Ads y Google Ads gestionadas por IA
4. Optimización autónoma basada en ROAS y métricas de conversión

## Stack Tecnológico
- **Lenguaje:** Python 3.11+
- **Framework:** FastAPI
- **Orquestación de agentes:** LangGraph (multi-agent loops)
- **LLM:** Claude claude-sonnet-4-6 (Anthropic API) como cerebro de decisión
- **Automatización web:** Playwright (Dropi panel)
- **Base de datos:** PostgreSQL + Redis (cola de tareas)
- **Scheduler:** Celery + Celery Beat
- **APIs de ads:** Meta Graph API, TikTok Marketing API, Google Ads API

## Plataformas de publicidad
- Facebook Ads / Instagram Ads (Meta)
- TikTok Ads
- Google Ads (Shopping + Search)

## Integración con Dropi
- Automatización vía Playwright del panel web de Dropi
- Scraping de catálogo de productos disponibles
- Gestión de órdenes y seguimiento

## Restricciones
- Presupuesto de infraestructura: < $200 USD/mes
- El sistema debe ser 100% autónomo (sin aprobación humana requerida)
- Priorizar costo-eficiencia: aprovechar tiers gratuitos donde sea posible

## Métricas de éxito
- ROAS (Return on Ad Spend) > 2.5x como mínimo
- Costo de adquisición de cliente (CAC) optimizado continuamente
- Uptime del sistema > 99%
- Ciclo de investigación → publicación → campaña < 24 horas

## Arquitectura de Agentes

```
┌─────────────────────────────────────────────────────┐
│                  ORCHESTRATOR AGENT                  │
│         (LangGraph · decide, coordina, itera)        │
└─────────┬──────┬───────────┬────────────┬───────────┘
          │      │           │            │
    ┌─────▼──┐ ┌─▼──────┐ ┌─▼────────┐ ┌─▼──────────┐
    │RESEARCH│ │ DROPI  │ │CAMPAIGNS │ │ ANALYTICS  │
    │ AGENT  │ │ AGENT  │ │  AGENT   │ │  AGENT     │
    │        │ │        │ │          │ │            │
    │Tendencias│Playwright│Meta/TikTok│ Métricas   │
    │Productos │Panel web │Google Ads │ ROAS/CAC   │
    │Scoring  │Catálogo  │Creación   │ Optimizar  │
    └────────┘ └────────┘ └──────────┘ └───────────┘
```

## Equipo
- Desarrollador principal: Juan (solo)
- Asistencia de IA: Claude Code

## Fecha de inicio
2026-05-24
