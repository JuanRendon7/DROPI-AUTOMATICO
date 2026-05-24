import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dropi_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    price_buy: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    price_sell: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    stock: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )  # active | inactive | pending
    category: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    images: Mapped[list] = mapped_column(JSON, default=list)
    score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Product {self.dropi_id} — {self.name[:40]}>"
