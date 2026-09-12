import asyncio
import json
import logging
import uuid
import pywebpush
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.push.models import PushSubscription

logger = logging.getLogger("app.push.service")


async def save_subscription(
    db: AsyncSession,
    business_id: uuid.UUID,
    user_id: uuid.UUID,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None = None,
) -> PushSubscription:
    """Saves or updates a Web Push subscription for a business/user.

    An endpoint is a capability URL the push service issued to one browser;
    whoever presents it is logged in on that browser now. So an existing row
    moves to the presenting business — that's the same device changing hands
    (one owner logged out, another logged in), and the previous owner's alerts
    must stop going to it."""
    query = select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    result = await db.execute(query)
    sub = result.scalar_one_or_none()

    if sub is None:
        sub = PushSubscription(
            business_id=business_id,
            user_id=user_id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=user_agent,
        )
        db.add(sub)
    else:
        sub.business_id = business_id
        sub.user_id = user_id
        sub.p256dh = p256dh
        sub.auth = auth
        sub.user_agent = user_agent

    await db.commit()
    await db.refresh(sub)
    return sub


async def delete_subscription(
    db: AsyncSession,
    business_id: uuid.UUID,
    endpoint: str,
) -> bool:
    """Removes a subscription for the tenant."""
    query = delete(PushSubscription).where(
        PushSubscription.business_id == business_id,
        PushSubscription.endpoint == endpoint,
    )
    result = await db.execute(query)
    await db.commit()
    return (result.rowcount or 0) > 0


async def list_subscriptions(
    db: AsyncSession,
    business_id: uuid.UUID,
) -> list[PushSubscription]:
    """Lists all active subscriptions for the business."""
    query = select(PushSubscription).where(PushSubscription.business_id == business_id)
    result = await db.execute(query)
    return list(result.scalars().all())


def _sync_send_webpush(
    subscription_info: dict,
    data_str: str,
    vapid_private_key: str,
    vapid_claims: dict,
) -> None:
    pywebpush.webpush(
        subscription_info=subscription_info,
        data=data_str,
        vapid_private_key=vapid_private_key,
        vapid_claims=vapid_claims,
        # Without a timeout a push service that never answers pins a worker
        # thread for good.
        timeout=10,
    )


async def send_push_notification(
    db: AsyncSession,
    business_id: uuid.UUID,
    title: str,
    body: str,
    url: str | None = None,
    tag: str | None = None,
    data: dict | None = None,
) -> int:
    """Sends a Web Push notification to all registered devices of the business.
    Automatically removes dead / expired endpoints (404 or 410).
    Never raises an unhandled error to the caller."""
    settings = get_settings()
    if not settings.vapid_private_key or not settings.vapid_public_key:
        return 0  # push not configured on this server

    subscriptions = await list_subscriptions(db, business_id)
    if not subscriptions:
        return 0

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url or "/leads",
        "tag": tag or "mivo-lead",
        "data": data or {},
    })

    vapid_claims = {"sub": settings.vapid_claims_sub}
    dead_endpoint_ids = []
    sent_count = 0

    for sub in subscriptions:
        sub_info = {
            "endpoint": sub.endpoint,
            "keys": {
                "p256dh": sub.p256dh,
                "auth": sub.auth,
            },
        }
        try:
            await asyncio.to_thread(
                _sync_send_webpush,
                sub_info,
                payload,
                settings.vapid_private_key,
                vapid_claims,
            )
            sent_count += 1
        except pywebpush.WebPushException as exc:
            # 404/410: the browser unregistered. 401/403: the subscription was
            # made with a different VAPID key (e.g. after a key rotation) and
            # can never be delivered to again. Either way it's dead.
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (401, 403, 404, 410):
                logger.info(f"[PushService] Removing expired push subscription {sub.id} (HTTP {status_code})")
                dead_endpoint_ids.append(sub.id)
            else:
                logger.warning(f"[PushService] Push failed for subscription {sub.id}: {exc}")
        except Exception as exc:
            logger.warning(f"[PushService] Unexpected error sending push to {sub.id}: {exc}")

    if dead_endpoint_ids:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead_endpoint_ids)))
        await db.commit()

    return sent_count
