"""Superadmin business logic: onboarding businesses, managing their
subscription, and the platform-wide usage/cost/revenue numbers behind the
superadmin dashboard."""
import datetime as dt
import uuid

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AiUsageLog, Payment
from app.auth import service as auth_service
from app.auth.models import User
from app.billing import service as billing
from app.billing.models import Plan
from app.billing.schemas import PlanCreate, PlanUpdate
from app.businesses.models import Business
from app.core.config import get_settings

_OPENROUTER_CREDITS_URL = get_settings().llm_credits_url


def _month_start(now: dt.datetime) -> dt.datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _day_start(now: dt.datetime) -> dt.datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def create_business(
    db: AsyncSession,
    email: str,
    password: str,
    business_name: str,
    days: int,
    plan_id: uuid.UUID | None,
    plan_chosen: bool,
) -> User:
    """The chosen plan (not chosen: the default one) starts today and the
    subscription runs `days` from today."""
    user = await auth_service.signup(db, email, password, business_name, trial_days=days)
    if not plan_chosen:
        default = await billing.default_plan(db)
        plan_id = default.id if default else None
    business = await db.scalar(select(Business).where(Business.owner_user_id == user.id))
    business.plan_id = plan_id
    business.plan_started_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()
    return user


def _to_out(
    business: Business,
    owner_email: str,
    cost_last_30d: float,
    now: dt.datetime,
    plan: Plan | None = None,
    replies_used: int = 0,
) -> dict:
    return {
        "plan_id": business.plan_id,
        "plan_name": plan.name if plan else None,
        "plan_started_at": business.plan_started_at,
        "ai_replies_used": replies_used,
        "ai_replies_limit": plan.monthly_ai_replies if plan else None,
        "usage_period_end": billing.billing_period(business.plan_started_at, now)[1],
        "id": business.id,
        "name": business.name,
        "owner_email": owner_email,
        "ai_enabled": business.ai_enabled,
        "ai_suspended": business.ai_suspended,
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
            select(Business, User.email, Plan)
            .join(User, User.id == Business.owner_user_id)
            .outerjoin(Plan, Plan.id == Business.plan_id)
            .where(Business.deleted_at.is_(None))
            .order_by(Business.created_at.desc())
        )
    ).all()

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    cost_by_business = await _cost_since(db, since)
    replies = await billing.used_by_business(db, [business for business, _, _ in rows])
    now = dt.datetime.now(dt.timezone.utc)

    return [
        _to_out(business, owner_email, cost_by_business.get(business.id, 0.0), now, plan, replies.get(business.id, 0))
        for business, owner_email, plan in rows
    ]


async def get_business_detail(db: AsyncSession, business_id: uuid.UUID) -> dict | None:
    row = (
        await db.execute(
            select(Business, User.email, Plan)
            .join(User, User.id == Business.owner_user_id)
            .outerjoin(Plan, Plan.id == Business.plan_id)
            .where(Business.id == business_id)
        )
    ).first()
    if row is None:
        return None
    business, owner_email, plan = row

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    cost_by_business = await _cost_since(db, since, business_id)
    current = await billing.usage(db, business)
    now = dt.datetime.now(dt.timezone.utc)
    return _to_out(business, owner_email, cost_by_business.get(business.id, 0.0), now, plan, current.used)


async def get_business_or_404(db: AsyncSession, business_id: uuid.UUID) -> Business | None:
    return await db.get(Business, business_id)


async def renew_plan(
    db: AsyncSession,
    business: Business,
    admin_user_id: uuid.UUID,
    plan_id: uuid.UUID | None,
    months: int,
    payment_amount: float | None,
    payment_currency: str,
    payment_note: str | None,
) -> Business:
    """Someone paid: the plan starts again today (billing.start_plan), and the
    payment is recorded."""
    billing.start_plan(business, plan_id, months)
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


async def list_plans(db: AsyncSession) -> list[dict]:
    """Every plan, inactive ones too, with how many businesses are on each."""
    counts = dict(
        (
            await db.execute(
                select(Business.plan_id, func.count())
                .where(Business.deleted_at.is_(None), Business.plan_id.is_not(None))
                .group_by(Business.plan_id)
            )
        ).all()
    )
    plans = (await db.execute(select(Plan).order_by(Plan.sort_order, Plan.price, Plan.name))).scalars().all()
    return [_plan_out(plan, counts.get(plan.id, 0)) for plan in plans]


def _plan_out(plan: Plan, businesses_count: int) -> dict:
    return {
        "id": plan.id, "name": plan.name, "price": float(plan.price), "currency": plan.currency,
        "monthly_ai_replies": plan.monthly_ai_replies, "is_active": plan.is_active,
        "is_default": plan.is_default, "sort_order": plan.sort_order, "businesses_count": businesses_count,
    }


async def create_plan(db: AsyncSession, body: PlanCreate) -> dict:
    fields = body.model_dump()
    make_default = fields.pop("is_default")
    plan = Plan(**fields, is_default=False)
    db.add(plan)
    await db.flush()
    if make_default:
        await billing.make_default(db, plan)
    await db.commit()
    await db.refresh(plan)
    return _plan_out(plan, 0)


async def update_plan(db: AsyncSession, plan: Plan, body: PlanUpdate) -> dict:
    changes = body.model_dump(exclude_unset=True)
    for field in ("name", "price", "currency", "is_active", "sort_order"):
        if changes.get(field) is not None:
            setattr(plan, field, changes[field])
    if "monthly_ai_replies" in changes:
        plan.monthly_ai_replies = changes["monthly_ai_replies"]
    if changes.get("is_default") is True:
        await billing.make_default(db, plan)
    elif changes.get("is_default") is False:
        plan.is_default = False
    await db.commit()
    await db.refresh(plan)
    count = await db.scalar(
        select(func.count()).select_from(Business).where(Business.plan_id == plan.id, Business.deleted_at.is_(None))
    )
    return _plan_out(plan, count or 0)


async def set_ai_suspended(db: AsyncSession, business: Business, ai_suspended: bool) -> Business:
    business.ai_suspended = ai_suspended
    await db.commit()
    await db.refresh(business)
    return business


async def delete_business(db: AsyncSession, business: Business) -> None:
    """Hard delete: the owner's account goes, and with it (ON DELETE CASCADE)
    the business and everything in it — catalog, customers, conversations,
    leads, integrations. Payments and AI cost logs stay for the platform's
    books, detached from it."""
    owner = await db.get(User, business.owner_user_id)
    if owner is not None and owner.is_superadmin:
        await db.delete(business)  # never the platform's own admin account
    else:
        await db.execute(delete(User).where(User.id == business.owner_user_id))
    await db.commit()


async def delete_plan(db: AsyncSession, plan: Plan) -> bool:
    """False, and nothing deleted, while a business is on it."""
    if await db.scalar(select(func.count()).select_from(Business).where(Business.plan_id == plan.id)):
        return False
    await db.delete(plan)
    await db.commit()
    return True


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
