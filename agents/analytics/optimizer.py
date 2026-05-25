from dataclasses import dataclass, field

from agents.analytics.models import MetricSnapshot, OptimizationAction


@dataclass
class OptimizerConfig:
    min_days_active: int = 7
    roas_pause_threshold: float = 1.5
    roas_scale_threshold: float = 3.0
    ctr_low_threshold: float = 0.008        # 0.8%
    scale_factor: float = 1.20              # +20%
    max_budget_multiplier: float = 5.0      # Nunca más de 5x el budget inicial
    spend_spike_multiplier: float = 1.5     # Alerta si gasto > 1.5x promedio 7d
    window_days: int = 7                    # Ventana de análisis


class ProductionOptimizer:
    """
    Motor de reglas autónomas para optimización de campañas.
    No toma acciones durante el periodo de aprendizaje (< min_days_active días).
    """

    def __init__(self, config: OptimizerConfig | None = None) -> None:
        self.config = config or OptimizerConfig()

    def evaluate_campaign(
        self,
        campaign_db_id: str,
        external_id: str,
        platform: str,
        daily_metrics: list[MetricSnapshot],  # ordenadas por fecha ASC
        current_budget_usd: float,
        initial_budget_usd: float,
    ) -> list[OptimizationAction]:
        """
        Evalúa una campaña y retorna lista de acciones recomendadas.
        Lista vacía = no hacer nada (periodo de aprendizaje o campaña sana).
        """
        if len(daily_metrics) < self.config.min_days_active:
            return []

        c = self.config
        recent = daily_metrics[-c.window_days:]
        avg_roas = sum(m.roas for m in recent) / len(recent)
        avg_spend = sum(m.spend_usd for m in recent) / len(recent)
        avg_ctr = sum(m.ctr for m in recent) / len(recent)
        today_spend = daily_metrics[-1].spend_usd

        actions: list[OptimizationAction] = []

        # ── Regla 1: Pausar por ROAS bajo ──────────────────────────────────────
        if avg_roas < c.roas_pause_threshold and avg_spend > 0:
            actions.append(OptimizationAction(
                campaign_db_id=campaign_db_id,
                external_id=external_id,
                platform=platform,
                action="pause",
                reason=(
                    f"ROAS promedio {c.window_days}d = {avg_roas:.2f} "
                    f"< umbral {c.roas_pause_threshold}"
                ),
            ))

        # ── Regla 2: Escalar presupuesto (solo si no se va a pausar) ───────────
        elif avg_roas > c.roas_scale_threshold:
            max_budget = initial_budget_usd * c.max_budget_multiplier
            new_budget = current_budget_usd * c.scale_factor
            if new_budget > max_budget:
                new_budget = max_budget
            if new_budget > current_budget_usd:
                actions.append(OptimizationAction(
                    campaign_db_id=campaign_db_id,
                    external_id=external_id,
                    platform=platform,
                    action="scale_budget",
                    reason=(
                        f"ROAS promedio {c.window_days}d = {avg_roas:.2f} "
                        f"> umbral {c.roas_scale_threshold}"
                    ),
                    old_value=round(current_budget_usd, 2),
                    new_value=round(new_budget, 2),
                ))

        # ── Regla 3: Alerta spike de gasto ─────────────────────────────────────
        if avg_spend > 0 and today_spend > avg_spend * c.spend_spike_multiplier:
            actions.append(OptimizationAction(
                campaign_db_id=campaign_db_id,
                external_id=external_id,
                platform=platform,
                action="alert_spike",
                reason=(
                    f"Gasto hoy ${today_spend:.2f} > "
                    f"{c.spend_spike_multiplier}x promedio 7d ${avg_spend:.2f}"
                ),
            ))

        # ── Regla 4: CTR bajo → marcar para rotar creativos ────────────────────
        if 0 < avg_ctr < c.ctr_low_threshold:
            actions.append(OptimizationAction(
                campaign_db_id=campaign_db_id,
                external_id=external_id,
                platform=platform,
                action="flag_low_ctr",
                reason=(
                    f"CTR promedio {c.window_days}d = {avg_ctr:.4%} "
                    f"< umbral {c.ctr_low_threshold:.4%}"
                ),
            ))

        return actions
