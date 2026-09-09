"""Superadmin business logic: onboarding businesses, managing their
subscription, and the platform-wide usage/cost/revenue numbers behind the
superadmin dashboard."""
import datetime as dt
import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AiUsageLog, Payment
from app.auth import service as auth_service
from app.auth.models import User
from app.businesses.models import Business
from app.core.config import get_settings

_OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"


def _month_start(now: dt.datetime) -> dt.datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _day_start(now: dt.datetime) -> dt.datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def create_business(
    db: AsyncSession, email: str, password: str, business_name: str, trial_days: int
) -> User:
    return await auth_service.signup(db, email, password, business_name, trial_days=trial_days)


def _to_out(business: Business, owner_email: str, cost_last_30d: float, now: dt.datetime) -> dict:
    return {
        "id": business.id,
        "name": business.name,
        "owner_email": owner_email,
        "ai_enabled": business.ai_enabled,
        "subscription_expires_at": business.subscription_expires_at,
        "subscription_active": bool(
            business.subscription_expires_at and business.subscription_expires_at > now
        ),
        "created_at": business.created_at,
        "cost_last_30d_usd": cost_last_30d,
    }


async def _cost_since(db: AsyncSession, since: dt.datetime, business_id: uuid.UUID | None = None) -> dict:
    """business_id -> cost_usd over [since, now)."""
    stmt = select(AiUsageLog.business_id, func.coalesce(func.sum(AiUsageLog.cost_usd), 0)).where(
        AiUsageLog.created_at >= since
    )
    if business_id is not None:
        stmt = stmt.where(AiUsageLog.business_id == business_id)
    rows = (await db.execute(stmt.group_by(AiUsageLog.business_id))).all()
    return {bid: float(cost) for bid, cost in rows}


async def list_businesses(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(Business, User.email)
            .join(User, User.id == Business.owner_user_id)
            .where(Business.deleted_at.is_(None))
            .order_by(Business.created_at.desc())
        )
    ).all()

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    cost_by_business = await _cost_since(db, since)
    now = dt.datetime.now(dt.timezone.utc)

    return [
        _to_out(business, owner_email, cost_by_business.get(business.id, 0.0), now)
        for business, owner_email in rows
    ]


async def get_business_detail(db: AsyncSession, business_id: uuid.UUID) -> dict | None:
    row = (
        await db.execute(
            select(Business, User.email)
            .join(User, User.id == Business.owner_user_id)
            .where(Business.id == business_id)
        )
    ).first()
    if row is None:
        return None
    business, owner_email = row

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    cost_by_business = await _cost_since(db, since, business_id)
    now = dt.datetime.now(dt.timezone.utc)
    return _to_out(business, owner_email, cost_by_business.get(business.id, 0.0), now)


async def get_business_or_404(db: AsyncSession, business_id: uuid.UUID) -> Business | None:
    return await db.get(Business, business_id)


async def extend_subscription(
    db: AsyncSession,
    business: Business,
    admin_user_id: uuid.UUID,
    new_expires_at: dt.datetime,
    payment_amount: float | None,
    payment_currency: str,
    payment_note: str | None,
) -> Business:
    business.subscription_expires_at = new_expires_at
    if payment_amount:
        db.add(
            Payment(
                business_id=business.id,
                recorded_by_user_id=admin_user_id,
                amount=payment_amount,
                currency=payment_currency.upper(),
                note=payment_note,
            )
        )
    await db.commit()
    await db.refresh(business)
    return business


async def set_ai_enabled(db: AsyncSession, business: Business, ai_enabled: bool) -> Business:
    business.ai_enabled = ai_enabled
    await db.commit()
    await db.refresh(business)
    return business


async def soft_delete_business(db: AsyncSession, business: Business) -> None:
    """Marks the business (and its owner's access) deleted without touching
    any row a lead/conversation/usage-log foreign key points at — see
    Business.deleted_at."""
    business.deleted_at = dt.datetime.now(dt.timezone.utc)
    business.ai_enabled = False
    await db.commit()


