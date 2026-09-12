from fastapi import APIRouter, Depends
from sqlalchemy import cast, func, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.ai.replies import DEFAULT_REPLIES, LANGUAGES, MAX_REPLY_CHARS
from app.businesses.schemas import BusinessOut, BusinessUpdate, ReplyDefaultsOut
from app.common.tenancy import get_current_business
from app.core.db import get_db

router = APIRouter(prefix="/business", tags=["business"])


@router.get("", response_model=BusinessOut)
async def get_business(business: Business = Depends(get_current_business)) -> Business:
    return business


@router.get("/reply-defaults", response_model=ReplyDefaultsOut)
async def get_reply_defaults(business: Business = Depends(get_current_business)) -> ReplyDefaultsOut:
    """The default ready-made replies, shown as placeholders on the AI settings page."""
    return ReplyDefaultsOut(replies=DEFAULT_REPLIES, languages=list(LANGUAGES), max_chars=MAX_REPLY_CHARS)


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
