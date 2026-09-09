import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import UUIDPk
from app.core.db import Base


class Discount(Base, UUIDPk):
    """Structured discount and promotion model owned by a tenant business."""

    __tablename__ = "discounts"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Optional coupon or promo code (e.g., 'WELCOME10', 'SPRING2026')
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # 'percentage' (e.g. 10.0 for 10%) or 'fixed' (e.g. 50000.0 for 50,000 UZS)
    discount_type: Mapped[str] = mapped_column(String(20), nullable=False, default="percentage")
    value: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)

    # Customer-facing explanation (e.g., "Birinchi xarid uchun 10% chegirma")
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Conditions or restrictions (e.g., "Minimal buyurtma 200 000 so'm", "Birinchi xaridga")
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)

    valid_from: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    valid_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
