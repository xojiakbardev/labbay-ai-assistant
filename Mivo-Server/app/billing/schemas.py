import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _upper(value: str | None) -> str | None:
    return value.strip().upper() if isinstance(value, str) else value


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price: float
    currency: str
    monthly_ai_replies: int | None
    is_active: bool
    is_default: bool
    sort_order: int
    # Superadmin list only: how many businesses are on it.
    businesses_count: int = 0


class PlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    price: float = Field(ge=0, le=1_000_000_000)
    currency: str = Field(default="USD", pattern="^[A-Za-z]{3}$")
    # Null = unlimited.
    monthly_ai_replies: int | None = Field(default=None, ge=1, le=10_000_000)
    is_active: bool = True
    is_default: bool = False
    sort_order: int = Field(default=0, ge=0, le=1000)

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        return _upper(value)


class PlanUpdate(BaseModel):
    """Only the fields sent change; monthly_ai_replies: null makes it unlimited."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    price: float | None = Field(default=None, ge=0, le=1_000_000_000)
    currency: str | None = Field(default=None, pattern="^[A-Za-z]{3}$")
    monthly_ai_replies: int | None = Field(default=None, ge=1, le=10_000_000)
    is_active: bool | None = None
    is_default: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1000)

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        return _upper(value)


class UsageOut(BaseModel):
    plan: PlanOut | None
    period_start: dt.datetime
    period_end: dt.datetime
    ai_replies_used: int
    # Null = unlimited. The AI stops at the hard limit (limit + grace).
    ai_replies_limit: int | None
    ai_replies_hard_limit: int | None
    subscription_expires_at: dt.datetime | None
    subscription_active: bool
    billing_contact: str
