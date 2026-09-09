import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String
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

    ig_business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ig_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fb_page_id: Mapped[str] = mapped_column(String(64), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    token_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="connected", nullable=False)
    connected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