async def get_openrouter_balance() -> tuple[float | None, float | None]:
    """Live balance from OpenRouter's own account API — no local ledger to
    keep in sync. Returns (remaining_usd, limit_usd); (None, None) on any
    failure (no key configured, network error, ...) so the dashboard just
    shows "unavailable" instead of a broken page."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        return None, None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                _OPENROUTER_CREDITS_URL,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
    except (httpx.HTTPError, KeyError, ValueError):
        return None, None

    total_credits = data.get("total_credits")
    total_usage = data.get("total_usage")
    if total_credits is None or total_usage is None:
        return None, None
    return float(total_credits) - float(total_usage), float(total_credits)


async def get_stats(db: AsyncSession) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    today_start = _day_start(now)
    month_start = _month_start(now)

    total_businesses = await db.scalar(
        select(func.count()).select_from(Business).where(Business.deleted_at.is_(None))
    )
    active_businesses = await db.scalar(
        select(func.count())
        .select_from(Business)
        .where(Business.deleted_at.is_(None), Business.subscription_expires_at > now)
    )

    tokens_today = await db.scalar(
        select(func.coalesce(func.sum(AiUsageLog.total_tokens), 0)).where(
            AiUsageLog.created_at >= today_start
        )
    )
    cost_today = await db.scalar(
        select(func.coalesce(func.sum(AiUsageLog.cost_usd), 0)).where(
            AiUsageLog.created_at >= today_start
        )
    )
    cost_this_month = await db.scalar(
        select(func.coalesce(func.sum(AiUsageLog.cost_usd), 0)).where(
            AiUsageLog.created_at >= month_start
        )
    )

    income_rows = (
        await db.execute(
            select(Payment.currency, func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.created_at >= month_start)
            .group_by(Payment.currency)
        )
    ).all()
    income_this_month = {currency: float(total) for currency, total in income_rows}

    balance_usd, limit_usd = await get_openrouter_balance()

    return {
        "total_businesses": total_businesses or 0,
        "active_businesses": active_businesses or 0,
        "expired_businesses": (total_businesses or 0) - (active_businesses or 0),
        "tokens_today": tokens_today or 0,
        "cost_today_usd": float(cost_today or 0),
        "cost_this_month_usd": float(cost_this_month or 0),
        "income_this_month": income_this_month,
        "openrouter_balance_usd": balance_usd,
        "openrouter_limit_usd": limit_usd,
    }


async def get_usage_timeseries(db: AsyncSession, days: int) -> list[dict]:
    since = _day_start(dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=days - 1)
    day = func.date_trunc("day", AiUsageLog.created_at)
    rows = (
        await db.execute(
            select(day, func.sum(AiUsageLog.total_tokens), func.sum(AiUsageLog.cost_usd))
            .where(AiUsageLog.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
    ).all()
    by_day = {d.date(): (int(tokens), float(cost)) for d, tokens, cost in rows}

    out = []
    for i in range(days):
        d = (since + dt.timedelta(days=i)).date()
        tokens, cost = by_day.get(d, (0, 0.0))
        out.append({"date": d, "tokens": tokens, "cost_usd": cost})
    return out


async def get_revenue_timeseries(db: AsyncSession, months: int) -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    first_month = _month_start(now)
    for _ in range(months - 1):
        first_month = _month_start(first_month - dt.timedelta(days=1))

    month = func.date_trunc("month", Payment.created_at)
    income_rows = (
        await db.execute(
            select(month, Payment.currency, func.sum(Payment.amount))
            .where(Payment.created_at >= first_month)
            .group_by(month, Payment.currency)
        )
    ).all()
    income_by_month: dict[str, dict[str, float]] = {}
    for m, currency, total in income_rows:
        key = m.strftime("%Y-%m")
        income_by_month.setdefault(key, {})[currency] = float(total)

    expense_month = func.date_trunc("month", AiUsageLog.created_at)
    expense_rows = (
        await db.execute(
            select(expense_month, func.sum(AiUsageLog.cost_usd))
            .where(AiUsageLog.created_at >= first_month)
            .group_by(expense_month)
        )
    ).all()
    expense_by_month = {m.strftime("%Y-%m"): float(cost) for m, cost in expense_rows}

    out = []
    cursor = first_month
    for _ in range(months):
        key = cursor.strftime("%Y-%m")
        out.append(
            {
                "period": key,
                "income": income_by_month.get(key, {}),
                "expense_usd": expense_by_month.get(key, 0.0),
            }
        )
        # advance one calendar month
        next_month = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        cursor = next_month
    return out
