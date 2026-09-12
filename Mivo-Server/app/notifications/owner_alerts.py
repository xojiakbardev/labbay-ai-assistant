"""Telling the business owner something needs them — one place for every
channel (dashboard notification + SSE, web push, Telegram).

Each channel is independent: Telegram being down must not stop the dashboard
notification, and a push failure must not stop Telegram. A failed channel is
logged and recorded, never raised into the customer pipeline that called it —
by the time an alert fires, the customer-facing work is already done.

A failed channel rolls the session back, which expires every loaded object;
reading an attribute after that would be a lazy load, which async SQLAlchemy
refuses. So everything an alert needs is read into plain values first, and the
one channel that needs ORM objects (the hot-lead Telegram template) re-fetches
them.
"""
import html
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.core.config import get_settings
from app.customers.models import Customer
from app.leads.models import Lead
from app.leads.notifications import get_telegram_client, notify_hot_lead
from app.leads.service import mark_hot_notified
from app.notifications.service import create_notification
from app.push.service import send_push_notification
from app.telegram.models import TelegramConnection

logger = logging.getLogger("app.notifications.owner_alerts")


def customer_label(customer: Customer | None) -> str:
    if customer is None:
        return "Instagram foydalanuvchisi"
    if customer.username:
        return f"@{customer.username.lstrip('@')}"
    return customer.name or "Instagram foydalanuvchisi"


async def _telegram(db: AsyncSession, business_id: uuid.UUID, text: str, url: str) -> None:
    chat_id = await db.scalar(
        select(TelegramConnection.telegram_chat_id).where(
            TelegramConnection.business_id == business_id,
            TelegramConnection.telegram_chat_id.is_not(None),
        )
    )
    if chat_id is None:
        return
    await get_telegram_client().send_message(
        chat_id,
        text,
        reply_markup={"inline_keyboard": [[{"text": "💬 Suhbatni ochish", "url": url}]]},
    )


async def alert_owner(
    db: AsyncSession,
    business: Business,
    *,
    type: str,
    title: str,
    message: str,
    customer: Customer | None = None,
    conversation_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
) -> None:
    """Dashboard + push + Telegram for a situation that needs a human."""
    business_id = business.id
    customer_id = customer.id if customer else None
    frontend = get_settings().frontend_url
    path = f"/?id={conversation_id}" if conversation_id else "/"

    try:
        await create_notification(
            db,
            business_id=business_id,
            type=type,
            title=title,
            message=message,
            lead_id=lead_id,
            customer_id=customer_id,
            extra_metadata={"conversation_id": str(conversation_id) if conversation_id else None},
        )
    except Exception:  # noqa: BLE001 — see module docstring
        logger.exception("[alerts] dashboard notification failed for business %s", business_id)
        await db.rollback()

    try:
        await send_push_notification(
            db, business_id=business_id, title=title, body=message, url=path, tag=f"{type}-{conversation_id}"
        )
    except Exception:  # noqa: BLE001
        logger.exception("[alerts] web push failed for business %s", business_id)
        await db.rollback()

    try:
        await _telegram(db, business_id, f"<b>{html.escape(title)}</b>\n\n{html.escape(message)}", f"{frontend}{path}")
    except Exception:  # noqa: BLE001
        logger.exception("[alerts] telegram alert failed for business %s", business_id)
        await db.rollback()


async def alert_hot_lead(db: AsyncSession, business: Business, customer: Customer, lead: Lead) -> None:
    """The one alert with its own Telegram template and once-per-HOT gating
    (hot_notified_at)."""
    business_id = business.id
    lead_id = lead.id
    label = customer_label(customer)
    phone_value = lead.phone
    phone = f" • {phone_value}" if phone_value else ""
    summary = f": {lead.summary}" if lead.summary else ""
    metadata = {
        "score": lead.score,
        "phone": phone_value,
        "username": customer.username if customer else None,
        "products": [p.get("name") for p in (lead.interested_products or []) if isinstance(p, dict)],
    }
    lead_customer_id = lead.customer_id

    try:
        await create_notification(
            db,
            business_id=business_id,
            type="lead_hot",
            title="Yangi Issiq Lid 🔥",
            message=f"{label}{phone}{summary}",
            lead_id=lead_id,
            customer_id=lead_customer_id,
            extra_metadata=metadata,
        )
    except Exception:  # noqa: BLE001
        logger.exception("[alerts] hot-lead dashboard notification failed for lead %s", lead_id)
        await db.rollback()

    try:
        await send_push_notification(
            db,
            business_id=business_id,
            title="Yangi Issiq Lid 🔥",
            body=f"{label}{phone}",
            url=f"/leads?id={lead_id}",
            tag=f"lead-hot-{lead_id}",
            data={"lead_id": str(lead_id)},
        )
    except Exception:  # noqa: BLE001
        logger.exception("[alerts] hot-lead web push failed for lead %s", lead_id)
        await db.rollback()

    try:
        fresh_business = await db.get(Business, business_id, populate_existing=True)
        fresh_lead = await db.get(Lead, lead_id, populate_existing=True)
        if fresh_business is None or fresh_lead is None:
            return
        sent = await notify_hot_lead(db, fresh_business, fresh_lead)
        if sent:
            await mark_hot_notified(db, fresh_lead)
        else:
            await db.commit()  # persists last_notification_error, if any
    except Exception:  # noqa: BLE001
        logger.exception("[alerts] telegram hot-lead notification failed for lead %s", lead_id)
        await db.rollback()
