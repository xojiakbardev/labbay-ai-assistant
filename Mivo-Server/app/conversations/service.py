"""Conversation + message persistence and the human-handoff state machine
(plan §23/§24)."""
import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import Conversation, Message

HISTORY_LIMIT = 10


async def get_or_create_conversation(
    db: AsyncSession, business_id: uuid.UUID, customer_id: uuid.UUID
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.business_id == business_id, Conversation.customer_id == customer_id
        )
    )
    if conversation is None:
        conversation = Conversation(
            business_id=business_id,
            customer_id=customer_id,
            channel="instagram",
            status="ai_active",
            created_at=dt.datetime.now(dt.timezone.utc),
        )
        db.add(conversation)
        await db.flush()
    return conversation


async def add_message(
    db: AsyncSession,
    conversation: Conversation,
    sender_type: str,
    content: str,
    message_type: str = "text",
    external_message_id: str | None = None,
    flagged_for_review: bool = False,
    attachment_url: str | None = None,
    attachment_type: str | None = None,
) -> Message:
    if external_message_id is not None:
        # Idempotent on retry: if a Meta webhook redelivery reaches this point
        # after a prior attempt already persisted the message (but crashed
        # before completing), don't violate the unique constraint or double-add
        # it — just return what's already there (plan §11 webhook reliability).
        existing = await db.scalar(
            select(Message).where(Message.external_message_id == external_message_id)
        )
        if existing is not None:
            return existing

    now = dt.datetime.now(dt.timezone.utc)
    message = Message(
        conversation_id=conversation.id,
        sender_type=sender_type,
        content=content,
        message_type=message_type,
        external_message_id=external_message_id,
        flagged_for_review=flagged_for_review,
        attachment_url=attachment_url,
        attachment_type=attachment_type,
        created_at=now,
    )
    db.add(message)
    conversation.last_message_at = now
    await db.flush()
    return message


async def get_recent_messages(
    db: AsyncSession, conversation_id: uuid.UUID, limit: int = HISTORY_LIMIT
) -> list[Message]:
    """Last `limit` messages, oldest first — never the whole conversation history
    (plan §20)."""
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def set_status(db: AsyncSession, conversation: Conversation, status: str) -> None:
    conversation.status = status
    await db.flush()
