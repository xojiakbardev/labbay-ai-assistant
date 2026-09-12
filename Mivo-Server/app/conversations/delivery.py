"""The outbox: how anything we say to a customer on Instagram goes out.

Every outbound message is persisted as `pending` *before* it's sent, then
marked `sent` (with Instagram's message id) or `failed`. That gives the
guarantees the old send-then-hope code didn't have:

- the owner sees every message the customer received (persist before send);
- a retried webhook event resends exactly the AI reply it had already written,
  instead of running a fresh LLM turn and giving the customer a second,
  different answer;
- an echo of one of our own messages is recognised — by its id when the send
  response already recorded it, or by matching it to the recent outbound row
  it belongs to when the echo got here first (`reconcile_echo`).
"""
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
from app.instagram.client import MetaAPIError, MetaClient

logger = logging.getLogger("app.conversations.delivery")

# A beat between the parts of a split reply — the pause a person spends typing
# the next line. Tests set it to 0.
PART_DELAY_SECONDS = 0.9

# An undelivered reply is resent while its webhook event is being retried;
# this is longer than the whole retry schedule (app/instagram/pipeline.py).
RESEND_WINDOW = dt.timedelta(minutes=20)


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
    """The AI text reply to `answering` that hasn't gone out yet, oldest part
    first. Only that: an operator's failed dashboard reply is theirs to retype,
    a product photo or follow-up that didn't go through isn't worth resending
    ahead of a real answer.

    A message whose echo already came back (it has an Instagram id) reached
    the customer, whatever its send call reported."""
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
    """Matches an echo to our own recent outbound message that has no
    Instagram id yet — sent but its response not recorded, a send that timed
    out after Meta delivered it, or a 2xx without an id. Records the id (and
    marks it sent). Returns whether it was ours."""
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
