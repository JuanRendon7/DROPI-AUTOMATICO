# Plan — Fase 3: Agente de Investigación de Mercado

**Fase:** 3 de 7
**Estado:** `pending`
**Estimación:** 3–4 días
**Objetivo:** Identificar automáticamente los productos con mayor potencial de venta combinando datos de Google Trends, Amazon, TikTok, Mercado Libre y Reddit, cruzarlos con el catálogo de Dropi, y generar un shortlist TOP 10 con justificación vía Claude.

**Stack de investigación:** pytrends-modern + SerpAPI + TikTokApi + PRAW + Playwright (MercadoLibre) + Claude claude-sonnet-4-6

---

## Estructura de archivos objetivo

```
agents/
└── research/
    ├── __init__.py
    ├── agent.py              # ResearchAgent — orquestador
    ├── models.py             # Pydantic schemas de investigación
    ├── scorer.py             # Algoritmo de scoring (0–100)
    ├── sources/
    │   ├── __init__.py
    │   ├── google_trends.py  # pytrends-modern
    │   ├── amazon.py         # SerpAPI → Amazon Best Sellers
    │   ├── tiktok.py         # TikTokApi + fallback
    │   ├── mercadolibre.py   # Playwright scraping LatAm
    │   └── reddit.py         # PRAW
    └── llm_analyst.py        # Claude: genera TOP 10 con justificación

tests/
└── test_research_agent.py
```

---

## Tareas

### TASK-3-01: Modelos Pydantic del dominio Research
**Archivo:** `agents/research/models.py`

```python
class ProductSignal(BaseModel):
    """Señal de tendencia de una fuente específica."""
    source: str        # "google_trends" | "amazon" | "tiktok" | "mercadolibre" | "reddit"
    keyword: str
    trend_score: float  # 0–100 normalizado por la fuente
    rank: int | None    # posición en ranking (si aplica)
    volume: int | None  # búsquedas/menciones
    growth_rate: float  # % de crecimiento (positivo = subiendo)
    fetched_at: datetime

class ProductResearch(BaseModel):
    """Datos agregados de investigación para un producto/keyword."""
    keyword: str
    signals: list[ProductSignal]
    composite_score: float = 0.0   # 0–100, calculado por scorer.py
    estimated_margin: float = 0.0  # % margen si está en Dropi
    dropi_product: DropiProductRaw | None = None  # None si no está en catálogo
    in_dropi_catalog: bool = False

class ResearchShortlist(BaseModel):
    """Resultado final del Research Agent."""
    generated_at: datetime
    top_products: list[ProductResearch]  # MAX 10, ordenados por score
    analysis: str         # Texto generado por Claude con justificaciones
    sources_used: list[str]
    total_keywords_analyzed: int
```

**Criterio:** `ResearchShortlist` serializa/deserializa con `.model_dump_json()`.

---

### TASK-3-02: Fuente — Google Trends (`google_trends.py`)
**Archivo:** `agents/research/sources/google_trends.py`

```python
class GoogleTrendsSource:
    async def get_trending_keywords(
        self, geo: str = "CO", category: int = 0
    ) -> list[ProductSignal]:
        """Top 20 tendencias actuales en Colombia."""

    async def get_interest_over_time(
        self, keywords: list[str], timeframe: str = "today 3-m"
    ) -> dict[str, float]:
        """Interés relativo (0–100) de cada keyword en los últimos 3 meses."""

    async def get_related_queries(
        self, keyword: str
    ) -> list[str]:
        """Keywords relacionadas que están creciendo."""
```

**Implementación:**
- Usar `pytrends-modern` con `hl="es"` y `geo="CO"`
- Sleep de 60s entre llamadas para evitar rate limiting
- Normalizar `interest_over_time` de 0–100 a float 0.0–1.0 internamente
- Cachear resultados en Redis con TTL de 6 horas (las tendencias no cambian tan rápido)

**Criterio:** Retorna al menos 10 keywords trending en Colombia sin errores 429.

---

### TASK-3-03: Fuente — Amazon Best Sellers (`amazon.py`)
**Archivo:** `agents/research/sources/amazon.py`

