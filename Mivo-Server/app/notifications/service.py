import datetime as dt
import logging
import uuid
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.notifications.broadcaster import broadcaster
from app.notifications.models import Notification

logger = logging.getLogger("app.notifications.service")


def _to_payload(n: Notification) -> dict:
    return {
        "id": str(n.id),
        "business_id": str(n.business_id),
        "type": n.type,
        "title": n.title,
        "message": n.message,
        "lead_id": str(n.lead_id) if n.lead_id else None,
        "customer_id": str(n.customer_id) if n.customer_id else None,
        "extra_metadata": n.extra_metadata or {},
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat() if n.created_at else dt.datetime.now(dt.timezone.utc).isoformat(),
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


async def create_notification(
    db: AsyncSession,
    business_id: uuid.UUID,
    type: str,
    title: str,
    message: str,
    lead_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    extra_metadata: dict | None = None,
) -> Notification:
    """Creates and persists a tenant-scoped notification and broadcasts it to active dashboard clients."""
    notification = Notification(
        business_id=business_id,
        type=type,
        title=title,
        message=message,
        lead_id=lead_id,
        customer_id=customer_id,
        extra_metadata=extra_metadata or {},
        is_read=False,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)

    # Broadcast event in real-time to active SSE subscribers
    payload = _to_payload(notification)
    try:
        await broadcaster.broadcast(business_id, payload)
    except Exception as exc:
        logger.warning(f"[NotificationService] Broadcaster error for business {business_id}: {exc}")

    return notification


async def list_notifications(
    db: AsyncSession,
    business_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[Notification]:
    """Returns the most recent notifications for the tenant."""
    query = (
        select(Notification)
        .where(Notification.business_id == business_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_unread_count(db: AsyncSession, business_id: uuid.UUID) -> int:
    """Returns the total number of unread notifications for the tenant."""
    query = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.business_id == business_id, Notification.is_read == False)  # noqa: E712
    )
    result = await db.execute(query)
    return result.scalar_one() or 0


async def mark_as_read(
    db: AsyncSession,
    business_id: uuid.UUID,
    notification_id: uuid.UUID,
) -> Notification | None:
    """Marks a single notification as read, ensuring strict tenant isolation."""
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.business_id == business_id,
    )
    result = await db.execute(query)
    notification = result.scalar_one_or_none()
    if notification is None:
        return None

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()
        await db.refresh(notification)

    return notification


async def mark_all_as_read(db: AsyncSession, business_id: uuid.UUID) -> int:
    """Marks all unread notifications for a tenant as read."""
    now = dt.datetime.now(dt.timezone.utc)
    query = (
        update(Notification)
        .where(Notification.business_id == business_id, Notification.is_read == False)  # noqa: E712
        .values(is_read=True, read_at=now)
    )
    result = await db.execute(query)
    await db.commit()
    return result.rowcount or 0


async def delete_notification(
    db: AsyncSession,
    business_id: uuid.UUID,
    notification_id: uuid.UUID,
) -> bool:
    """Deletes a single notification, ensuring strict tenant isolation."""
    query = (
        delete(Notification)
        .where(Notification.id == notification_id, Notification.business_id == business_id)
    )
    result = await db.execute(query)
    await db.commit()
    return bool(result.rowcount and result.rowcount > 0)


async def delete_read_notifications(db: AsyncSession, business_id: uuid.UUID) -> int:
    """Deletes all read notifications for a tenant."""
    query = (
        delete(Notification)
        .where(Notification.business_id == business_id, Notification.is_read == True)  # noqa: E712
    )
    result = await db.execute(query)
    await db.commit()
    return result.rowcount or 0
