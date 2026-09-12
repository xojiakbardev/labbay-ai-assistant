from fastapi import APIRouter, Depends
from sqlalchemy import cast, func, update
from sqlalchemy.dialects.postgresql import JSONB
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
    changes = body.model_dump(exclude_unset=True)
    preferences = changes.pop("ui_preferences", None)
    for field, value in changes.items():
        setattr(business, field, value)
    if preferences:
        # Merged in the database, key by key: two quick saves from the
        # dashboard (theme, then language) must not overwrite each other the
        # way a read-merge-write in Python would.
        await db.execute(
            update(Business)
            .where(Business.id == business.id)
            .values(ui_preferences=func.coalesce(Business.ui_preferences, cast({}, JSONB)).op("||")(cast(preferences, JSONB)))
        )
    await db.commit()
    await db.refresh(business)
    return business
