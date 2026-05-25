import asyncio
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.analytics.models import MetricSnapshot, OptimizationAction, WeeklyReport
from agents.analytics.notifier import TelegramNotifier
from agents.analytics.optimizer import ProductionOptimizer
from agents.analytics.platforms.google_ads import GoogleAdsReportClient
from agents.analytics.platforms.meta import MetaInsightsClient
from agents.analytics.platforms.tiktok import TikTokReportClient
from agents.analytics.reporter import AnalyticsReporter
from app.config import Settings
from app.logger import get_logger
from app.models import AgentLog, Campaign, Metric

log = get_logger("analytics_agent")


class AnalyticsAgent:
    """
    Agente de analítica y optimización autónoma.

    Modo collect (08:00 COT): recolecta métricas del día anterior de las 3 plataformas.
    Modo optimize (10:00 COT): aplica reglas del Optimizer y ejecuta acciones vía API.

    Llamado por el Orchestrator (Fase 6) diariamente.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._optimizer = ProductionOptimizer()
        self._notifier = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
        self._reporter = AnalyticsReporter(
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
        )

    # ── Modo collect ────────────────────────────────────────────────────────────

    async def collect_metrics(self, db: AsyncSession) -> list[MetricSnapshot]:
        """
        Recolecta métricas del día anterior para todas las campañas activas.
        Guarda resultados en la tabla metrics.
        """
        yesterday = date.today() - timedelta(days=1)
        log.info("Iniciando colección de métricas", date=str(yesterday))

        # Obtener campañas activas con external_id
        result = await db.execute(
            select(Campaign)
            .where(Campaign.status == "active")
            .where(Campaign.external_id.is_not(None))
        )
        campaigns = result.scalars().all()
        log.info("Campañas activas encontradas", count=len(campaigns))

        snapshots: list[MetricSnapshot] = []
        for campaign in campaigns:
            snapshot = await self._fetch_platform_metrics(campaign, yesterday)
            if snapshot:
                db.add(Metric(
                    campaign_id=campaign.id,
                    date=yesterday,
                    impressions=snapshot.impressions,
                    clicks=snapshot.clicks,
                    conversions=snapshot.conversions,
                    spend_usd=snapshot.spend_usd,
                    revenue_usd=snapshot.revenue_usd,
                    roas=snapshot.roas,
                    ctr=snapshot.ctr,
                    cpc=snapshot.cpc,
                ))
                snapshots.append(snapshot)

        await db.commit()

        db.add(AgentLog(
            agent="analytics",
            action="collect_metrics",
            status="success",
            meta={
                "date": str(yesterday),
                "campaigns_processed": len(campaigns),
                "snapshots_saved": len(snapshots),
            },
        ))
        await db.commit()

        log.info("Métricas recolectadas", snapshots=len(snapshots))
        return snapshots

    async def _fetch_platform_metrics(
        self, campaign: Campaign, target_date: date
    ) -> MetricSnapshot | None:
        """Despacha la llamada al cliente correcto según la plataforma."""
        s = self._settings
        try:
            if campaign.platform == "meta" and s.meta_access_token:
                async with MetaInsightsClient(s.meta_access_token, s.meta_ad_account_id) as client:
                    return await client.get_campaign_metrics(
                        campaign.external_id, str(campaign.id), target_date
                    )
            elif campaign.platform == "tiktok" and s.tiktok_access_token:
                async with TikTokReportClient(s.tiktok_access_token, s.tiktok_advertiser_id) as client:
                    return await client.get_campaign_metrics(
                        campaign.external_id, str(campaign.id), target_date
                    )
            elif campaign.platform == "google" and s.google_ads_customer_id:
                client = GoogleAdsReportClient(
                    developer_token=s.google_ads_developer_token,
                    customer_id=s.google_ads_customer_id,
                    client_id=s.google_ads_client_id,
                    client_secret=s.google_ads_client_secret,
                    refresh_token=s.google_ads_refresh_token,
                )
                return await client.get_campaign_metrics(
                    campaign.external_id, str(campaign.id), target_date
                )
        except Exception as exc:
            log.warning(
                "Error obteniendo métricas de plataforma",
                platform=campaign.platform,
                campaign_id=campaign.external_id,
                error=str(exc),
            )
        return None

    # ── Modo optimize ───────────────────────────────────────────────────────────

    async def run_optimization(self, db: AsyncSession) -> list[OptimizationAction]:
        """
        Evalúa campañas activas y ejecuta acciones autónomas.
        Retorna lista de todas las acciones tomadas (o intentadas).
        """
        log.info("Iniciando ciclo de optimización")

        result = await db.execute(
            select(Campaign)
            .where(Campaign.status.in_(["active", "paused"]))
            .where(Campaign.external_id.is_not(None))
        )
        campaigns = result.scalars().all()

        all_actions: list[OptimizationAction] = []
        today_str = date.today().strftime("%Y-%m-%d")

        for campaign in campaigns:
            # Cargar últimos 30 días de métricas de la DB
            metrics_result = await db.execute(
                select(Metric)
                .where(Metric.campaign_id == campaign.id)
                .order_by(Metric.date.asc())
            )
            db_metrics = metrics_result.scalars().all()

            if not db_metrics:
                continue

            # Convertir a MetricSnapshot para el optimizer
            daily_metrics = [
                MetricSnapshot(
                    campaign_db_id=str(campaign.id),
                    external_id=campaign.external_id,
                    platform=campaign.platform,
                    date=m.date,
                    impressions=m.impressions,
                    clicks=m.clicks,
                    conversions=m.conversions,
                    spend_usd=float(m.spend_usd),
                    revenue_usd=float(m.revenue_usd),
                )
                for m in db_metrics
            ]

            actions = self._optimizer.evaluate_campaign(
                campaign_db_id=str(campaign.id),
                external_id=campaign.external_id,
                platform=campaign.platform,
                daily_metrics=daily_metrics,
                current_budget_usd=float(campaign.daily_budget_usd),
                initial_budget_usd=float(campaign.daily_budget_usd),
            )

            for action in actions:
                await self._execute_action(action, campaign, db)
                all_actions.append(action)

        if all_actions:
            await self._notifier.send_optimization_summary(all_actions, today_str)

        db.add(AgentLog(
            agent="analytics",
            action="run_optimization",
            status="success" if all_actions is not None else "failure",
            meta={
                "date": today_str,
                "campaigns_evaluated": len(campaigns),
                "actions_taken": len(all_actions),
                "paused": sum(1 for a in all_actions if a.action == "pause" and a.executed),
                "scaled": sum(1 for a in all_actions if a.action == "scale_budget" and a.executed),
            },
        ))
        await db.commit()

        log.info("Optimización completada", total_actions=len(all_actions))
        return all_actions

    async def _execute_action(
        self, action: OptimizationAction, campaign: Campaign, db: AsyncSession
    ) -> None:
        """Ejecuta una acción de optimización vía API de la plataforma."""
        s = self._settings

        try:
            if action.action == "pause":
                executed = await self._pause_campaign_on_platform(action, s)
                action.executed = executed
                if executed:
                    campaign.status = "paused"
                    log.info("Campaña pausada", platform=action.platform, id=action.external_id)

            elif action.action == "scale_budget" and action.new_value:
                executed = await self._scale_budget_on_platform(action, s)
                action.executed = executed
                if executed:
                    campaign.daily_budget_usd = action.new_value
                    log.info(
                        "Budget escalado",
                        platform=action.platform,
                        old=action.old_value,
                        new=action.new_value,
                    )

            elif action.action in ("alert_spike", "flag_low_ctr"):
                # Solo notificación — ya se maneja en send_optimization_summary
                action.executed = True

        except Exception as exc:
            action.error = str(exc)
            log.error("Error ejecutando acción", action=action.action, error=str(exc))

    async def _pause_campaign_on_platform(self, action: OptimizationAction, s) -> bool:
        if action.platform == "meta" and s.meta_access_token:
            async with MetaInsightsClient(s.meta_access_token, s.meta_ad_account_id) as client:
                return await client.pause_campaign(action.external_id)
        elif action.platform == "tiktok" and s.tiktok_access_token:
            async with TikTokReportClient(s.tiktok_access_token, s.tiktok_advertiser_id) as client:
                return await client.pause_campaign(action.external_id)
        elif action.platform == "google" and s.google_ads_customer_id:
            client = GoogleAdsReportClient(
                developer_token=s.google_ads_developer_token,
                customer_id=s.google_ads_customer_id,
                client_id=s.google_ads_client_id,
                client_secret=s.google_ads_client_secret,
                refresh_token=s.google_ads_refresh_token,
            )
            return await client.pause_campaign(action.external_id)
        return False

    async def _scale_budget_on_platform(self, action: OptimizationAction, s) -> bool:
        new_budget = action.new_value
        if action.platform == "meta" and s.meta_access_token:
            # Para Meta escalamos el adset — usamos external_id como adset_id fallback
            async with MetaInsightsClient(s.meta_access_token, s.meta_ad_account_id) as client:
                return await client.update_adset_budget(action.external_id, new_budget)
        elif action.platform == "tiktok" and s.tiktok_access_token:
            async with TikTokReportClient(s.tiktok_access_token, s.tiktok_advertiser_id) as client:
                return await client.update_adgroup_budget(action.external_id, new_budget)
        elif action.platform == "google" and s.google_ads_customer_id:
            client = GoogleAdsReportClient(
                developer_token=s.google_ads_developer_token,
                customer_id=s.google_ads_customer_id,
                client_id=s.google_ads_client_id,
                client_secret=s.google_ads_client_secret,
                refresh_token=s.google_ads_refresh_token,
            )
            return await client.update_campaign_budget(action.external_id, new_budget)
        return False

    # ── Reporte semanal ─────────────────────────────────────────────────────────

    async def run_weekly_report(self, db: AsyncSession) -> str:
        """Genera reporte semanal (llamar los domingos). Retorna Markdown del reporte."""
        today = date.today()
        week_start = today - timedelta(days=7)

        result = await db.execute(
            select(Campaign, Metric)
            .join(Metric, Metric.campaign_id == Campaign.id)
            .where(Metric.date >= week_start)
            .where(Metric.date <= today)
        )
        rows = result.all()

        metrics_by_campaign: dict[str, list[MetricSnapshot]] = {}
        for campaign, m in rows:
            name = f"{campaign.platform}:{campaign.external_id[:8]}"
            if name not in metrics_by_campaign:
                metrics_by_campaign[name] = []
            metrics_by_campaign[name].append(
                MetricSnapshot(
                    campaign_db_id=str(campaign.id),
                    external_id=campaign.external_id,
                    platform=campaign.platform,
                    date=m.date,
                    impressions=m.impressions,
                    clicks=m.clicks,
                    conversions=m.conversions,
                    spend_usd=float(m.spend_usd),
                    revenue_usd=float(m.revenue_usd),
                )
            )

        total_spend = sum(
            m.spend_usd for ms in metrics_by_campaign.values() for m in ms
        )
        total_revenue = sum(
            m.revenue_usd for ms in metrics_by_campaign.values() for m in ms
        )
        overall_roas = total_revenue / total_spend if total_spend > 0 else 0.0

        # Identificar mejor y peor campaña por ROAS promedio
        campaign_roas = {
            name: sum(m.roas for m in ms) / len(ms)
            for name, ms in metrics_by_campaign.items()
            if ms
        }
        top_campaign = max(campaign_roas, key=campaign_roas.get) if campaign_roas else None
        worst_campaign = min(campaign_roas, key=campaign_roas.get) if campaign_roas else None

        weekly_report = WeeklyReport(
            week_start=week_start,
            week_end=today,
            total_spend_usd=total_spend,
            total_revenue_usd=total_revenue,
            overall_roas=overall_roas,
            top_campaign=top_campaign,
            worst_campaign=worst_campaign,
        )

        analysis = await self._reporter.generate_weekly_report(weekly_report, metrics_by_campaign)
        weekly_report.analysis_text = analysis

        db.add(AgentLog(
            agent="analytics",
            action="weekly_report",
            status="success",
            reasoning=analysis[:2000],
            meta={
                "week_start": str(week_start),
                "week_end": str(today),
                "total_spend": total_spend,
                "total_revenue": total_revenue,
                "overall_roas": overall_roas,
            },
        ))
        await db.commit()

        log.info("Reporte semanal generado", roas=overall_roas, spend=total_spend)
        return analysis
