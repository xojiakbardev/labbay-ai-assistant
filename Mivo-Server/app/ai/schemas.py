import datetime as dt
import uuid
from pydantic import BaseModel, ConfigDict


class FeedbackCreate(BaseModel):
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    rating: str  # 'thumb_up' or 'thumb_down'
    customer_query: str | None = None
    ai_response: str | None = None
    correction: str | None = None


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    conversation_id: uuid.UUID | None
    message_id: uuid.UUID | None
    rating: str
    customer_query: str | None
    ai_response: str | None
    correction: str | None
    is_active: bool
    created_at: dt.datetime


class SandboxMessageRequest(BaseModel):
    content: str
    attachment_url: str | None = None
    simulate_telegram: bool = False


class SandboxMessageOut(BaseModel):
    id: uuid.UUID | str
    sender_type: str
    content: str
    attachment_url: str | None = None
    created_at: dt.datetime


class SandboxProductOut(BaseModel):
    id: str
    name: str
    price: float | None = None
    currency: str = "UZS"
    image_url: str | None = None
    availability: bool = True


class SandboxTurnResponse(BaseModel):
    reply: str
    lead_status: str
    lead_score: int
    qualification_reason: str
    phone_detected: str | None = None
    extracted_facts: list[str] = []
    known_facts: list[dict] = []
    interested_products: list[SandboxProductOut] = []
    executed_tools: list[dict] = []
    telegram_sent: bool = False
    messages: list[SandboxMessageOut] = []


class SandboxStateResponse(BaseModel):
    conversation_id: uuid.UUID | None = None
    messages: list[SandboxMessageOut] = []
    lead_status: str | None = None
    lead_score: int | None = None
    phone: str | None = None
    qualification_reason: str | None = None
    known_facts: list[dict] = []
    interested_products: list[SandboxProductOut] = []

