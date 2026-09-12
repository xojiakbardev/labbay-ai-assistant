import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    customer_username: str | None = None
    # None once the conversation was deleted — the lead outlives it.
    conversation_id: uuid.UUID | None
    status: str
    score: int
    phone: str | None
    interested_products: list
    summary: str | None
    qualification_reason: str | None
    # The reason for the owner, {lang: text}; missing languages fall back to
    # qualification_reason.
    qualification_reasons: dict = {}
    summaries: dict | None = None
    hot_notified_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
