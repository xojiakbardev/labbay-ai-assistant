"""Autonomous Smart Follow-Up Engine.

Periodically sweeps conversations to re-engage warm/hot leads who stopped
responding after inquiring about products or prices, helping convert them
into hot leads ready for operator handover.
"""
import datetime as dt
import logging
import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.businesses.models import Business
from app.conversations.models import Conversation, Message
from app.core.security import decrypt_secret
from app.customers.models import Customer
from app.instagram.client import MetaClient
from app.instagram.models import InstagramAccount
from app.leads.models import Lead

logger = logging.getLogger("app.ai.follow_up")

_FOLLOW_UP_MIN_MINUTES = 30
_FOLLOW_UP_MAX_HOURS = 24


async def sweep_inactive_leads_follow_up(db: AsyncSession, meta_client: MetaClient | None = None) -> int:
    """Finds warm/hot conversations that went silent after an AI reply,
    and sends a helpful, natural follow-up message to re-engage the customer.
    
    Returns the number of follow-ups sent.
    """
    now = dt.datetime.now(dt.timezone.utc)
    min_cutoff = now - dt.timedelta(minutes=_FOLLOW_UP_MIN_MINUTES)
    max_cutoff = now - dt.timedelta(hours=_FOLLOW_UP_MAX_HOURS)

    client = meta_client or MetaClient()
    sent_count = 0

    try:
        # Find active AI conversations with warm or hot leads
        stmt = (
            select(Conversation, Lead, Customer, Business, InstagramAccount)
            .join(Lead, Lead.conversation_id == Conversation.id)
            .join(Customer, Customer.id == Conversation.customer_id)
            .join(Business, Business.id == Conversation.business_id)
            .join(InstagramAccount, InstagramAccount.business_id == Business.id)
            .where(
                Conversation.status == "ai_active",
                Lead.status.in_(["warm", "hot"]),
                Conversation.last_message_at <= min_cutoff,
                Conversation.last_message_at >= max_cutoff,
                InstagramAccount.access_token_encrypted.is_not(None),
            )
            .limit(15)
        )
        results = (await db.execute(stmt)).all()

        for conv, lead, customer, business, account in results:
            # Check the last message in this conversation
            last_msg_stmt = (
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at.desc())
                .limit(2)
            )
            msgs = (await db.execute(last_msg_stmt)).scalars().all()
            if not msgs:
                continue

            latest_msg = msgs[0]
            # Only follow up if the last sender was the AI (customer stopped responding)
            if latest_msg.sender_type != "ai":
                continue

            # Check if latest message was already a follow-up to avoid spamming
            content_lower = (latest_msg.content or "").lower()
            if "savollaringiz qoldimi" in content_lower or "yordam bera olamizmi" in content_lower:
                continue

            access_token = decrypt_secret(account.access_token_encrypted)
            if not access_token:
                continue

            # Determine language & construct friendly follow-up
            product_name = None
            if lead.interested_products and isinstance(lead.interested_products, list):
                first_p = lead.interested_products[0]
                if isinstance(first_p, dict) and first_p.get("name"):
                    product_name = first_p["name"]

            # Choose natural message text based on product interest
            if product_name:
                follow_up_text = (
                    f"Assalomu alaykum! Siz so'ragan {product_name} bo'yicha savollaringiz qoldimi? "
                    "Agar razmer yoki rangini tanlashda yordam kerak bo'lsa, xursand bo'lamiz 😊"
                )
            else:
                follow_up_text = (
                    "Assalomu alaykum! Mahsulotlarimiz bo'yicha savollaringiz qoldimi? "
                    "Sizga mos model va o'lchamni tanlashda yordam bera olamizmi? 😊"
                )

            try:
                # Send follow up via Instagram Graph API
                send_res = await client.send_message(
                    ig_business_id=account.ig_business_id,
                    access_token=access_token,
                    recipient_id=customer.ig_scoped_id,
                    text=follow_up_text,
                )

                # Persist the follow-up message
                follow_up_msg = Message(
                    id=uuid.uuid4(),
                    conversation_id=conv.id,
                    sender_type="ai",
                    content=follow_up_text,
                    external_message_id=send_res.get("message_id") if isinstance(send_res, dict) else None,
                    created_at=now,
                )
                db.add(follow_up_msg)
                conv.updated_at = now
                await db.commit()

                logger.info(
                    f"[SmartFollowUp] Sent follow-up to customer {customer.ig_scoped_id} for conversation {conv.id}"
                )
                sent_count += 1
            except Exception as err:
                logger.warning(f"[SmartFollowUp] Failed to send follow-up to customer {customer.id}: {err}")
                await db.rollback()

    except Exception as exc:
        logger.error(f"[SmartFollowUp] Sweep failed: {exc}")

    return sent_count
