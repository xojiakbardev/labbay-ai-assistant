import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service as auth_service
from app.auth.models import User
from app.auth.schemas import TokenResponse
from app.billing.models import Plan
from app.billing.schemas import PlanCreate, PlanOut, PlanUpdate
from app.common.tenancy import get_current_superadmin
from app.core.db import get_db
from app.superadmin import service
from app.superadmin.schemas import (
    BusinessAiSuspendRequest,
    CreateBusinessRequest,
    ExtendSubscriptionRequest,
    RevenuePoint,
    StatsOut,
    SuperadminBusinessOut,
    UsagePoint,
)

router = APIRouter(prefix="/superadmin", tags=["superadmin"])


@router.post("/businesses", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def create_business(
    body: CreateBusinessRequest,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_superadmin),
) -> TokenResponse:
    """Onboards a business owner. Returns tokens purely as a convenience (e.g.
    to hand the owner a working session immediately) — the superadmin's own
    session is untouched."""
    try:
        user = await service.create_business(db, body.email, body.password, body.business_name, body.trial_days)
    except auth_service.AuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    access, refresh = await auth_service.issue_tokens(db, user.id)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.get("/businesses", response_model=list[SuperadminBusinessOut])
async def list_businesses(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_superadmin),
) -> list[dict]:
    return await service.list_businesses(db)


@router.patch("/businesses/{business_id}/subscription", response_model=SuperadminBusinessOut)
async def extend_subscription(
    business_id: uuid.UUID,
    body: ExtendSubscriptionRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_superadmin),
) -> dict:
    business = await service.get_business_or_404(db, business_id)
    if business is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Business not found.")
    change_plan = "plan_id" in body.model_fields_set
    if change_plan and body.plan_id is not None and await db.get(Plan, body.plan_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
    await service.extend_subscription(
        db,
        business,
        admin.id,
        body.subscription_expires_at,
        body.payment_amount,
        body.payment_currency,
        body.payment_note,
        plan_id=body.plan_id,
        change_plan=change_plan,
    )
    return await service.get_business_detail(db, business_id)


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_superadmin),
) -> list[dict]:
    return await service.list_plans(db)


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(
    body: PlanCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_superadmin),
) -> dict:
    return await service.create_plan(db, body)


@router.patch("/plans/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: uuid.UUID,
    body: PlanUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_superadmin),
) -> dict:
    """A plan is never deleted (businesses and payments point at it) —
    switch it off with is_active instead."""
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
    return await service.update_plan(db, plan, body)


@router.patch("/businesses/{business_id}/ai", response_model=SuperadminBusinessOut)
async def suspend_ai(
    business_id: uuid.UUID,
    body: BusinessAiSuspendRequest,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_superadmin),
) -> dict:
    business = await service.get_business_or_404(db, business_id)
    if business is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Business not found.")
    await service.set_ai_suspended(db, business, body.ai_suspended)
    return await service.get_business_detail(db, business_id)


@router.delete("/businesses/{business_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_business(
    business_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_superadmin),
) -> None:
    """Soft delete — see Business.deleted_at. Blocks the owner's login and AI
    auto-replies; existing leads/conversations/usage history are untouched."""
    business = await service.get_business_or_404(db, business_id)
    if business is None or business.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Business not found.")
    await service.soft_delete_business(db, business)


@router.get("/stats", response_model=StatsOut)
async def stats(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_superadmin),
) -> dict:
    return await service.get_stats(db)


@router.get("/usage-timeseries", response_model=list[UsagePoint])
async def usage_timeseries(
    days: int = Query(default=30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_superadmin),
) -> list[dict]:
    return await service.get_usage_timeseries(db, days)


@router.get("/revenue-timeseries", response_model=list[RevenuePoint])
async def revenue_timeseries(
    months: int = Query(default=6, ge=1, le=24),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_superadmin),
) -> list[dict]:
    return await service.get_revenue_timeseries(db, months)
