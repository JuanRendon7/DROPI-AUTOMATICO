import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dropi_order_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )  # pending | confirmed | shipped | delivered | cancelled
    revenue_usd: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    product = relationship("Product", backref="orders", lazy="select")
    campaign = relationship("Campaign", backref="orders", lazy="select")

    def __repr__(self) -> str:
        return f"<Order {self.dropi_order_id} status={self.status}>"
