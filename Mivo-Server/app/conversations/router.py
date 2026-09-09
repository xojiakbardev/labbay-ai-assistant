import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.conversations.models import Conversation, Message
from app.conversations.schemas import ConversationDetailOut, ConversationOut, MessageOut
from app.conversations.service import add_message
from app.core.db import get_db
from app.core.security import decrypt_secret
from app.customers.models import Customer
from app.instagram.client import MetaAPIError, MetaClient
from app.instagram.models import InstagramAccount

router = APIRouter(prefix="/conversations", tags=["conversations"])


def get_meta_client() -> MetaClient:
    return MetaClient()


class ConversationReplyIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    business: Business = Depends(get_current_business), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .where(Conversation.business_id == business.id)
        .order_by(Conversation.last_message_at.desc().nullslast())
    )
    items = []
    ig_account = None
    account_checked = False

    for conv, cust in result.all():
        if (not cust.username or not cust.username.strip()) and cust.ig_scoped_id:
            if not account_checked:
                ig_account = await db.scalar(
                    select(InstagramAccount).where(
                        InstagramAccount.business_id == business.id,
                        InstagramAccount.status == "connected",
                    )
                )
                account_checked = True

            if ig_account and ig_account.access_token_encrypted:
                try:
                    acc_token = decrypt_secret(ig_account.access_token_encrypted)
                    client = MetaClient()
                    prof = await client.get_user_profile(cust.ig_scoped_id, acc_token)
                    if prof:
                        if prof.get("username"):
                            cust.username = prof["username"].lstrip("@")
                        if prof.get("name"):
                            cust.name = prof["name"]
                        await db.commit()
                except Exception as e:
                    print(f"[Conversations] Auto backfill profile error for {cust.ig_scoped_id}: {e}")

        if cust.username and cust.username.strip():
            username = f"@{cust.username.lstrip('@')}"
        elif cust.name and cust.name.strip():
            username = cust.name.strip()
        else:
            username = "Instagram foydalanuvchisi"

        items.append(
            ConversationOut(
                id=conv.id,
                customer_id=conv.customer_id,
                customer_username=username,
                customer_name=cust.name,
                customer_phone=cust.phone,
                channel=conv.channel,
                status=conv.status,
                last_message_at=conv.last_message_at,
                created_at=conv.created_at,
            )
        )
    return items


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(
        select(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .where(Conversation.business_id == business.id, Conversation.id == conversation_id)
    )
    res = row.first()
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")

    conversation, cust = res

    messages = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.asc())
        )
    ).scalars().all()

    if (not cust.username or not cust.username.strip()) and cust.ig_scoped_id:
        ig_account = await db.scalar(
            select(InstagramAccount).where(
                InstagramAccount.business_id == business.id,
                InstagramAccount.status == "connected",
            )
        )
        if ig_account and ig_account.access_token_encrypted:
            try:
                acc_token = decrypt_secret(ig_account.access_token_encrypted)
                client = MetaClient()
                prof = await client.get_user_profile(cust.ig_scoped_id, acc_token)
                if prof:
                    if prof.get("username"):
                        cust.username = prof["username"].lstrip("@")
                    if prof.get("name"):
                        cust.name = prof["name"]
                    await db.commit()
            except Exception as e:
                print(f"[Conversations] Auto backfill profile error for {cust.ig_scoped_id}: {e}")

    if cust.username and cust.username.strip():
        username = f"@{cust.username.lstrip('@')}"
    elif cust.name and cust.name.strip():
        username = cust.name.strip()
    else:
        username = "Instagram foydalanuvchisi"

    return ConversationDetailOut(
        id=conversation.id,
        customer_id=conversation.customer_id,
        customer_username=username,
        customer_name=cust.name,
        customer_phone=cust.phone,
        channel=conversation.channel,
        status=conversation.status,
        last_message_at=conversation.last_message_at,
        created_at=conversation.created_at,
        messages=list(messages),
    )


@router.patch("/{conversation_id}/status", response_model=ConversationOut)
async def update_conversation_status(
    conversation_id: uuid.UUID,
    status_value: str,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(
        select(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .where(Conversation.business_id == business.id, Conversation.id == conversation_id)
    )
    res = row.first()
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")

    conv, cust = res
    conv.status = status_value
    await db.commit()
    await db.refresh(conv)

    username = f"@{cust.username}" if cust.username else f"@user_{cust.ig_scoped_id[:8]}"
    return ConversationOut(
        id=conv.id,
        customer_id=conv.customer_id,
        customer_username=username,
        customer_phone=cust.phone,
        channel=conv.channel,
        status=conv.status,
        last_message_at=conv.last_message_at,
        created_at=conv.created_at,
    )


@router.post("/{conversation_id}/reply", response_model=MessageOut)
async def send_operator_reply(
    conversation_id: uuid.UUID,
    payload: ConversationReplyIn,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
    meta_client: MetaClient = Depends(get_meta_client),
):
    row = await db.execute(
        select(Conversation, Customer)
        .join(Customer, Conversation.customer_id == Customer.id)
        .where(Conversation.business_id == business.id, Conversation.id == conversation_id)
    )
    res = row.first()
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")

    conversation, customer = res

    # Find connected Instagram Account for this business
    account = await db.scalar(
        select(InstagramAccount).where(
            InstagramAccount.business_id == business.id,
            InstagramAccount.status == "connected",
        )
    )
    if account is None or not account.access_token_encrypted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No active Instagram account connected.")

    access_token = decrypt_secret(account.access_token_encrypted)

    # Send message to Instagram Direct via Meta API
    ext_id = None
    try:
        res_data = await meta_client.send_message(
            ig_business_id=account.ig_business_id,
            access_token=access_token,
            recipient_id=customer.ig_scoped_id,
            text=payload.content,
        )
        if isinstance(res_data, dict):
            ext_id = res_data.get("message_id") or res_data.get("id")
    except MetaAPIError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Add message with sender_type = "human"
    now = dt.datetime.now(dt.timezone.utc)
    message = await add_message(
        db,
        conversation,
        sender_type="human",
        content=payload.content,
        external_message_id=ext_id,
    )
    conversation.last_message_at = now
    await db.commit()
    await db.refresh(message)

    try:
        from app.notifications.broadcaster import broadcaster
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
    except Exception as e:
        print(f"[ConversationsRouter] Broadcast reply error: {e}")

    return message


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    conv = await db.scalar(
        select(Conversation).where(Conversation.business_id == business.id, Conversation.id == conversation_id)
    )
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")

    # Delete related messages first
    from sqlalchemy import delete
    await db.execute(delete(Message).where(Message.conversation_id == conv.id))
    await db.delete(conv)
    await db.commit()
