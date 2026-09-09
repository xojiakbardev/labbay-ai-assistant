from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.businesses.schemas import BusinessOut, BusinessUpdate
from app.common.tenancy import get_current_business
from app.core.db import get_db

router = APIRouter(prefix="/business", tags=["business"])


@router.get("", response_model=BusinessOut)
async def get_business(business: Business = Depends(get_current_business)) -> Business:
    return business


@router.patch("", response_model=BusinessOut)
async def update_business(
    body: BusinessUpdate,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
) -> Business:
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "ui_preferences" and isinstance(value, dict) and isinstance(business.ui_preferences, dict):
            merged = dict(business.ui_preferences)
            merged.update(value)
            setattr(business, field, merged)
        else:
            setattr(business, field, value)
    await db.commit()
    await db.refresh(business)
    return business
