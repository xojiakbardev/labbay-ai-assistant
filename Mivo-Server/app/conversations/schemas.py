import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender_type: str
    content: str
    message_type: str
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
