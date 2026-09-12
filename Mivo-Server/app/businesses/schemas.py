import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.replies import DEFAULT_REPLIES, LANGUAGES, MAX_REPLY_CHARS
from app.core.config import get_settings


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
    # Read-only here: the platform's kill switch, set by the superadmin.
    ai_suspended: bool = False
    ui_preferences: dict | None = None
    reply_texts: dict[str, dict[str, str]] = {}


_TEXT = get_settings().business_text_max_chars  # generous for a settings paragraph, bounded for the prompt


class BusinessUpdate(BaseModel):
    """All fields optional — PATCH semantics, only supplied fields are updated.

    `ai_enabled` is the owner's own switch; the superadmin's `ai_suspended`
    is deliberately not settable here."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=_TEXT)
    target_customers: str | None = Field(default=None, max_length=_TEXT)
    tone: str | None = Field(default=None, max_length=_TEXT)
    language: str | None = Field(default=None, max_length=50)
    selling_approach: str | None = Field(default=None, max_length=_TEXT)
    rules_text: str | None = Field(default=None, max_length=_TEXT)
    discount_policy: str | None = Field(default=None, max_length=_TEXT)
    delivery_info: str | None = Field(default=None, max_length=_TEXT)
    payment_info: str | None = Field(default=None, max_length=_TEXT)
    handoff_instructions: str | None = Field(default=None, max_length=_TEXT)
    ai_enabled: bool | None = None
    ui_preferences: dict | None = None
    # Replaces all the owner's reply texts; an empty text means "use the default".
    reply_texts: dict[str, dict[str, str]] | None = None

    @field_validator("name", "ai_enabled")
    @classmethod
    def _not_null(cls, value):
        # These columns are NOT NULL: an explicit null is a client bug, and
        # letting it through was a 500 with the SQL error in the body.
        if value is None:
            raise ValueError("may not be null")
        return value

    @field_validator("ui_preferences")
    @classmethod
    def _small_preferences(cls, value):
        # Merged into the stored dict key by key (see the router); small flat
        # UI settings only.
        if value is None:
            raise ValueError("may not be null")
        if len(value) > 20 or any(len(str(k)) > 50 or len(str(v)) > 200 for k, v in value.items()):
            raise ValueError("ui_preferences is limited to 20 short keys")
        return value

    @field_validator("reply_texts")
    @classmethod
    def _known_replies(cls, value):
        if value is None:
            raise ValueError("may not be null")
        cleaned: dict[str, dict[str, str]] = {}
        for key, texts in value.items():
            if key not in DEFAULT_REPLIES:
                raise ValueError(f"unknown reply {key!r}")
            for lang, text in texts.items():
                if lang not in LANGUAGES:
                    raise ValueError(f"unknown language {lang!r}")
                text = text.strip()
                if len(text) > MAX_REPLY_CHARS:
                    raise ValueError(f"{key}/{lang} is longer than {MAX_REPLY_CHARS} characters")
                if text:
                    cleaned.setdefault(key, {})[lang] = text
        return cleaned


class ReplyDefaultsOut(BaseModel):
    replies: dict[str, dict[str, str]]
    languages: list[str]
    max_chars: int
