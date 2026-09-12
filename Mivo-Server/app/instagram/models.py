import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import UUIDPk
from app.core.db import Base


class InstagramAccount(Base, UUIDPk):
    """All Meta-specific persistence lives here — see plan §11 (isolated module)."""

    __tablename__ = "instagram_accounts"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # Unique: webhooks are routed by this id, so one Instagram account linked to
    # two businesses would deliver each customer's DMs to whichever row the
    # database happened to return.
    ig_business_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    ig_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fb_page_id: Mapped[str] = mapped_column(String(64), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    token_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="connected", nullable=False)
    connected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class OAuthState(Base, UUIDPk):
    """One Instagram connect attempt.

    The id travels as the OAuth `state` nonce and is single-use. Meta's
    callback doesn't connect anything by itself: it parks the (encrypted) code
    under a fresh, unguessable completion_id that only the browser which came
    back from Instagram ever sees, and the connection is made when the
    business's own logged-in dashboard presents that id. A connect URL sent to
    someone else therefore can't attach *their* Instagram to *your* business.
    """

    __tablename__ = "oauth_states"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completion_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    code_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
