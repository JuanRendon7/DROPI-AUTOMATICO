import anthropic

from agents.analytics.models import MetricSnapshot, OptimizationAction, WeeklyReport
from app.logger import get_logger

log = get_logger("analytics.reporter")

_SYSTEM_PROMPT = """Eres un analista experto en marketing digital y dropshipping para el mercado colombiano.
Analiza métricas de campañas de Facebook Ads, TikTok Ads y Google Ads, y genera recomendaciones claras y accionables.
Responde siempre en español. Sé directo, usa números concretos, sin relleno."""


def _format_metrics(metrics_by_campaign: dict[str, list[MetricSnapshot]]) -> str:
    lines = []
    for campaign_name, metrics in metrics_by_campaign.items():
        if not metrics:
            continue
        total_spend = sum(m.spend_usd for m in metrics)
        total_revenue = sum(m.revenue_usd for m in metrics)
        total_clicks = sum(m.clicks for m in metrics)
        total_impressions = sum(m.impressions for m in metrics)
        avg_roas = total_revenue / total_spend if total_spend > 0 else 0.0
        avg_ctr = total_clicks / total_impressions if total_impressions > 0 else 0.0
        platform = metrics[0].platform if metrics else "?"
        lines.append(
            f"- **{campaign_name}** [{platform.upper()}]: "
            f"ROAS={avg_roas:.2f}x | Gasto=${total_spend:.2f} | "
            f"Ingresos=${total_revenue:.2f} | CTR={avg_ctr:.2%} | "
            f"Días={len(metrics)}"
        )
    return "\n".join(lines) if lines else "Sin datos disponibles."


def _format_actions(actions: list[OptimizationAction]) -> str:
    if not actions:
        return "Ninguna acción tomada esta semana."
    lines = []
    for a in actions:
        status = "✅" if a.executed else ("❌" if a.error else "📋")
        lines.append(f"- {status} `{a.platform}` **{a.action}**: {a.reason[:100]}")
    return "\n".join(lines)


class AnalyticsReporter:
    """Genera el reporte semanal de performance usando Claude."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate_weekly_report(
        self,
        report: WeeklyReport,
        metrics_by_campaign: dict[str, list[MetricSnapshot]],
    ) -> str:
        """Genera análisis semanal en Markdown. Retorna el texto del reporte."""
        metrics_text = _format_metrics(metrics_by_campaign)
        actions_text = _format_actions(report.actions_taken)

        prompt = f"""Aquí están las métricas de la semana {report.week_start} al {report.week_end}:

## Resumen global
- Gasto total: ${report.total_spend_usd:.2f} USD
- Ingresos totales: ${report.total_revenue_usd:.2f} USD
- ROAS global: {report.overall_roas:.2f}x
- Mejor campaña: {report.top_campaign or 'N/A'}
- Peor campaña: {report.worst_campaign or 'N/A'}

## Métricas por campaña
{metrics_text}

## Acciones tomadas automáticamente
{actions_text}

Por favor genera un reporte semanal en Markdown con:

1. **Análisis ejecutivo** (3-4 oraciones sobre el estado general de las campañas)
2. **Campaña estrella de la semana** (la más rentable, con datos concretos)
3. **Campaña problema** (la de peor performance, con recomendación específica)
4. **3 acciones para la próxima semana** (concretas y priorizadas)
5. **Señal de mercado** (observación sobre el mercado colombiano basada en los datos)

Usa formato Markdown limpio. Sé directo y usa números."""

        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=2000,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = message.content[0].text if message.content else ""
            log.info("Reporte semanal generado", tokens=message.usage.output_tokens)
            return text
        except Exception as exc:
            log.error("Error generando reporte semanal", error=str(exc))
            return f"# Reporte Semanal — {report.week_start} al {report.week_end}\n\nError generando análisis: {exc}"
