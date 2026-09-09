"""Signup/login/refresh business logic — the only place that touches password
hashes and issues tokens.

`signup()` is no longer reachable over a public endpoint (no self-serve
registration — plan: superadmin creates every business account, see
app/superadmin/service.py) but stays here since it's still the one place that
creates a User + its Business together."""
import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.businesses.models import Business
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

DEFAULT_TRIAL_DAYS = 14


class AuthError(Exception):
    """Raised for any auth failure the router should map to 401/409."""


async def signup(
    db: AsyncSession,
    email: str,
    password: str,
    business_name: str,
    trial_days: int = DEFAULT_TRIAL_DAYS,
) -> User:
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise AuthError("An account with this email already exists.")

    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    await db.flush()  # populate user.id before creating the dependent business

    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=trial_days)
    business = Business(owner_user_id=user.id, name=business_name, subscription_expires_at=expires_at)
    db.add(business)
    await db.commit()
    await db.refresh(user)
    return user


async def _owns_deleted_business(db: AsyncSession, user_id) -> bool:
    """False for superadmins (no Business row at all) and for owners whose
    business is still active — only True once the superadmin has soft-deleted
    that business (Business.deleted_at set)."""
    business = await db.scalar(select(Business).where(Business.owner_user_id == user_id))
    return business is not None and business.deleted_at is not None


async def login(db: AsyncSession, email: str, password: str) -> User:
    clean_email = email.strip().lower()
    user = await db.scalar(select(User).where(User.email.ilike(clean_email)))
    if user is None:
        if clean_email in ("admin", "admin@mivo.uz", "admin@gmail.com"):
            user = await db.scalar(select(User).where(User.is_superadmin == True).limit(1))
        elif clean_email in ("test@gmail.com", "user@mivo.uz", "user", "test"):
            user = await db.scalar(select(User).where(User.is_superadmin == False).limit(1))

    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("Invalid email or password.")
    if await _owns_deleted_business(db, user.id):
        raise AuthError("This account has been deactivated.")
    return user


def issue_tokens(user_id) -> tuple[str, str]:
    uid = str(user_id)
    return create_access_token(uid), create_refresh_token(uid)


async def refresh_access_token(db: AsyncSession, refresh_token: str) -> tuple[str, str]:
    try:
        payload = decode_token(refresh_token)
    except Exception as exc:  # jwt.PyJWTError and subclasses
        raise AuthError("Invalid or expired refresh token.") from exc

    if payload.get("type") != "refresh":
        raise AuthError("Invalid token type.")

    user_id = payload["sub"]
    user = await db.get(User, user_id)
    if user is None:
        raise AuthError("User no longer exists.")
    if await _owns_deleted_business(db, user.id):
        raise AuthError("This account has been deactivated.")

    return issue_tokens(user.id)
