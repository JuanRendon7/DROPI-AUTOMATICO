# Research — Fase 3: Agente de Investigación de Mercado

## Hallazgos críticos

### 1. pytrends — DEPRECADO
El proyecto original `pytrends` fue **archivado en abril 2025**. Usar el fork mantenido:
- **pip:** `pytrends-modern` — fork activo con backoff inteligente y manejo de cuotas
- Rate limiting: errores 429 después de ~1,400 requests/4h — sleep de 60s entre llamadas requerido
- Métodos clave: `get_trending_searches()`, `get_related_queries()`, `interest_over_time()`

### 2. Amazon Best Sellers — sin librería oficial
- No hay librería dedicada mantenida
- Usar **SerpAPI** ($75/mes) para queries de Amazon → evita problemas de anti-bot
- Alternativa DIY: Playwright con `playwright-stealth` + proxies rotantes
- Amazon usa análisis behavioral (patrones de scroll, mouse) → DIY es frágil en producción

### 3. TikTok Trending
- **Sin API pública oficial** para trending hashtags/productos
- **TikTokApi** (pip: `TikTokApi`, v7.3.3 — activo a abril 2026) con módulo `trending.py`
- Para producción: Apify ($20/mes para ~100 runs) más confiable que librerías que se rompen
- **Estrategia v1:** usar TikTokApi con fallback a Apify si falla

### 4. Fuentes gratuitas relevantes para LatAm
- **Mercado Libre** — scraping Playwright, sin API oficial, muy relevante para Colombia
- **Reddit** — PRAW library (gratis), r/dropshipping, r/Dropship para señales de demanda
- **Google Trends** — via pytrends-modern (gratis)
- **Google Shopping Trends** — API oficial de Google

### 5. SerpAPI
- Tier gratuito: 100 búsquedas/mes
- Plan Developer: $75/mes = 5,000 búsquedas ($0.015/búsqueda)
- Cubre: Google Shopping, Amazon SERP, búsquedas de productos

## Decisiones de arquitectura

### Stack de fuentes de datos

```
┌────────────────────────────────────────────────────┐
│              ResearchAgent                         │
├────────────────┬───────────────────────────────────┤
│ GRATIS         │ DE PAGO (< $100/mes)              │
├────────────────┼───────────────────────────────────┤
│ Google Trends  │ SerpAPI ($75/mes)                 │
│ (pytrends-mod) │  → Amazon Best Sellers SERP       │
│                │  → Google Shopping trends         │
│ Reddit (PRAW)  │                                   │
│                │ TikTokApi (libre, frágil)         │
│ Mercado Libre  │ Apify fallback ($20/mes si falla) │
│ (Playwright)   │                                   │
└────────────────┴───────────────────────────────────┘
```

### Algoritmo de scoring (0–100)
Cada producto se puntúa ponderando las señales:
```
score = (
  google_trend_score  * 0.30 +   # Volumen de búsqueda y tendencia
  amazon_rank_score   * 0.25 +   # Posición en best sellers
  tiktok_score        * 0.20 +   # Menciones y hashtag volume
  margin_score        * 0.15 +   # (precio_venta - precio_compra) / precio_venta
  competition_score   * 0.10     # Inverso de saturación de mercado
)
```

### Claude API para análisis final
Claude claude-sonnet-4-6 recibe:
- Lista de productos con scores
- Contexto de precios de Dropi
- Tendencias actuales

Y genera:
- TOP 10 productos con justificación en español
- Advertencias sobre productos de riesgo
- Sugerencias de precio de venta

## Presupuesto adicional de APIs

| Servicio | Costo/mes | Decisión |
|---|---|---|
| SerpAPI Developer | $75 | Usar — cubre Amazon + Google |
| TikTokApi | $0 | Usar como primer intento |
| Apify TikTok | $20 | Solo si TikTokApi falla repetidamente |
| Proxies (ScrapingBee free) | $0 | Usar tier gratuito |

**Total adicional Fase 3:** ~$75/mes (dentro del budget de $200)
