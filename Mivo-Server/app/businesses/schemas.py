import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class BusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    subscription_expires_at: dt.datetime | None
    description: str | None
    target_customers: str | None
    tone: str | None
    language: str | None
    selling_approach: str | None
    rules_text: str | None
    discount_policy: str | None
    delivery_info: str | None
    payment_info: str | None
    handoff_instructions: str | None
    ai_enabled: bool
    ui_preferences: dict | None = None


class BusinessUpdate(BaseModel):
    """All fields optional — PATCH semantics, only supplied fields are updated."""

    name: str | None = None
    description: str | None = None
    target_customers: str | None = None
    tone: str | None = None
    language: str | None = None
    selling_approach: str | None = None
    rules_text: str | None = None
    discount_policy: str | None = None
    delivery_info: str | None = None
    payment_info: str | None = None
    handoff_instructions: str | None = None
    ai_enabled: bool | None = None
    ui_preferences: dict | None = None
