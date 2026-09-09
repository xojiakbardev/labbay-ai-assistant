import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    customer_username: str | None = None
    conversation_id: uuid.UUID
    status: str
    score: int
    phone: str | None
    interested_products: list
    summary: str | None
    qualification_reason: str | None
    summaries: dict | None = None
    hot_notified_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
