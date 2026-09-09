import asyncio
import json
import uuid
import jwt
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.core.db import get_db
from app.core.security import decode_token
from app.notifications import service
from app.notifications.broadcaster import broadcaster
from app.notifications.schemas import MarkReadAllOut, NotificationOut, UnreadCountOut

router = APIRouter(prefix="/notifications", tags=["notifications"])
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_business_for_sse(
    token: str | None = Query(None),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Business:
    """Authenticates SSE stream requests via Bearer header or ?token= query parameter."""
    raw_jwt = credentials.credentials if credentials else token
    if not raw_jwt:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing authentication token.")

    try:
        payload = decode_token(raw_jwt)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.") from exc

    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type.")

    user = await db.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists.")

    business = await db.scalar(select(Business).where(Business.owner_user_id == user.id))
    if business is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No business found for this account.")
    return business


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """List recent notifications for the authenticated business."""
    return await service.list_notifications(db, business.id, limit=limit, offset=offset)


@router.get("/unread-count", response_model=UnreadCountOut)
async def get_unread_count(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Returns the total number of unread notifications for the business."""
    count = await service.get_unread_count(db, business.id)
    return UnreadCountOut(unread_count=count)


@router.patch("/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: uuid.UUID,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Mark a single notification as read."""
    notification = await service.mark_as_read(db, business.id, notification_id)
    if notification is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found.")
    return notification


@router.post("/read-all", response_model=MarkReadAllOut)
async def mark_all_notifications_read(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Mark all notifications as read for the business."""
    marked = await service.mark_all_as_read(db, business.id)
    return MarkReadAllOut(marked_read=marked)


@router.delete("/read", status_code=status.HTTP_204_NO_CONTENT)
async def delete_read_notifications(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Delete all read notifications for the business."""
    await service.delete_read_notifications(db, business.id)


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: uuid.UUID,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single notification."""
    deleted = await service.delete_notification(db, business.id, notification_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found.")


@router.get("/stream")
async def stream_notifications(
    business: Business = Depends(get_current_business_for_sse),
):
    """Server-Sent Events (SSE) real-time notification stream, strictly scoped to caller's business_id."""
    async def event_generator() -> AsyncGenerator[str, None]:
        # Send initial connection confirmation
        yield "event: connected\ndata: {}\n\n"
        
        subscriber = broadcaster.subscribe(business.id)
        try:
            while True:
                try:
                    # Wait for next notification or send heartbeat ping every 15s
                    event_data = await asyncio.wait_for(subscriber.__anext__(), timeout=15.0)
                    yield f"event: notification\ndata: {json.dumps(event_data)}\n\n"
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                except StopAsyncIteration:
                    break
        finally:
            await subscriber.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
