"""Signup/login/refresh/logout business logic — the only place that touches
password hashes and issues tokens.

`signup()` is no longer reachable over a public endpoint (no self-serve
registration — plan: superadmin creates every business account, see
app/superadmin/service.py) but stays here since it's still the one place that
creates a User + its Business together."""
import datetime as dt
import uuid

import jwt
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import RefreshToken, User
from app.businesses.models import Business
from app.core.config import get_settings
from app.core.rate_limit import LoginThrottle
from app.core.security import (
    TOKEN_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password_async,
)

# A rotated refresh token presented again within this window is treated as a
# benign race (two tabs refreshing at once, a retried request whose response
# was lost) — refused, but without revoking the user's other sessions.
REUSE_GRACE = dt.timedelta(seconds=30)

DEFAULT_TRIAL_DAYS = 14

_settings = get_settings()
login_throttle = LoginThrottle(
    _settings.login_max_failures, dt.timedelta(minutes=_settings.login_failure_window_minutes)
)


class AuthError(Exception):
    """Raised for any auth failure the router should map to 401/409."""


class LoginThrottled(AuthError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many failed attempts. Try again later.")
        self.retry_after = retry_after


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def signup(
    db: AsyncSession,
    email: str,
    password: str,
    business_name: str,
    trial_days: int = DEFAULT_TRIAL_DAYS,
) -> User:
    clean_email = normalize_email(email)
    existing = await db.scalar(select(User).where(func.lower(User.email) == clean_email))
    if existing is not None:
        raise AuthError("An account with this email already exists.")

    user = User(email=clean_email, password_hash=hash_password(password))
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
    clean_email = normalize_email(email)
    retry_after = login_throttle.retry_after_seconds(clean_email)
    if retry_after is not None:
        raise LoginThrottled(retry_after)

    # Exact (case-insensitive) match only. `ilike` treated % and _ in the
    # submitted "email" as wildcards, so "%@gmail.com" matched real accounts.
    user = await db.scalar(select(User).where(func.lower(User.email) == clean_email))

    # bcrypt runs even when the user doesn't exist, so a wrong email and a
    # wrong password take the same time; it runs off the event loop.
    password_ok = await verify_password_async(password, user.password_hash if user else None)
    if user is None or not password_ok:
        login_throttle.record_failure(clean_email)
        raise AuthError("Invalid email or password.")
    if await _owns_deleted_business(db, user.id):
        raise AuthError("This account has been deactivated.")

    login_throttle.reset(clean_email)
    return user


async def issue_tokens(db: AsyncSession, user_id: uuid.UUID) -> tuple[str, str]:
    """Access + refresh pair. The refresh token's jti is recorded so it can be
    rotated and revoked; commits."""
    uid = str(user_id)
    refresh, jti, expires_at = create_refresh_token(uid)
    db.add(RefreshToken(id=jti, user_id=user_id, expires_at=expires_at))
    await db.commit()
    return create_access_token(uid), refresh


async def revoke_all_refresh_tokens(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Ends every session of this user at the next refresh (access tokens run
    out on their own within ACCESS_TOKEN_EXPIRE_MINUTES). Does not commit."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=dt.datetime.now(dt.timezone.utc))
    )


async def _load_refresh_row(db: AsyncSession, refresh_token: str) -> tuple[dict, RefreshToken]:
    try:
        payload = decode_token(refresh_token, TOKEN_REFRESH)
        jti = uuid.UUID(payload["jti"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise AuthError("Invalid or expired refresh token.") from exc

    row = await db.scalar(select(RefreshToken).where(RefreshToken.id == jti).with_for_update())
    if row is None or str(row.user_id) != payload["sub"]:
        raise AuthError("Invalid or expired refresh token.")
    return payload, row


async def refresh_access_token(db: AsyncSession, refresh_token: str) -> tuple[str, str]:
    """Rotates: the presented refresh token is revoked and a new pair issued.

    Presenting a token that was already rotated means it was copied — the
    legitimate client would be holding its replacement — so every refresh
    token of that user is revoked and both sides must log in again.
    """
    _payload, row = await _load_refresh_row(db, refresh_token)
    now = dt.datetime.now(dt.timezone.utc)

    if row.revoked_at is not None:
        if row.replaced_by is not None and now - row.revoked_at <= REUSE_GRACE:
            raise AuthError("Refresh token was just rotated; use the new one.")
        await revoke_all_refresh_tokens(db, row.user_id)
        await db.commit()
        raise AuthError("Refresh token reuse detected; all sessions were signed out.")
    if row.expires_at <= now:
        raise AuthError("Invalid or expired refresh token.")

    user = await db.get(User, row.user_id)
    if user is None:
        raise AuthError("User no longer exists.")
    if await _owns_deleted_business(db, user.id):
        raise AuthError("This account has been deactivated.")

    uid = str(user.id)
    new_refresh, new_jti, new_expires = create_refresh_token(uid)
    row.revoked_at = now
    row.replaced_by = new_jti
    db.add(RefreshToken(id=new_jti, user_id=user.id, expires_at=new_expires))
    await db.commit()
    return create_access_token(uid), new_refresh


async def logout(db: AsyncSession, refresh_token: str) -> None:
    """Revokes the given refresh token. An invalid/unknown token is an error,
    not a silent success — the client should know its session wasn't ended."""
    _payload, row = await _load_refresh_row(db, refresh_token)
    if row.revoked_at is None:
        row.revoked_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()
