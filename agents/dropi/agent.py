from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.dropi.api_client import DropiAPIClient
from agents.dropi.browser_client import DropiBrowserClient
from agents.dropi.models import DropiProductRaw
from app.config import Settings
from app.logger import get_logger
from app.models import AgentLog, Order, Product


class DropiAgent:
    """
    Agente principal de Dropi. Orquesta el DropiAPIClient y DropiBrowserClient
    para mantener productos y órdenes sincronizados con la DB local.

    Usado por el Orchestrator (Fase 6) en el ciclo autónomo.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._log = get_logger("dropi")
        self.api_client = DropiAPIClient(
            integration_key=settings.dropi_integration_key,
            base_url=settings.dropi_api_url,
        )
        self.browser = DropiBrowserClient(
            email=settings.dropi_email,
            password=settings.dropi_password,
            headless=settings.playwright_headless,
            state_dir=settings.playwright_state_dir,
        )

    # ── Ciclo completo ──────────────────────────────────────────────────────────

    async def run_full_sync(self, db: AsyncSession) -> dict:
        """
        Ciclo completo: login → sync_catalog → sync_orders → check_stock.
        Llamado por el Orchestrator en cada ciclo de 2 horas.
        """
        self._log.info("Iniciando ciclo completo de sincronización Dropi")

        async with self.browser:
            await self.browser.load_or_login()
            catalog_result = await self.sync_catalog(db)

        orders_result = {"new": 0, "updated": 0}
        if self._settings.dropi_integration_key:
            async with self.api_client:
                orders_result = await self.sync_orders(db)
        else:
            self._log.warning(
                "dropi_integration_key no configurada — saltando sync de órdenes via API"
            )

        async with self.browser:
            await self.browser.load_or_login()
            paused = await self.check_and_pause_out_of_stock(db)

        summary = {
            "catalog": catalog_result,
            "orders": orders_result,
            "paused_products": paused,
        }

        await self._log_action(db, "run_full_sync", "success", summary)
        self._log.info("Ciclo completo finalizado", **summary)
        return summary

    # ── Sincronización de catálogo ──────────────────────────────────────────────

    async def sync_catalog(self, db: AsyncSession) -> dict:
        """
        Scrapea el catálogo de Dropi y hace upsert en la tabla products.
        Requiere que self.browser esté en contexto activo (async with).
        """
        self._log.info("Sincronizando catálogo de Dropi")
        raw_products = await self.browser.scrape_full_catalog()

        new_count = updated_count = 0

        for raw in raw_products:
            existing = await db.scalar(
                select(Product).where(Product.dropi_id == raw.dropi_id)
            )
            if existing is None:
                db.add(Product(**raw.to_db_dict()))
                new_count += 1
            else:
                existing.price_buy = raw.price_buy
                existing.price_sell = raw.price_sell
                existing.stock = raw.stock
                existing.status = "active" if raw.is_available else "inactive"
                if raw.images:
                    existing.images = raw.images
                updated_count += 1

        # Productos activos en DB que ya no aparecen en Dropi → desactivar
        dropi_ids_scraped = {p.dropi_id for p in raw_products}
        result = await db.execute(select(Product).where(Product.status == "active"))
        deactivated_count = 0
        for product in result.scalars():
            if product.dropi_id not in dropi_ids_scraped:
                product.status = "inactive"
                deactivated_count += 1

        await db.commit()
        summary = {"new": new_count, "updated": updated_count, "deactivated": deactivated_count}
        self._log.info("Catálogo sincronizado", **summary)
        return summary

    # ── Sincronización de órdenes ───────────────────────────────────────────────

    async def sync_orders(self, db: AsyncSession) -> dict:
        """
        Obtiene órdenes de la API REST y hace upsert en la tabla orders.
        Requiere que self.api_client esté en contexto activo (async with).
        """
        self._log.info("Sincronizando órdenes desde API de Dropi")
        raw_orders = await self.api_client.get_all_orders()

        new_count = updated_count = 0
        for raw in raw_orders:
            existing = await db.scalar(
                select(Order).where(Order.dropi_order_id == raw.dropi_order_id)
            )

            if existing is None:
                product = await db.scalar(
                    select(Product).where(Product.dropi_id == raw.product_dropi_id)
                )
                if product:
                    db.add(
                        Order(
                            dropi_order_id=raw.dropi_order_id,
                            product_id=product.id,
                            status=raw.status,
                            revenue_usd=raw.revenue_usd,
                        )
                    )
                    new_count += 1
            elif existing.status != raw.status:
                existing.status = raw.status
                updated_count += 1

        await db.commit()
        summary = {"new": new_count, "updated": updated_count}
        self._log.info("Órdenes sincronizadas", **summary)
        return summary

    # ── Gestión de stock ────────────────────────────────────────────────────────

    async def check_and_pause_out_of_stock(self, db: AsyncSession) -> list[str]:
        """
        Detecta productos activos con stock=0 y los pausa en Dropi y en DB.
        Requiere que self.browser esté en contexto activo (async with).
        """
        result = await db.execute(
            select(Product).where(Product.status == "active", Product.stock == 0)
        )
        out_of_stock = list(result.scalars())

        if not out_of_stock:
            return []

        self._log.info("Pausando productos sin stock", count=len(out_of_stock))
        paused: list[str] = []

        for product in out_of_stock:
            success = await self.browser.deactivate_product(product.dropi_id)
            if success:
                product.status = "inactive"
                paused.append(product.dropi_id)
                self._log.info("Producto pausado por stock=0", dropi_id=product.dropi_id)

        await db.commit()
        return paused

    # ── Activación de productos ─────────────────────────────────────────────────

    async def activate_products(
        self, dropi_ids: list[str], db: AsyncSession
    ) -> dict:
        """
        Activa en Dropi los productos del shortlist del Research Agent.
        Requiere que self.browser esté en contexto activo (async with).
        """
        activated: list[str] = []
        failed: list[str] = []

        for dropi_id in dropi_ids:
            product = await db.scalar(
                select(Product).where(Product.dropi_id == dropi_id)
            )
            if product is None:
                self._log.warning("Producto no encontrado en DB", dropi_id=dropi_id)
                failed.append(dropi_id)
                continue

            success = await self.browser.activate_product(dropi_id)
            if success:
                product.status = "active"
                activated.append(dropi_id)
            else:
                failed.append(dropi_id)

        await db.commit()
        result = {"activated": activated, "failed": failed}
        self._log.info("Activación de productos completada", **result)
        await self._log_action(db, "activate_products", "success", result)
        return result

    async def activate_products_from_shortlist(
        self, shortlist: list[DropiProductRaw], db: AsyncSession
    ) -> dict:
        """Recibe la lista del Research Agent y activa los productos disponibles."""
        dropi_ids = [p.dropi_id for p in shortlist if p.is_available]
        async with self.browser:
            await self.browser.load_or_login()
            return await self.activate_products(dropi_ids, db)

    # ── Logging ─────────────────────────────────────────────────────────────────

    async def _log_action(
        self,
        db: AsyncSession,
        action: str,
        status: str,
        metadata: dict,
    ) -> None:
        db.add(
            AgentLog(
                agent="dropi",
                action=action,
                status=status,
                meta=metadata,
            )
        )
        await db.commit()
