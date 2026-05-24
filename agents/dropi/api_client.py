from datetime import datetime
from decimal import Decimal

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agents.dropi.exceptions import DropiAPIError, DropiAuthError
from agents.dropi.models import DropiOrderRaw
from app.core.exceptions import RateLimitError
from app.logger import get_logger


class DropiAPIClient:
    """
    Cliente httpx async para la API REST oficial de Dropi (api.dropi.co).
    Autenticación via header dropi-integration-key.
    """

    def __init__(self, integration_key: str, base_url: str = "https://api.dropi.co") -> None:
        self._integration_key = integration_key
        self._base_url = base_url.rstrip("/")
        self._log = get_logger("dropi.api")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "DropiAPIClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "dropi-integration-key": self._integration_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _check_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("DropiAPIClient debe usarse como context manager (async with)")
        return self._client

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 401:
            raise DropiAuthError("dropi-integration-key inválida o expirada")
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise RateLimitError("dropi_api", retry_after=retry_after)
        if response.status_code >= 500:
            raise DropiAPIError(response.status_code, response.text[:200])
        if response.status_code >= 400:
            raise DropiAPIError(response.status_code, response.text[:200])

    @retry(
        retry=retry_if_exception_type(DropiAPIError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def get_orders(
        self,
        status: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> list[DropiOrderRaw]:
        client = self._check_client()
        params: dict = {"page": page, "per_page": per_page}
        if status:
            params["status"] = status

        response = await client.get("/orders", params=params)
        self._raise_for_status(response)

        data = response.json()
        orders_data = data.get("data", data) if isinstance(data, dict) else data

        orders = []
        for item in orders_data:
            try:
                order = DropiOrderRaw(
                    dropi_order_id=str(item.get("id", item.get("order_id", ""))),
                    product_dropi_id=str(
                        item.get("product_id", item.get("productId", ""))
                    ),
                    status=item.get("status", "pending"),
                    customer_name=item.get("customer_name", item.get("customerName", "")),
                    revenue_usd=Decimal(str(item.get("total", item.get("amount", 0)))),
                    created_at=datetime.fromisoformat(
                        item.get("created_at", item.get("createdAt", datetime.now().isoformat()))
                    ),
                )
                orders.append(order)
            except Exception as e:
                self._log.warning("No se pudo parsear orden", error=str(e), item=item)

        self._log.info("Órdenes obtenidas desde API", count=len(orders), page=page)
        return orders

    @retry(
        retry=retry_if_exception_type(DropiAPIError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def get_order(self, order_id: str) -> DropiOrderRaw:
        client = self._check_client()
        response = await client.get(f"/orders/{order_id}")
        self._raise_for_status(response)

        item = response.json()
        if isinstance(item, dict) and "data" in item:
            item = item["data"]

        return DropiOrderRaw(
            dropi_order_id=str(item.get("id", order_id)),
            product_dropi_id=str(item.get("product_id", "")),
            status=item.get("status", "pending"),
            customer_name=item.get("customer_name", ""),
            revenue_usd=Decimal(str(item.get("total", 0))),
            created_at=datetime.fromisoformat(
                item.get("created_at", datetime.now().isoformat())
            ),
        )

    async def confirm_order(self, order_id: str) -> bool:
        client = self._check_client()
        response = await client.post(f"/orders/{order_id}/confirm")
        self._raise_for_status(response)
        return response.status_code in (200, 201, 204)

    async def get_shipping_guide(self, order_id: str) -> str:
        """Retorna la URL del PDF de la guía de envío."""
        client = self._check_client()
        response = await client.get(f"/orders/{order_id}/shipping-guide")
        self._raise_for_status(response)

        data = response.json()
        if isinstance(data, dict):
            return data.get("url", data.get("guide_url", ""))
        return str(data)

    async def get_all_orders(self, status: str | None = None) -> list[DropiOrderRaw]:
        """Obtiene todas las órdenes paginando automáticamente."""
        all_orders: list[DropiOrderRaw] = []
        page = 1
        while True:
            batch = await self.get_orders(status=status, page=page)
            if not batch:
                break
            all_orders.extend(batch)
            if len(batch) < 50:
                break
            page += 1
        return all_orders
