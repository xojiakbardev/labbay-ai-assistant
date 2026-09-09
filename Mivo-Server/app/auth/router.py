from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.models import User
from app.auth.schemas import LoginRequest, RefreshRequest, TokenResponse, UserOut
from app.common.tenancy import get_current_user
from app.core.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

# There is no public /auth/signup — every business account is created by the
# superadmin (see app/superadmin/router.py). Business owners only ever log in
# with the credentials the superadmin gave them.


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        user = await service.login(db, body.email, body.password)
    except service.AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    access, refresh = service.issue_tokens(user.id)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        access, refresh_token = await service.refresh_access_token(db, body.refresh_token)
    except service.AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    return TokenResponse(access_token=access, refresh_token=refresh_token)
