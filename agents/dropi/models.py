from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator


class DropiProductRaw(BaseModel):
    dropi_id: str
    name: str
    description: str = ""
    price_buy: Decimal
    price_sell: Decimal
    stock: int = 0
    category: str = ""
    images: list[str] = []
    is_available: bool = True

    @field_validator("price_buy", "price_sell", mode="before")
    @classmethod
    def parse_price(cls, v) -> Decimal:
        if isinstance(v, str):
            # Limpiar formato colombiano: "$ 25.000" → "25000"
            cleaned = v.replace("$", "").replace(".", "").replace(",", ".").strip()
            return Decimal(cleaned)
        return Decimal(str(v))

    def to_db_dict(self) -> dict:
        return {
            "dropi_id": self.dropi_id,
            "name": self.name,
            "price_buy": self.price_buy,
            "price_sell": self.price_sell,
            "stock": self.stock,
            "category": self.category,
            "images": self.images,
            "status": "active" if self.is_available else "inactive",
        }


class DropiOrderRaw(BaseModel):
    dropi_order_id: str
    product_dropi_id: str
    status: str  # pending | confirmed | shipped | delivered | cancelled
    customer_name: str = ""
    revenue_usd: Decimal = Decimal("0")
    created_at: datetime

    @field_validator("status")
    @classmethod
    def normalize_status(cls, v: str) -> str:
        mapping = {
            "pendiente": "pending",
            "confirmado": "confirmed",
            "enviado": "shipped",
            "entregado": "delivered",
            "cancelado": "cancelled",
        }
        return mapping.get(v.lower(), v.lower())


class DropiCatalogPage(BaseModel):
    products: list[DropiProductRaw]
    page: int
    has_next: bool
    total_pages: int = 0


class DropiSessionState(BaseModel):
    cookies_path: str | None = None
    last_login: datetime | None = None
    is_valid: bool = False

    def is_fresh(self, max_age_hours: int = 8) -> bool:
        if not self.last_login or not self.is_valid:
            return False
        age = (datetime.now() - self.last_login).total_seconds() / 3600
        return age < max_age_hours
