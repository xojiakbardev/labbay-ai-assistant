"""The outbox: every outbound message is stored as pending, then sent and marked
sent/failed; retries resend, echoes are matched."""
import asyncio
import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import (
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_SENT,
    MESSAGE_TYPE_REACTION,
    Conversation,
    Message,
)
from app.core.config import get_settings
from app.instagram.client import MetaAPIError, MetaClient

logger = logging.getLogger("app.conversations.delivery")

_settings = get_settings()
# Pause between the parts of a split reply.
PART_DELAY_SECONDS = _settings.reply_part_delay_seconds
# How long an undelivered reply is still resent / an echo still matched.
RESEND_WINDOW = dt.timedelta(minutes=_settings.resend_window_minutes)


class DeliveryError(Exception):
    """A customer-facing send failed. The message stays recorded as failed.
    `permanent` means retrying can't help (messaging window closed, recipient
    unreachable, token revoked)."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


async def send_outbound(
    db: AsyncSession,
    meta_client: MetaClient,
    *,
    ig_business_id: str,
    access_token: str,
    recipient_id: str,
    messages: list[Message],
) -> None:
    """Sends already-persisted outbound messages in order, committing each
    one's outcome. Raises DeliveryError on the first failure (later messages
    stay pending and go out on the retry, in order)."""
    for index, message in enumerate(messages):
        if index and PART_DELAY_SECONDS > 0:
            await asyncio.sleep(PART_DELAY_SECONDS)
        try:
            if message.attachment_type == "image" and message.attachment_url:
                response = await meta_client.send_image(
                    ig_business_id=ig_business_id,
                    access_token=access_token,
                    recipient_id=recipient_id,
                    image_url=message.attachment_url,
                )
            else:
                response = await meta_client.send_message(
                    ig_business_id=ig_business_id,
                    access_token=access_token,
                    recipient_id=recipient_id,
                    text=message.content,
                )
        except MetaAPIError as exc:
            # The echo may have beaten the error here (Meta delivered, then the
            # response was lost): then it did go out.
            await db.refresh(message, ["external_message_id"])
            if message.external_message_id is not None:
                message.delivery_status = DELIVERY_SENT
                message.delivery_error = None
                await db.commit()
                continue
            message.delivery_status = DELIVERY_FAILED
            message.delivery_error = str(exc)[:500]
            await db.commit()
            raise DeliveryError(str(exc), permanent=getattr(exc, "permanent", False)) from exc

        message.delivery_status = DELIVERY_SENT
        message.delivery_error = None
        message_id = response.get("message_id") if isinstance(response, dict) else None
        if message_id and message.external_message_id is None:
            message.external_message_id = message_id
        await db.commit()


async def undelivered_reply(db: AsyncSession, conversation: Conversation, answering: Message) -> list[Message]:
    """The unsent AI text reply to `answering`, oldest part first (a message whose
    echo arrived counts as delivered)."""
    since = max(answering.created_at, dt.datetime.now(dt.timezone.utc) - RESEND_WINDOW)
    result = await db.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.sender_type == "ai",
            Message.message_type == "text",
            Message.delivery_status.in_((DELIVERY_PENDING, DELIVERY_FAILED)),
            Message.external_message_id.is_(None),
            Message.created_at >= since,
        )
        .order_by(Message.created_at.asc())
    )
    return list(result.scalars().all())


async def reconcile_echo(
    db: AsyncSession,
    conversation: Conversation,
    *,
    external_message_id: str,
    text: str,
    attachment_type: str | None,
) -> bool:
    """Matches an echo to our recent outbound message still missing its Instagram id.
    Returns whether it was ours."""
    since = dt.datetime.now(dt.timezone.utc) - RESEND_WINDOW
    candidates = (
        await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.sender_type.in_(("ai", "human")),
                Message.external_message_id.is_(None),
                Message.message_type != MESSAGE_TYPE_REACTION,
                Message.created_at >= since,
                Message.delivery_status.in_((DELIVERY_PENDING, DELIVERY_FAILED, DELIVERY_SENT)),
            )
            .order_by(Message.created_at.asc())
            .with_for_update()
        )
    ).scalars().all()
    # The send response may have recorded this very id while the rows above
    # were being locked (they're then no longer candidates).
    if await db.scalar(select(Message.id).where(Message.external_message_id == external_message_id)) is not None:
        await db.commit()
        return True
    for message in candidates:
        is_image = message.attachment_type == "image"
        if (attachment_type == "image" and is_image) or (
            attachment_type != "image" and not is_image and message.content.strip() == (text or "").strip()
        ):
            message.external_message_id = external_message_id
            message.delivery_status = DELIVERY_SENT
            message.delivery_error = None
            await db.commit()
            return True
    await db.commit()
    return False
