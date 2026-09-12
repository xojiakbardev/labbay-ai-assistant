import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.conversations.delivery import DeliveryError, send_outbound
from app.conversations.locks import conversation_lock
from app.conversations.models import DELIVERY_PENDING, Conversation, Message
from app.conversations.schemas import ConversationDetailOut, ConversationOut, ConversationStatus, MessageOut
from app.conversations.service import add_message
from app.core.db import get_db
from app.core.security import decrypt_secret
from app.customers.models import Customer
from app.instagram.client import MetaClient
from app.instagram.models import InstagramAccount
from app.notifications.broadcaster import broadcaster

router = APIRouter(prefix="/conversations", tags=["conversations"])

DETAIL_MESSAGE_LIMIT = 100


def get_meta_client() -> MetaClient:
    return MetaClient()


class ConversationReplyIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)


def _display_name(cust: Customer) -> str:
    if cust.username and cust.username.strip():
        return f"@{cust.username.lstrip('@')}"
    if cust.name and cust.name.strip():
        return cust.name.strip()
    return "Instagram foydalanuvchisi"


def _out(conv: Conversation, cust: Customer) -> ConversationOut:
    return ConversationOut(
        id=conv.id,
        customer_id=conv.customer_id,
        customer_username=_display_name(cust),
        customer_name=cust.name,
        customer_phone=cust.phone,
        channel=conv.channel,
        status=conv.status,
        last_message_at=conv.last_message_at,
        created_at=conv.created_at,
    )


async def _owned(db: AsyncSession, business_id: uuid.UUID, conversation_id: uuid.UUID) -> tuple[Conversation, Customer]:
    res = (
        await db.execute(
            select(Conversation, Customer)
            .join(Customer, Conversation.customer_id == Customer.id)
            .where(Conversation.business_id == business_id, Conversation.id == conversation_id)
        )
    ).first()
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return res


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Newest activity first, paginated. Profiles are looked up by the
    message pipeline, never here — a page load must not wait on Meta."""
    result = await db.execute(
        select(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .where(Conversation.business_id == business.id)
        .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id)
        .limit(limit)
        .offset(offset)
    )
    return [_out(conv, cust) for conv, cust in result.all()]


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    limit: int = Query(DETAIL_MESSAGE_LIMIT, ge=1, le=500),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """The conversation with its most recent `limit` messages (oldest first)."""
    conversation, cust = await _owned(db, business.id, conversation_id)
    newest_first = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    has_more = len(newest_first) > limit
    messages = list(reversed(newest_first[:limit]))
    base = _out(conversation, cust)
    return ConversationDetailOut(**base.model_dump(), messages=messages, has_more_messages=has_more)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages_after(
    conversation_id: uuid.UUID,
    after: uuid.UUID | None = Query(default=None, description="Return messages newer than this message id."),
    limit: int = Query(100, ge=1, le=500),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Incremental polling: only what arrived after the last message the
    client already has, instead of the whole history every few seconds."""
    conversation, _cust = await _owned(db, business.id, conversation_id)
    stmt = select(Message).where(Message.conversation_id == conversation.id)
    if after is not None:
        anchor = await db.scalar(
            select(Message.created_at).where(Message.id == after, Message.conversation_id == conversation.id)
        )
        if anchor is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Anchor message not found in this conversation.")
        stmt = stmt.where(Message.created_at > anchor)
    rows = (await db.execute(stmt.order_by(Message.created_at.asc()).limit(limit))).scalars().all()
    return list(rows)


@router.patch("/{conversation_id}/status", response_model=ConversationOut)
async def update_conversation_status(
    conversation_id: uuid.UUID,
    status_value: ConversationStatus,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    conv, cust = await _owned(db, business.id, conversation_id)
    conv.status = status_value
    await db.commit()
    await db.refresh(conv)
    return _out(conv, cust)


@router.post("/{conversation_id}/reply", response_model=MessageOut)
async def send_operator_reply(
    conversation_id: uuid.UUID,
    payload: ConversationReplyIn,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
    meta_client: MetaClient = Depends(get_meta_client),
):
    """An operator's reply from the dashboard. Recorded first, then sent
    (outbox), under the conversation lock so its echo is recognised as ours.
    A person replying means a person has taken over: the AI steps back until
    the owner hands the conversation back."""
    conversation, customer = await _owned(db, business.id, conversation_id)
    account = await db.scalar(
        select(InstagramAccount).where(
            InstagramAccount.business_id == business.id,
            InstagramAccount.status == "connected",
        )
    )
    if account is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No active Instagram account connected.")
    access_token = decrypt_secret(account.access_token_encrypted)
    # End the read transaction before waiting on the lock (it takes a second
    # pooled connection; a waiter must not hold two).
    await db.commit()

    async with conversation_lock(conversation.id):
        await db.refresh(conversation)
        message = await add_message(
            db, conversation, sender_type="human", content=payload.content, delivery_status=DELIVERY_PENDING
        )
        if conversation.status in ("ai_active", "human_needed"):
            conversation.status = "human_active"
        await db.commit()
        try:
            await send_outbound(
                db, meta_client, ig_business_id=account.ig_business_id, access_token=access_token,
                recipient_id=customer.ig_scoped_id, messages=[message],
            )
        except DeliveryError as exc:
            # The message stays in the thread, marked failed, so the owner
            # sees exactly what didn't go out.
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.refresh(message)
    await broadcaster.broadcast(
        business.id,
        {
            "type": "conversation_updated",
            "conversation_id": str(conversation.id),
            "customer_id": str(customer.id),
            "last_message": payload.content,
            "sender_type": "human",
        },
    )
    return message


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Deletes the thread (messages cascade). The customer's lead — phone
    number, history — survives; its conversation link is cleared."""
    conv, _cust = await _owned(db, business.id, conversation_id)
    await db.delete(conv)
    await db.commit()
