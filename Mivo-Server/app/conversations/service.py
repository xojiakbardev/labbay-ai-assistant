"""Conversation + message persistence and the human-handoff state machine
(plan §23/§24)."""
import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import Conversation, Message

# Instagram DM is a burst medium: people split one thought across three short
# messages, so ten messages is barely three exchanges and a conversation loses
# its thread almost immediately. Twenty covers a realistic sales conversation;
# anything older is carried by the lead's known_facts/summary (CUSTOMER PROFILE)
# and the conversation's working_state instead of by raw history.
HISTORY_LIMIT = 20


async def get_or_create_conversation(
    db: AsyncSession, business_id: uuid.UUID, customer_id: uuid.UUID
) -> Conversation:
    """Atomic: the unique (business_id, customer_id) constraint plus ON
    CONFLICT DO NOTHING means concurrent first messages share one
    conversation instead of splitting the thread across two rows."""
    await db.execute(
        insert(Conversation)
        .values(
            id=uuid.uuid4(),
            business_id=business_id,
            customer_id=customer_id,
            channel="instagram",
            status="ai_active",
            working_state={},
            created_at=dt.datetime.now(dt.timezone.utc),
        )
        .on_conflict_do_nothing(constraint="uq_conversations_business_id_customer_id")
    )
    return await db.scalar(
        select(Conversation).where(
            Conversation.business_id == business_id, Conversation.customer_id == customer_id
        )
    )


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
    delivery_status: str | None = None,
) -> Message:
    """Idempotent on external_message_id: a Meta redelivery of a message that
    is already stored returns the stored row (INSERT ... ON CONFLICT, so two
    concurrent deliveries can't both insert)."""
    now = dt.datetime.now(dt.timezone.utc)
    values = dict(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sender_type=sender_type,
        content=content,
        message_type=message_type,
        external_message_id=external_message_id,
        flagged_for_review=flagged_for_review,
        attachment_url=attachment_url,
        attachment_type=attachment_type,
        delivery_status=delivery_status,
        created_at=now,
    )
    if external_message_id is not None:
        inserted_id = (
            await db.execute(
                insert(Message)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[Message.external_message_id])
                .returning(Message.id)
            )
        ).scalar_one_or_none()
        message = await db.scalar(select(Message).where(Message.external_message_id == external_message_id))
        if inserted_id is not None:
            conversation.last_message_at = now
        return message

    message = Message(**values)
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
