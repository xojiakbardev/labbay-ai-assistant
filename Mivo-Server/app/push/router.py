from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.businesses.models import Business
from app.common.tenancy import get_current_business, get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.push import service
from app.push.schemas import (
    PushStatusOut,
    PushSubscribeIn,
    PushTestOut,
    PushUnsubscribeIn,
    VapidPublicKeyOut,
)

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key", response_model=VapidPublicKeyOut)
async def get_vapid_public_key(
    _user: User = Depends(get_current_user),
):
    """Returns the VAPID public key needed by the browser to create a PushSubscription."""
    settings = get_settings()
    return VapidPublicKeyOut(public_key=settings.vapid_public_key)


@router.get("/status", response_model=PushStatusOut)
async def get_push_status(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Returns whether the business has active push subscriptions and device count."""
    subs = await service.list_subscriptions(db, business.id)
    return PushStatusOut(subscribed=len(subs) > 0, devices_count=len(subs))


@router.post("/subscribe", response_model=PushStatusOut)
async def subscribe_push(
    body: PushSubscribeIn,
    user: User = Depends(get_current_user),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Saves or updates a Web Push subscription for the authenticated user and business."""
    settings = get_settings()
    if not settings.vapid_public_key or not settings.vapid_private_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Push notifications are not configured.")
    await service.save_subscription(
        db=db,
        business_id=business.id,
        user_id=user.id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=body.user_agent,
    )
    subs = await service.list_subscriptions(db, business.id)
    return PushStatusOut(subscribed=True, devices_count=len(subs))


@router.post("/unsubscribe", response_model=PushStatusOut)
async def unsubscribe_push(
    body: PushUnsubscribeIn,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Removes a Web Push subscription for this business."""
    await service.delete_subscription(db, business.id, body.endpoint)
    subs = await service.list_subscriptions(db, business.id)
    return PushStatusOut(subscribed=len(subs) > 0, devices_count=len(subs))


@router.post("/test", response_model=PushTestOut)
async def test_push_notification(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Sends a test Web Push notification to all active devices of the business."""
    sent = await service.send_push_notification(
        db=db,
        business_id=business.id,
        title="Mivo AI — Test Xabarnoma 🔔",
        body="Brauzer push bildirishnomalari muvaffaqiyatli ulandi!",
        url="/leads",
        tag="mivo-test-push",
    )
    return PushTestOut(sent_count=sent)
