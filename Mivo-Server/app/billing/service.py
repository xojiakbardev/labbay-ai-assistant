"""Plans and the monthly AI-reply count behind them. A reply is one AI turn on
Instagram (however many parts it's split into); the sandbox, follow-ups and
ready-made lines don't count."""
import datetime as dt
import logging
import math
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import AiReplyUsage, Plan
from app.businesses.models import Business
from app.core.config import get_settings

logger = logging.getLogger("app.billing")

_settings = get_settings()
_TZ = dt.timezone(dt.timedelta(hours=_settings.billing_utc_offset_hours))


def billing_period(now: dt.datetime | None = None) -> tuple[dt.date, dt.date]:
    """(first day of this month, first day of the next) in the billing zone."""
    today = (now or dt.datetime.now(dt.timezone.utc)).astimezone(_TZ).date()
    start = today.replace(day=1)
    end = (start + dt.timedelta(days=32)).replace(day=1)
    return start, end


@dataclass
class Usage:
    plan: Plan | None
    used: int
    period_start: dt.date
    period_end: dt.date

    @property
    def limit(self) -> int | None:
        return self.plan.monthly_ai_replies if self.plan is not None else None

    @property
    def hard_limit(self) -> int | None:
        """Where the AI stops: the limit plus the grace."""
        if self.limit is None:
            return None
        return self.limit + math.floor(self.limit * _settings.plan_grace_fraction)

    @property
    def warn_at(self) -> int | None:
        if self.limit is None:
            return None
        return max(1, math.ceil(self.limit * _settings.plan_warn_fraction))

    @property
    def exhausted(self) -> bool:
        return self.hard_limit is not None and self.used >= self.hard_limit


async def usage(db: AsyncSession, business: Business, now: dt.datetime | None = None) -> Usage:
    start, end = billing_period(now)
    plan = await db.get(Plan, business.plan_id) if business.plan_id else None
    used = await db.scalar(
        select(AiReplyUsage.replies).where(
            AiReplyUsage.business_id == business.id, AiReplyUsage.period_start == start
        )
    )
    return Usage(plan=plan, used=int(used or 0), period_start=start, period_end=end)


async def usage_by_business(db: AsyncSession) -> dict[uuid.UUID, int]:
    """This month's replies for every business that has any."""
    start, _ = billing_period()
    rows = await db.execute(
        select(AiReplyUsage.business_id, AiReplyUsage.replies).where(AiReplyUsage.period_start == start)
    )
    return {business_id: replies for business_id, replies in rows.all()}


async def record_ai_reply(db: AsyncSession, business: Business) -> Usage:
    """Counts one AI reply; the usage right after it (exact even with other
    conversations counting at the same time). Commits."""
    start, end = billing_period()
    total = (
        await db.execute(
            insert(AiReplyUsage)
            .values(business_id=business.id, period_start=start, replies=1)
            .on_conflict_do_update(
                index_elements=[AiReplyUsage.business_id, AiReplyUsage.period_start],
                set_={"replies": AiReplyUsage.replies + 1},
            )
            .returning(AiReplyUsage.replies)
        )
    ).scalar_one()
    plan = await db.get(Plan, business.plan_id) if business.plan_id else None
    await db.commit()
    return Usage(plan=plan, used=total, period_start=start, period_end=end)


def limit_reason(current: Usage) -> str | None:
    """Shown to the owner when the plan stops the AI."""
    if not current.exhausted:
        return None
    return (
        f"Oylik tarif limiti tugadi ({current.used} / {current.limit} AI javob). "
        "Suhbatlar operatorga o'tkazildi — tarifni uzaytirish yoki oshirish kerak."
    )


def crossing_alert(current: Usage) -> tuple[str, str] | None:
    """(title, message) when this reply just crossed a threshold, else None."""
    if current.limit is None:
        return None
    used, limit, hard = current.used, current.limit, current.hard_limit
    if used == hard:
        return (
            "AI to'xtadi — tarif limiti ⛔",
            f"Bu oy {used} ta AI javob berildi (limit {limit}). Yangi xabarlar operatorga o'tadi, "
            "tarifni oshirsangiz AI darhol qayta ishlaydi.",
        )
    if used == limit:
        return (
            "Tarif limiti tugadi ⚠️",
            f"Bu oy {used} / {limit} AI javob. Yana {hard - limit} ta javobdan keyin AI to'xtaydi.",
        )
    if used == current.warn_at and used < limit:
        return (
            "Tarif limiti yaqin 📊",
            f"Bu oy {used} / {limit} AI javob ishlatildi.",
        )
    return None


async def default_plan(db: AsyncSession) -> Plan | None:
    return await db.scalar(select(Plan).where(Plan.is_default.is_(True), Plan.is_active.is_(True)))


async def make_default(db: AsyncSession, plan: Plan) -> None:
    """Only one plan is the default; setting one clears the others."""
    await db.execute(update(Plan).where(Plan.id != plan.id, Plan.is_default.is_(True)).values(is_default=False))
    plan.is_default = True
