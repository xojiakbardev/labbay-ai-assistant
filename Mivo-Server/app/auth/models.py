
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.mixins import TimestampMixin, UUIDPk
from app.core.db import Base


class User(Base, UUIDPk, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Superadmin: manages every business's subscription + views platform-wide
    # usage/cost stats. There's no self-serve signup (plan: superadmin creates
    # every business account), so this is the only role split that exists.
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
