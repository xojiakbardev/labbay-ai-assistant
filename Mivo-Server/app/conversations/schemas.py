import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict

ConversationStatus = Literal["ai_active", "active", "human_needed", "human_active", "closed"]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender_type: str
    content: str
    message_type: str
    # Media is carried in these fields, never parsed out of `content` — the
    # content of a customer message is whatever the customer typed.
    attachment_url: str | None = None
    attachment_type: str | None = None
    # Outbound only: pending | sent | failed | unknown (pre-outbox rows).
    delivery_status: str | None = None
    delivery_error: str | None = None
    created_at: dt.datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    customer_username: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    channel: str
    status: str
    last_message_at: dt.datetime | None
    created_at: dt.datetime


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]
    # True when older messages exist beyond the ones returned.
    has_more_messages: bool = False
