import datetime as dt

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import UUIDPk
from app.core.db import Base


class WebhookEvent(Base, UUIDPk):
    """Idempotency guard for both Instagram and Telegram webhooks (plan §5/§11)."""

    __tablename__ = "webhook_events"

    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # instagram | telegram
    external_event_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="received", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
