import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import service
from app.billing.schemas import PlanOut, UsageOut
from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.core.config import get_settings
from app.core.db import get_db

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/usage", response_model=UsageOut)
async def get_usage(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
) -> UsageOut:
    """The owner's plan and this month's AI replies."""
    current = await service.usage(db, business)
    expires = business.subscription_expires_at
    return UsageOut(
        plan=PlanOut.model_validate(current.plan) if current.plan else None,
        period_start=current.period_start,
        period_end=current.period_end,
        ai_replies_used=current.used,
        ai_replies_limit=current.limit,
        ai_replies_hard_limit=current.hard_limit,
        subscription_expires_at=expires,
        subscription_active=bool(expires and expires > dt.datetime.now(dt.timezone.utc)),
        billing_contact=get_settings().billing_contact,
    )
