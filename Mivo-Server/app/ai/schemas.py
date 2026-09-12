import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FeedbackCreate(BaseModel):
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    rating: Literal["thumb_up", "thumb_down"]
    customer_query: str | None = Field(default=None, max_length=2000)
    ai_response: str | None = Field(default=None, max_length=4000)
    # Goes into every future system prompt of this business — bounded.
    correction: str | None = Field(default=None, max_length=1000)


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
    content: str = Field(min_length=1, max_length=2000)
    attachment_url: str | None = Field(default=None, max_length=2048)
    simulate_telegram: bool = False

    @field_validator("attachment_url")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        # Handed to the model provider as an image URL — a public https image,
        # never a data: blob or an internal address.
        if value is not None and not value.startswith("https://"):
            raise ValueError("attachment_url must be an https URL")
        return value


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