```python
class AmazonSource:
    BASE_SERPAPI_URL = "https://serpapi.com/search"

    async def get_best_sellers(
        self, category: str = "electronics", country: str = "co"
    ) -> list[ProductSignal]:
        """Top 20 best sellers de una categoría via SerpAPI."""

    async def search_product(
        self, keyword: str
    ) -> list[ProductSignal]:
        """SERP de Amazon para un keyword."""
```

**Implementación:**
- `httpx.AsyncClient` a SerpAPI con `engine=amazon` o `engine=google_shopping`
- Headers: `api_key: {settings.serpapi_key}`
- Mapear `rank` (posición en resultados) a `trend_score` inverso (rank 1 = score 100)
- Reintentos automáticos con tenacity (429 → esperar 30s)

**Criterio:** Retorna al menos 10 productos de Amazon Best Sellers con rank y score.

---

### TASK-3-04: Fuente — TikTok (`tiktok.py`)
**Archivo:** `agents/research/sources/tiktok.py`

```python
class TikTokSource:
    async def get_trending_products(
        self, count: int = 30
    ) -> list[ProductSignal]:
        """Productos trending en TikTok Shop / TikTok general."""

    async def get_hashtag_volume(
        self, hashtag: str
    ) -> int:
        """Número de videos con un hashtag específico."""
```

**Implementación:**
- Usar `TikTokApi` (pip: `TikTokApi`) con métodos `trending()`
- Si `TikTokApi` falla con error de autenticación: loggear warning y retornar `[]` (degradación elegante)
- No bloquear el pipeline si TikTok no funciona — es fuente complementaria

**Criterio:** Si `TikTokApi` responde, retorna señales. Si falla, retorna lista vacía sin crashear.

---

### TASK-3-05: Fuente — Mercado Libre Colombia (`mercadolibre.py`)
**Archivo:** `agents/research/sources/mercadolibre.py`

```python
class MercadoLibreSource:
    BASE_URL = "https://api.mercadolibre.com"
    SITE_ID = "MCO"  # Colombia

    async def get_trending_searches(self) -> list[str]:
        """Top búsquedas del momento en MercadoLibre Colombia."""

    async def get_top_sellers(
        self, category_id: str = "MCO1648"
    ) -> list[ProductSignal]:
        """Productos más vendidos en una categoría."""

    async def search_product(
        self, keyword: str
    ) -> list[ProductSignal]:
        """Resultados de búsqueda ordenados por relevancia."""
```

**Implementación:**
- **Usar la API REST oficial de MercadoLibre** (no scraping) — es pública y gratuita
- API pública: `https://api.mercadolibre.com/trends/MCO` para trending
- `https://api.mercadolibre.com/sites/MCO/search?q={keyword}&sort=sold_quantity`
- No requiere autenticación para búsquedas públicas
- Muy relevante para Colombia: correlación alta con lo que se vende en Dropi

**Criterio:** Retorna top 20 búsquedas trending en MercadoLibre Colombia.

---

### TASK-3-06: Fuente — Reddit (`reddit.py`)
**Archivo:** `agents/research/sources/reddit.py`

```python
class RedditSource:
    SUBREDDITS = ["dropshipping", "Dropship", "ecommerce", "sidehustle"]

    async def get_product_mentions(
        self, keyword: str, limit: int = 25
    ) -> ProductSignal:
        """Cuenta menciones de un keyword en subreddits relevantes."""

    async def get_hot_posts(self, subreddit: str = "dropshipping") -> list[str]:
        """Extrae keywords de posts 'hot' del subreddit."""
```

