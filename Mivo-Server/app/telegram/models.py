import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import UUIDPk
from app.core.db import Base


class TelegramConnection(Base, UUIDPk):
    __tablename__ = "telegram_connections"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connect_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    connect_token_expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    connected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
