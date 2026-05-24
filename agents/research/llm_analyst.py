import json

import anthropic

from agents.research.models import ProductResearch, ResearchShortlist
from app.logger import get_logger

log = get_logger("research.llm")

_SYSTEM_PROMPT = """Eres un experto en dropshipping para el mercado colombiano y latinoamericano.
Analiza los productos investigados y genera recomendaciones claras, concretas y accionables.
Responde siempre en español. Sé directo y práctico — sin relleno.
Conoces el mercado colombiano: precios en pesos, plataformas como Dropi, TikTok Shop, Instagram."""


def _format_product_list(products: list[ProductResearch]) -> str:
    lines = []
    for i, p in enumerate(products, 1):
        sources = ", ".join(p.sources_present) if p.sources_present else "sin fuentes"
        margin_info = f", margen~{p.estimated_margin:.0f}%" if p.in_dropi_catalog else ""
        dropi_status = "✓ en catálogo Dropi" if p.in_dropi_catalog else "× no en Dropi"
        lines.append(
            f"{i}. **{p.keyword}** — score={p.composite_score:.1f}/100{margin_info} "
            f"| fuentes: {sources} | {dropi_status}"
        )
    return "\n".join(lines)


class LLMAnalyst:
    """
    Usa Claude claude-sonnet-4-6 para generar el análisis del shortlist de productos.
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate_shortlist_analysis(
        self,
        top_products: list[ProductResearch],
        market_context: str = "Colombia",
    ) -> str:
        """
        Genera análisis en texto para el shortlist TOP N.
        Incluye: por qué cada producto es prometedor, riesgo, precio sugerido.
        """
        if not top_products:
            return "Sin productos para analizar."

        product_list = _format_product_list(top_products)

        user_prompt = f"""Aquí están los {len(top_products)} productos con mayor puntaje de tendencia para dropshipping en {market_context}:

{product_list}

Para cada uno de los **5 productos con score más alto**, proporciona:
1. **Por qué tiene potencial ahora mismo** (2–3 oraciones específicas sobre el mercado actual)
2. **Riesgo principal** (1 oración)
3. **Precio de venta sugerido en COP** y margen esperado

Luego, **3 observaciones generales del mercado colombiano** en este momento.

Sé concreto y accionable. No repitas el número de score en el análisis."""

        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=1500,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = message.content[0].text if message.content else ""
            log.info("LLM análisis generado", tokens_used=message.usage.output_tokens)
            return text

        except Exception as e:
            log.error("LLM: error generando análisis", error=str(e))
            return f"Error generando análisis: {e}"

    async def suggest_ad_copy(
        self, product_keyword: str, platform: str = "facebook"
    ) -> dict[str, str]:
        """
        Genera copy de anuncio para un producto.
        Usado también por el Campaign Agent (Fase 4).
        """
        platform_context = {
            "facebook": "Facebook/Instagram Ads, formato carrusel o imagen única, máximo 125 caracteres para texto principal",
            "tiktok": "TikTok Ads, formato video corto, gancho en los primeros 3 segundos, lenguaje juvenil",
            "google": "Google Search Ads, máximo 30 caracteres por headline, 90 caracteres por descripción",
        }.get(platform, platform)

        prompt = f"""Crea copy de anuncio para el siguiente producto en {market_context if (market_context := 'Colombia') else 'Colombia'}:

**Producto:** {product_keyword}
**Plataforma:** {platform_context}

Genera:
- headline: texto principal llamativo (máximo 30 chars si es Google, 125 si es Meta/TikTok)
- body: descripción del beneficio principal
- cta: llamado a la acción

Responde en JSON con las claves: headline, body, cta."""

        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=400,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text if message.content else "{}"
            # Extraer JSON del texto
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(raw[start:end])
            return {"headline": product_keyword, "body": "", "cta": "Ver más"}

        except Exception as e:
            log.warning("LLM: error generando ad copy", product=product_keyword, error=str(e))
            return {"headline": product_keyword, "body": "", "cta": "Ver más"}