**Implementación:**
- Usar `asyncpraw` (versión async de PRAW)
- Credenciales: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`
- Extraer keywords de títulos y comentarios top con regex/NLP básico
- Score basado en upvotes + número de comentarios

**Criterio:** Extrae al menos 5 keywords de posts trending en r/dropshipping.

---

### TASK-3-07: Algoritmo de Scoring (`scorer.py`)
**Archivo:** `agents/research/scorer.py`

```python
class ProductScorer:
    WEIGHTS = {
        "google_trends": 0.30,
        "amazon":        0.25,
        "tiktok":        0.20,
        "margin":        0.15,
        "competition":   0.10,
    }

    def calculate_score(
        self,
        signals: list[ProductSignal],
        dropi_product: DropiProductRaw | None
    ) -> float:
        """
        Calcula score compuesto 0–100 ponderando todas las señales.
        Si hay datos de Dropi, incluye el margen real en el score.
        """

    def _margin_score(self, product: DropiProductRaw) -> float:
        """(precio_venta - precio_compra) / precio_venta * 100"""

    def _competition_score(self, signals: list[ProductSignal]) -> float:
        """Inverso del número de resultados de búsqueda (menos competencia = mayor score)."""

    def rank_products(
        self, researches: list[ProductResearch]
    ) -> list[ProductResearch]:
        """Ordena por composite_score DESC y retorna TOP N."""
```

**Criterio:** `calculate_score()` retorna float 0–100. Productos con mayor margen Y mayor tendencia = score > 70.

---

### TASK-3-08: Analizador LLM (`llm_analyst.py`)
**Archivo:** `agents/research/llm_analyst.py`

Usa Claude claude-sonnet-4-6 para generar el análisis final del shortlist.

```python
class LLMAnalyst:
    SYSTEM_PROMPT = """
    Eres un experto en dropshipping para el mercado colombiano y latinoamericano.
    Analiza los productos investigados y genera recomendaciones claras y accionables.
    Responde siempre en español. Sé directo y práctico.
    """

    async def generate_shortlist_analysis(
        self,
        top_products: list[ProductResearch],
        market_context: str = "Colombia",
    ) -> str:
        """
        Genera texto de análisis para el shortlist TOP 10.
        Incluye: por qué cada producto es prometedor, riesgos, precio sugerido.
        """
```

**Prompt al modelo:**
```
Aquí están los 10 productos con mayor puntaje de tendencia para dropshipping en {market_context}:

{product_list_json}

Para cada producto, proporciona:
1. Por qué tiene potencial ahora mismo (2–3 oraciones)
2. Riesgo principal (1 oración)
3. Precio de venta sugerido y margen esperado

Luego, dame 3 observaciones generales del mercado actual.
```

**Criterio:** Genera texto coherente y útil en español para cada uno de los 10 productos.

---

### TASK-3-09: `ResearchAgent` — orquestador completo
**Archivo:** `agents/research/agent.py`

```python
class ResearchAgent:
    def __init__(self, settings: Settings) -> None:
        self.google = GoogleTrendsSource(settings)
        self.amazon = AmazonSource(settings)
        self.tiktok = TikTokSource(settings)
        self.mercadolibre = MercadoLibreSource(settings)
        self.reddit = RedditSource(settings)
        self.scorer = ProductScorer()
        self.analyst = LLMAnalyst(settings)
        self.log = get_logger("research")

    async def run(self, db: AsyncSession) -> ResearchShortlist:
        """
        Ciclo completo de investigación:
        1. Recolectar tendencias de todas las fuentes en paralelo
        2. Normalizar y cruzar con catálogo de Dropi
        3. Calcular scores
        4. Generar análisis con Claude
        5. Persistir en DB (AgentLog)
        6. Retornar ResearchShortlist
        """
        # Paso 1: recolectar en paralelo (todas las fuentes son independientes)
        results = await asyncio.gather(
            self.google.get_trending_keywords(),
            self.amazon.get_best_sellers(),
            self.tiktok.get_trending_products(),
            self.mercadolibre.get_trending_searches(),
            self.reddit.get_hot_posts("dropshipping"),
            return_exceptions=True,  # errores no crashean el pipeline
        )

        # Paso 2: agregar señales por keyword
        # Paso 3: cruzar con catálogo de Dropi en DB
        # Paso 4: calcular scores
        # Paso 5: TOP 10 → Claude para análisis
        # Paso 6: persistir
