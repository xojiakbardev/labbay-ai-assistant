import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, false, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import UUIDPk
from app.core.db import Base
import app.customers.models  # noqa: F401


class Conversation(Base, UUIDPk):
    __tablename__ = "conversations"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="instagram", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="ai_active", nullable=False
    )  # ai_active | human_needed | human_active | closed
    last_message_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # What the AI knows about this conversation *as a sale in progress* — the
    # stage, the product in focus, the grounded price/stock facts it has already
    # told this customer, the question it's waiting on. Message history alone
    # doesn't carry any of that: tool results are not persisted, so without this
    # every turn re-derives the facts from scratch and can contradict what it
    # said two messages ago. Shape and update rules live in
    # app/ai/conversation_state.py.
    #
    # Assign a new dict to change it — mutating in place won't mark the
    # attribute dirty, and the update will silently not be saved.
    working_state: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Message(Base, UUIDPk):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_type: Mapped[str] = mapped_column(String(20), nullable=False)  # customer | ai | human | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(20), default="text", nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(512), unique=True, nullable=True)
    flagged_for_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    attachment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
