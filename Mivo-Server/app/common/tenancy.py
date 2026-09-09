"""Central tenant-scoping dependencies.

Every router except /auth/* and the webhook endpoints depends on
`get_current_business` here, so business_id scoping is enforced in exactly one
place instead of being re-implemented (and potentially forgotten) per endpoint
— plan §13.
"""
import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.businesses.models import Business
from app.core.db import get_db
from app.core.security import decode_token

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")

    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.") from exc

    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type.")

    user = await db.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists.")
    return user


async def get_current_superadmin(user: User = Depends(get_current_user)) -> User:
    """Gate for every /superadmin/* route. Deliberately re-reads `user` fresh
    per request (via get_current_user, not a JWT claim) — revoking someone's
    is_superadmin flag takes effect on their very next request, not just
    after their token expires."""
    if not user.is_superadmin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superadmin access required.")
    return user


async def get_current_business(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Business:
    """Resolves the caller's own business. Every business-owned query in every
    router should filter by this ID — never by a client-supplied business_id."""
    business = await db.scalar(select(Business).where(Business.owner_user_id == user.id))
    if business is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No business found for this account.")
    return business
