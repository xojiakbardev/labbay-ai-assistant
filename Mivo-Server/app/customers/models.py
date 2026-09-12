import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, false
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import UUIDPk
from app.core.db import Base


class Customer(Base, UUIDPk):
    __tablename__ = "customers"
    __table_args__ = (
        # A customer's Instagram-scoped ID is unique per business, not globally.
        UniqueConstraint("business_id", "ig_scoped_id", name="uq_customers_business_id_ig_scoped_id"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ig_scoped_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The owner's own AI tester (app/ai/router.py): kept out of the inbox,
    # the leads and anything measured from them.
    is_sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    # Last time the Instagram profile lookup ran, successful or not — a profile
    # Meta won't return is not re-requested on every message/page load.
    profile_fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
