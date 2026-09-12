import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import password_too_long


class CreateBusinessRequest(BaseModel):
    """Superadmin onboards a new business owner — this is the only way an
    account gets created now (no public /auth/signup)."""

    # Not EmailStr on purpose — this doubles as the login identifier, and the
    # superadmin may set it to a phone number instead of an email.
    email: str = Field(min_length=1, max_length=255)
    # No minimum here on purpose — this is the superadmin setting a password
    # for someone else, often just a placeholder the owner changes later, and
    # they're trusted to judge what's good enough (unlike a public signup
    # form, which is exactly why there isn't one — see module docstring).
    password: str = Field(min_length=1, max_length=72)
    business_name: str = Field(min_length=1, max_length=255)
    trial_days: int = Field(default=14, ge=0, le=365)

    @field_validator("password")
    @classmethod
    def _fits_bcrypt(cls, value: str) -> str:
        if password_too_long(value):
            raise ValueError("password is longer than 72 bytes")
        return value


class SuperadminBusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    owner_email: str
    # The owner's own switch (read-only here) and the platform kill switch.
    ai_enabled: bool
    ai_suspended: bool
    subscription_expires_at: dt.datetime | None
    subscription_active: bool
    created_at: dt.datetime
    cost_last_30d_usd: float
    plan_id: uuid.UUID | None
    plan_name: str | None
    # This month; limit null = unlimited.
    ai_replies_this_month: int
    ai_replies_limit: int | None


class ExtendSubscriptionRequest(BaseModel):
    """Extends/sets the expiry date. Optionally records the payment that
    prompted it and sets the plan — all in one call since in practice a
    superadmin only ever touches this screen because someone just paid.
    `plan_id` changes the plan only when sent; null = no plan (unlimited)."""

    subscription_expires_at: dt.datetime
    payment_amount: float | None = Field(default=None, gt=0)
    payment_currency: str = Field(default="UZS", min_length=3, max_length=3)
    payment_note: str | None = Field(default=None, max_length=500)
    plan_id: uuid.UUID | None = None


class BusinessAiSuspendRequest(BaseModel):
    """The platform kill switch. Separate from the owner's `ai_enabled`, which
    the owner controls — a suspension is not something they can undo."""

    ai_suspended: bool


class StatsOut(BaseModel):
    total_businesses: int
    active_businesses: int
    expired_businesses: int
    tokens_today: int
    cost_today_usd: float
    cost_this_month_usd: float
    income_this_month: dict[str, float]  # {currency: total}
    openrouter_balance_usd: float | None
    openrouter_limit_usd: float | None


class UsagePoint(BaseModel):
    date: dt.date
    tokens: int
    cost_usd: float


class RevenuePoint(BaseModel):
    period: str  # "YYYY-MM"
    income: dict[str, float]  # {currency: total}
    expense_usd: float
