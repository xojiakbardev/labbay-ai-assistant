import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import TimestampMixin, UUIDPk
from app.core.db import Base


class Business(Base, UUIDPk, TimestampMixin):
    """One business per owner_user_id (MVP tenant model — see plan §5)."""

    __tablename__ = "businesses"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_customers: Mapped[str | None] = mapped_column(Text, nullable=True)
    tone: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    selling_approach: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The owner's own on/off switch (AI settings page).
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # The platform's kill switch — superadmin only, and the owner has no way to
    # override it. AI replies need ai_enabled AND NOT ai_suspended.
    ai_suspended: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    ui_preferences: Mapped[dict | None] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=True
    )
    # The owner's own wording for the ready-made replies (app/ai/replies.py):
    # {key: {lang: text}}; anything missing uses the default.
    reply_texts: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)

    # Subscription is superadmin-managed, not self-serve: when someone pays
    # (outside the app), the superadmin pushes this date out via the
    # superadmin panel. Null = never activated (shouldn't normally happen,
    # every business gets a trial window on creation). Past this date, AI
    # auto-replies stop (app/instagram/service.py) but the dashboard itself
    # stays reachable so the owner can see it needs renewing.
    subscription_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The tariff (app/billing): its monthly AI-reply limit. Null = no limit.
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # When the plan was last started or renewed: its months (and the reply
    # count) run from here. Null = calendar months.
    plan_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Soft delete: superadmin "deleting" a business/owner. Row stays (leads,
    # conversations, usage history all reference it) — deleted_at set instead.
    # Blocks the owner's login/token refresh (app/auth/service.py) and AI
    # auto-replies (app/instagram/service.py); excluded from the superadmin
    # business list by default.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
