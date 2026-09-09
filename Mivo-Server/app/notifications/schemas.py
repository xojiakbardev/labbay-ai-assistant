import datetime as dt
import uuid
from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    type: str  # lead_hot | lead_warm | lead_updated | system
    title: str
    message: str
    lead_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    extra_metadata: dict = {}
    is_read: bool
    created_at: dt.datetime
    read_at: dt.datetime | None = None


class UnreadCountOut(BaseModel):
    unread_count: int


class MarkReadAllOut(BaseModel):
    marked_read: int
