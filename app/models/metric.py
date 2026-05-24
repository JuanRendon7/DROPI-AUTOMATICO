import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    impressions: Mapped[int] = mapped_column(default=0)
    clicks: Mapped[int] = mapped_column(default=0)
    conversions: Mapped[int] = mapped_column(default=0)
    spend_usd: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    revenue_usd: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    roas: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    ctr: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    cpc: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    campaign = relationship("Campaign", backref="metrics", lazy="select")

    def __repr__(self) -> str:
        return f"<Metric campaign={self.campaign_id} date={self.date} roas={self.roas}>"
