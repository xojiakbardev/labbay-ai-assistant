import asyncio
import json
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.businesses.models import Business
from app.common.tenancy import business_for_user, get_current_business, get_current_user, user_from_token
from app.core.db import async_session_factory, get_db
from app.core.security import SSE_TICKET_TTL, TOKEN_SSE, create_sse_ticket
from app.notifications import service
from app.notifications.broadcaster import broadcaster
from app.notifications.schemas import MarkReadAllOut, NotificationOut, UnreadCountOut

router = APIRouter(prefix="/notifications", tags=["notifications"])

_HEARTBEAT_SECONDS = 15.0
_REAUTH_EVERY_PINGS = 20  # ~5 minutes


async def _still_active(business_id: uuid.UUID) -> bool:
    async with async_session_factory() as db:
        business = await db.get(Business, business_id)
        return business is not None and business.deleted_at is None


class StreamTicketOut(BaseModel):
    ticket: str
    expires_in: int


@router.post("/stream-ticket", response_model=StreamTicketOut)
async def issue_stream_ticket(
    user: User = Depends(get_current_user),
    _business: Business = Depends(get_current_business),
) -> StreamTicketOut:
    """A one-minute, stream-only credential for EventSource (which can't send
    an Authorization header). The access token never goes into a URL."""
    return StreamTicketOut(ticket=create_sse_ticket(str(user.id)), expires_in=int(SSE_TICKET_TTL.total_seconds()))


async def _business_for_ticket(ticket: str) -> uuid.UUID:
    # Its own short session: a stream stays open for hours, and it must not
    # hold a pooled connection (or a transaction) for all of that.
    async with async_session_factory() as db:
        user = await user_from_token(db, ticket, TOKEN_SSE)
        business = await business_for_user(db, user)
        return business.id


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
async def stream_notifications(ticket: str = Query(..., max_length=2048)):
    """Server-Sent Events stream, scoped to the ticket holder's business."""
    business_id = await _business_for_ticket(ticket)

    async def event_generator() -> AsyncGenerator[str, None]:
        # Registered before the first yield, so nothing broadcast between the
        # handshake and the first wait is missed. Waiting on the queue itself
        # (not on an async generator) is what lets the heartbeat time out
        # without tearing the subscription down — wait_for on a generator's
        # __anext__ cancelled it, and every idle stream died after one ping.
        queue, unsubscribe = broadcaster.register(business_id)
        pings = 0
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    event_data = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    pings += 1
                    # A stream lives for hours: every few minutes, check the
                    # business still may receive events (it may have been
                    # deleted since the ticket was issued).
                    if pings % _REAUTH_EVERY_PINGS == 0 and not await _still_active(business_id):
                        return
                    yield "event: ping\ndata: {}\n\n"
                    continue
                yield f"event: notification\ndata: {json.dumps(event_data, default=str)}\n\n"
        finally:
            unsubscribe()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
