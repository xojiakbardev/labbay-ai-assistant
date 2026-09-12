import datetime as dt

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import UUIDPk
from app.core.db import Base

# received   -> persisted by the webhook request, waiting for the worker
# processing -> claimed by a worker (claimed_at is the lease start)
# processed  -> done (including "nothing to do")
# failed     -> last attempt raised; retried after next_attempt_at
# abandoned  -> gave up after MAX_ATTEMPTS, or a pre-outbox legacy row
EVENT_RECEIVED = "received"
EVENT_PROCESSING = "processing"
EVENT_PROCESSED = "processed"
EVENT_FAILED = "failed"
EVENT_ABANDONED = "abandoned"


class WebhookEvent(Base, UUIDPk):
    """Idempotency guard and work queue for inbound Instagram events.

    The webhook request only verifies, persists and returns 200; the slow part
    (debounce, LLM turn, delivery) runs off this row, so a crash or a slow turn
    can never make Meta give up on the event — a sweeper re-drives anything
    left unfinished.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (Index("ix_webhook_events_status_next_attempt_at", "status", "next_attempt_at"),)

    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # instagram
    external_event_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=EVENT_RECEIVED, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
