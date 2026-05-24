import asyncio
import json
import os
import random
from datetime import datetime
from pathlib import Path

from agents.dropi.exceptions import DropiAuthError, DropiScrapingError, DropiSessionExpiredError
from agents.dropi.models import DropiCatalogPage, DropiProductRaw, DropiSessionState
from app.logger import get_logger

# User-Agent de Chrome real para evitar detección de bot
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


class DropiBrowserClient:
    """
    Cliente Playwright async para operaciones de Dropi que no están disponibles via API:
    - Scraping del catálogo de productos
    - Activación/desactivación de productos
    - Login con persistencia de sesión

    Nota sobre selectores: los selectores CSS/XPath están basados en la estructura
    observada de app.dropi.co. Si la UI cambia, actualizar los selectores en este archivo.
    """

    LOGIN_URL = "https://app.dropi.co/auth/login"
    DASHBOARD_URL = "https://app.dropi.co/dashboard"
    CATALOG_URL = "https://app.dropi.co/dashboard/products"
    ORDERS_URL = "https://app.dropi.co/dashboard/orders"

    # Selectores CSS — actualizar si Dropi cambia su UI
    SEL_EMAIL = "input[type='email'], input[name='email'], #email"
    SEL_PASSWORD = "input[type='password'], input[name='password'], #password"
    SEL_SUBMIT = "button[type='submit'], .btn-login, button:has-text('Ingresar'), button:has-text('Iniciar sesión')"
    SEL_PRODUCT_CARD = ".product-card, .producto-card, [class*='product-item'], [data-product-id]"
    SEL_NEXT_PAGE = "button[aria-label='Next'], .pagination-next, [class*='next-page']:not([disabled])"

    def __init__(
        self,
        email: str,
        password: str,
        headless: bool = True,
        state_dir: str = "playwright_state",
    ) -> None:
        self._email = email
        self._password = password
        self._headless = headless
        self._state_path = Path(state_dir) / "dropi_session.json"
        self._session_meta_path = Path(state_dir) / "dropi_session_meta.json"
        self._log = get_logger("dropi.browser")
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

    async def __aenter__(self) -> "DropiBrowserClient":
        from playwright.async_api import async_playwright

        Path(self._state_path.parent).mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        return self

    async def __aexit__(self, *args) -> None:
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _launch_browser(self, use_saved_state: bool = False) -> None:
        kwargs: dict = {
            "headless": self._headless,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        }

        self._browser = await self._playwright.chromium.launch(**kwargs)

        context_kwargs: dict = {
            "user_agent": _CHROME_UA,
            "viewport": {"width": 1366, "height": 768},
            "locale": "es-CO",
            "timezone_id": "America/Bogota",
        }

        if use_saved_state and self._state_path.exists():
            context_kwargs["storage_state"] = str(self._state_path)

        self._context = await self._browser.new_context(**context_kwargs)
        # Evitar detección de Playwright via navigator.webdriver
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self._page = await self._context.new_page()

    async def _random_delay(self, min_ms: float = 100, max_ms: float = 800) -> None:
        await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

    async def _save_session(self) -> None:
        if self._context:
            await self._context.storage_state(path=str(self._state_path))
            meta = DropiSessionState(
                cookies_path=str(self._state_path),
                last_login=datetime.now(),
                is_valid=True,
            )
            self._session_meta_path.write_text(meta.model_dump_json())
            self._log.info("Sesión de Dropi guardada", path=str(self._state_path))

    def _load_session_meta(self) -> DropiSessionState:
        if not self._session_meta_path.exists():
            return DropiSessionState()
        try:
            data = json.loads(self._session_meta_path.read_text())
            return DropiSessionState.model_validate(data)
        except Exception:
            return DropiSessionState()

    async def load_or_login(self) -> None:
        """Carga sesión existente o hace login si expiró/no existe."""
        meta = self._load_session_meta()
        if meta.is_fresh() and self._state_path.exists():
            self._log.info("Cargando sesión de Dropi existente")
            await self._launch_browser(use_saved_state=True)
            # Verificar que la sesión sigue válida
            await self._page.goto(self.DASHBOARD_URL, wait_until="domcontentloaded")
            if "/auth/login" not in self._page.url:
                self._log.info("Sesión válida, ya autenticado")
                return
            self._log.warning("Sesión expirada, re-haciendo login")
        await self._launch_browser(use_saved_state=False)
        await self.login(self._email, self._password)

    async def login(self, email: str, password: str) -> bool:
        if not self._browser:
            await self._launch_browser()

        self._log.info("Iniciando login en Dropi", url=self.LOGIN_URL)
        await self._page.goto(self.LOGIN_URL, wait_until="networkidle")
        await self._random_delay(500, 1200)

        try:
            email_field = await self._page.wait_for_selector(self.SEL_EMAIL, timeout=15000)
            await email_field.click()
            await self._random_delay(100, 300)
            await email_field.fill(email)
            await self._random_delay(200, 500)

            pwd_field = await self._page.wait_for_selector(self.SEL_PASSWORD, timeout=5000)
            await pwd_field.click()
            await self._random_delay(100, 300)
            await pwd_field.fill(password)
            await self._random_delay(300, 700)

            submit = await self._page.wait_for_selector(self.SEL_SUBMIT, timeout=5000)
            await submit.click()

            # Esperar redirección al dashboard
            await self._page.wait_for_url("**/dashboard**", timeout=20000)

        except Exception as e:
            if "/auth/login" in self._page.url:
                raise DropiAuthError(f"Login fallido: {e}") from e
            raise DropiScrapingError(f"Error durante login: {e}") from e

        await self._save_session()
        self._log.info("Login exitoso en Dropi")
        return True

    async def scrape_catalog_page(self, page: int = 1) -> DropiCatalogPage:
        url = f"{self.CATALOG_URL}?page={page}"
        await self._page.goto(url, wait_until="networkidle")
        await self._random_delay(500, 1500)

        # Esperar que carguen los productos
        try:
            await self._page.wait_for_selector(self.SEL_PRODUCT_CARD, timeout=15000)
        except Exception as e:
            raise DropiScrapingError(f"No se encontraron tarjetas de producto: {e}") from e

        products = await self._extract_products_from_page()

        # Detectar si hay página siguiente
        has_next = False
        try:
            next_btn = await self._page.query_selector(self.SEL_NEXT_PAGE)
            has_next = next_btn is not None and await next_btn.is_visible()
        except Exception:
            pass

        self._log.info("Página de catálogo scrapeada", page=page, products=len(products))
        return DropiCatalogPage(products=products, page=page, has_next=has_next)

    async def _extract_products_from_page(self) -> list[DropiProductRaw]:
        """
        Extrae datos de producto de las tarjetas en la página actual.
        Los selectores asumen la estructura típica de Dropi — ajustar si cambia la UI.
        """
        products: list[DropiProductRaw] = []
        cards = await self._page.query_selector_all(self.SEL_PRODUCT_CARD)

        for card in cards:
            try:
                # Intentar obtener data-product-id primero (más robusto)
                dropi_id = await card.get_attribute("data-product-id") or ""
                if not dropi_id:
                    # Intentar extraer del href de un link dentro de la card
                    link = await card.query_selector("a[href*='/product/'], a[href*='/productos/']")
                    if link:
                        href = await link.get_attribute("href") or ""
                        dropi_id = href.split("/")[-1].split("?")[0]

                if not dropi_id:
                    continue

                # Nombre
                name_el = await card.query_selector(
                    "h2, h3, [class*='name'], [class*='title'], [class*='nombre']"
                )
                name = (await name_el.inner_text()).strip() if name_el else ""

                # Precio de compra (precio del proveedor)
                buy_el = await card.query_selector(
                    "[class*='price-buy'], [class*='precio-compra'], [class*='costo']"
                )
                price_buy_text = (await buy_el.inner_text()).strip() if buy_el else "0"

                # Precio de venta sugerido
                sell_el = await card.query_selector(
                    "[class*='price-sell'], [class*='precio-venta'], [class*='price']:not([class*='buy'])"
                )
                price_sell_text = (await sell_el.inner_text()).strip() if sell_el else "0"

                # Stock
                stock_el = await card.query_selector("[class*='stock'], [class*='inventory']")
                stock_text = (await stock_el.inner_text()).strip() if stock_el else "0"
                stock = int("".join(filter(str.isdigit, stock_text)) or "0")

                # Imágenes
                img_els = await card.query_selector_all("img[src*='dropi'], img[src*='product']")
                images = []
                for img in img_els[:5]:
                    src = await img.get_attribute("src") or await img.get_attribute("data-src") or ""
                    if src and src.startswith("http"):
                        images.append(src)

                # Disponibilidad (sin stock = no disponible)
                is_available = stock > 0

                from agents.dropi.models import DropiProductRaw as _P

                products.append(
                    _P(
                        dropi_id=dropi_id,
                        name=name or f"Producto {dropi_id}",
                        price_buy=price_buy_text or "0",
                        price_sell=price_sell_text or "0",
                        stock=stock,
                        images=images,
                        is_available=is_available,
                    )
                )
            except Exception as e:
                self._log.warning("Error extrayendo producto de card", error=str(e))
                continue

        return products

    async def scrape_full_catalog(self, max_pages: int = 50) -> list[DropiProductRaw]:
        """Scrapea todas las páginas del catálogo."""
        all_products: list[DropiProductRaw] = []
        page = 1

        while page <= max_pages:
            catalog_page = await self.scrape_catalog_page(page)
            all_products.extend(catalog_page.products)

            if not catalog_page.has_next:
                break

            page += 1
            # Anti-detección: delay entre páginas
            await asyncio.sleep(random.uniform(1.0, 3.0))

        self._log.info("Catálogo completo scrapeado", total_products=len(all_products), pages=page)
        return all_products

    async def activate_product(self, dropi_id: str) -> bool:
        """Activa/publica un producto en el panel de Dropi."""
        product_url = f"{self.CATALOG_URL}/{dropi_id}"
        await self._page.goto(product_url, wait_until="networkidle")
        await self._random_delay(500, 1000)

        try:
            activate_btn = await self._page.wait_for_selector(
                "button:has-text('Publicar'), button:has-text('Activar'), "
                "[class*='activate'], [class*='publish']",
                timeout=10000,
            )
            if not await activate_btn.is_visible():
                self._log.info("Producto ya activo", dropi_id=dropi_id)
                return True

            await activate_btn.click()
            await self._random_delay(500, 1000)

            # Verificar que se activó (botón cambia o aparece confirmación)
            await self._page.wait_for_selector(
                "button:has-text('Pausar'), button:has-text('Desactivar'), "
                "[class*='deactivate'], .toast-success, [class*='success']",
                timeout=10000,
            )
            self._log.info("Producto activado", dropi_id=dropi_id)
            return True

        except Exception as e:
            self._log.error("Error activando producto", dropi_id=dropi_id, error=str(e))
            return False

    async def deactivate_product(self, dropi_id: str) -> bool:
        """Pausa/desactiva un producto en el panel de Dropi."""
        product_url = f"{self.CATALOG_URL}/{dropi_id}"
        await self._page.goto(product_url, wait_until="networkidle")
        await self._random_delay(500, 1000)

        try:
            deactivate_btn = await self._page.wait_for_selector(
                "button:has-text('Pausar'), button:has-text('Desactivar'), "
                "[class*='deactivate'], [class*='pause']",
                timeout=10000,
            )
            if not await deactivate_btn.is_visible():
                self._log.info("Producto ya inactivo", dropi_id=dropi_id)
                return True

            await deactivate_btn.click()
            await self._random_delay(300, 700)
            self._log.info("Producto desactivado", dropi_id=dropi_id)
            return True

        except Exception as e:
            self._log.error("Error desactivando producto", dropi_id=dropi_id, error=str(e))
            return False

    async def get_order_status_from_ui(self, order_id: str) -> str:
        """Obtiene el estado de una orden directo de la UI (fallback si la API falla)."""
        orders_url = f"{self.ORDERS_URL}?search={order_id}"
        await self._page.goto(orders_url, wait_until="networkidle")
        await self._random_delay(500, 1000)

        try:
            status_el = await self._page.wait_for_selector(
                f"[data-order-id='{order_id}'] [class*='status'], "
                f"[data-order-id='{order_id}'] [class*='estado']",
                timeout=8000,
            )
            return (await status_el.inner_text()).strip().lower()
        except Exception:
            return "unknown"
