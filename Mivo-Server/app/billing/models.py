import datetime as dt
import uuid

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, Numeric, String, false, text, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import TimestampMixin, UUIDPk
from app.core.db import Base


class Plan(Base, UUIDPk, TimestampMixin):
    """A tariff the superadmin edits in the panel. Payment itself happens
    outside the app; the superadmin records it when extending a subscription."""

    __tablename__ = "plans"
    __table_args__ = (
        # At most one plan is given to newly created businesses.
        Index("uq_plans_single_default", "is_default", unique=True, postgresql_where=text("is_default")),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    # AI replies per calendar month; null = unlimited.
    monthly_ai_replies: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AiReplyUsage(Base):
    """AI replies a business received on Instagram in one billing month."""

    __tablename__ = "ai_reply_usage"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    period_start: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    replies: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
