import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import TimestampMixin, UUIDPk
from app.core.db import Base


class Lead(Base, UUIDPk, TimestampMixin):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("business_id", "customer_id", name="uq_leads_business_id_customer_id"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: an owner tidying up a chat must not silently
    # lose the captured phone number and lead history with it.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(10), default="cold", nullable=False)  # cold | warm | hot
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    interested_products: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    summaries: Mapped[dict | None] = mapped_column(JSONB, default=dict, server_default="{}", nullable=True)
    known_facts: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)

    # Set on HOT transition; gates one-time Telegram notification per HOT event.
    hot_notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