```

**Criterio:** `run()` completa incluso si 1–2 fuentes fallan. Retorna `ResearchShortlist` con >= 5 productos.

---

### TASK-3-10: Scheduler Celery (tarea diaria 06:00 COT)
**Archivos:** `app/tasks.py`, `app/celery_app.py`

```python
# celery_app.py
app = Celery("dropi_sales_machine")
app.config_from_object("app.celeryconfig")

# tasks.py
@app.task(name="research.daily_run")
def run_daily_research():
    """Tarea Celery que ejecuta ResearchAgent.run() cada día a las 06:00 COT."""
    asyncio.run(_run_research_async())

# celeryconfig.py
beat_schedule = {
    "research-daily": {
        "task": "research.daily_run",
        "schedule": crontab(hour=11, minute=0),  # 06:00 COT = 11:00 UTC
    },
}
```

**Criterio:** `celery -A app.celery_app beat` programa la tarea y `celery worker` la ejecuta.

---

### TASK-3-11: Actualizar `config.py` y `.env.example`
**Archivos:** `app/config.py`, `.env.example`

Agregar variables:
```python
serpapi_key: str = ""          # OPTIONAL (requerido para Amazon)
reddit_client_id: str = ""     # OPTIONAL
reddit_client_secret: str = "" # OPTIONAL
reddit_user_agent: str = "dropi-sales-machine/1.0"
```

---

### TASK-3-12: Tests del Research Agent
**Archivo:** `tests/test_research_agent.py`

```python
# test_google_trends_returns_signals
# test_amazon_source_parses_serpapi_response
# test_mercadolibre_returns_trending
# test_scorer_calculates_composite_score
# test_scorer_ranks_by_score_desc
# test_research_agent_runs_with_one_source_failing
# test_llm_analyst_generates_text (mock de anthropic)
```

**Criterio:** Todos los tests pasan con mocks. `test_research_agent_runs_with_one_source_failing` verifica degradación elegante.

---

## Orden de ejecución

```
1. TASK-3-01  →  models.py
2. TASK-3-11  →  config.py + .env.example
3. TASK-3-05  →  mercadolibre.py  (API pública, más simple)
4. TASK-3-02  →  google_trends.py
5. TASK-3-03  →  amazon.py
6. TASK-3-04  →  tiktok.py
7. TASK-3-06  →  reddit.py
8. TASK-3-07  →  scorer.py
9. TASK-3-08  →  llm_analyst.py
10. TASK-3-09 →  agent.py (ResearchAgent)
11. TASK-3-10 →  celery_app.py + tasks.py
12. TASK-3-12 →  tests
```

---

## Dependencias nuevas

```toml
# Agregar a [project.dependencies] en pyproject.toml
"asyncpraw>=7.7",          # Reddit async

# Agregar a [project.optional-dependencies] agents
"pytrends-modern>=0.2",    # Google Trends (fork mantenido)
"TikTokApi>=7.3",          # TikTok trends (unofficial)
```

SerpAPI y Mercado Libre usan `httpx` ya instalado. No requieren lib adicional.

---

## Criterios de aceptación (de ROADMAP.md)

- [ ] Genera shortlist de 10 productos con scores en < 30 minutos
- [ ] Justificación legible generada por Claude para cada producto
- [ ] Resultados persistidos en DB (AgentLog) y consultables via API
- [ ] El pipeline no crashea si 1–2 fuentes fallan (degradación elegante)
- [ ] Scheduler Celery ejecuta `run()` diariamente a las 06:00 COT
- [ ] `pytest tests/test_research_agent.py` pasa al 100%

---

## Notas de implementación

- `asyncio.gather(..., return_exceptions=True)` es crítico — nunca dejes que una fuente rota tumbe el pipeline
- MercadoLibre es la fuente más confiable para Colombia (API pública, sin auth) — darle prioridad
- Google Trends requiere delays entre llamadas — no llamar más de 2 veces por minuto
- TikTokApi puede romperse con cualquier update de TikTok — loggear pero no fallar
- El score final de Claude se puede ajustar con prompt engineering en iteraciones futuras
- Cachear resultados de cada fuente en Redis (TTL 6h) para no repetir llamadas costosas
