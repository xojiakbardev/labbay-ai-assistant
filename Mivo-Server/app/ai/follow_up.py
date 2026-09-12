"""Smart follow-up: one gentle nudge to a warm/hot lead who went quiet after
the AI's last reply.

Rules, every one of them enforced here rather than hoped for:
- only businesses whose AI may reply at all (owner switch on, not suspended,
  not deleted, subscription active) — the same rule as live replies;
- only inside Meta's 24-hour messaging window, measured from the customer's
  last message (a promotional message outside it gets the app restricted);
- at most one follow-up per silence: a new one only after the customer has
  written again;
- persisted before sent, under the conversation lock, through the outbox — so
  two sweeps (or two processes) can't both send, and the echo is recognised.
"""
import datetime as dt
import logging
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestrator import detect_preferred_language
from app.businesses.models import Business
from app.businesses.service import ai_may_reply
from app.conversations.delivery import DeliveryError, send_outbound
from app.conversations.locks import conversation_lock
from app.conversations.models import DELIVERY_PENDING, Conversation, Message
from app.conversations.service import add_message, get_recent_messages
from app.core.security import decrypt_secret
from app.customers.models import Customer
from app.instagram.client import MetaClient
from app.instagram.models import InstagramAccount
from app.leads.models import Lead

logger = logging.getLogger("app.ai.follow_up")

_FOLLOW_UP_AFTER = dt.timedelta(minutes=30)
# Meta allows 24h after the customer's last message; keep a margin.
_MESSAGING_WINDOW = dt.timedelta(hours=23)
_BATCH = 50

_TEXT = {
    "uz": (
        "Assalomu alaykum! Siz so'ragan {product} bo'yicha savollaringiz qoldimi? "
        "Razmer yoki rangini tanlashda yordam kerak bo'lsa, bemalol yozing 😊",
        "Assalomu alaykum! Mahsulotlarimiz bo'yicha savollaringiz qoldimi? "
        "Sizga mos model va o'lchamni tanlashda yordam beraman 😊",
    ),
    "ru": (
        "Здравствуйте! Остались вопросы по {product}? Если нужно помочь с размером или цветом — пишите 😊",
        "Здравствуйте! Остались вопросы по нашим товарам? Помогу подобрать модель и размер 😊",
    ),
    "en": (
        "Hi! Any questions left about the {product}? Happy to help with the size or colour 😊",
        "Hi! Any questions left about our products? Happy to help you pick the right one 😊",
    ),
}


def _last_customer_message_at():
    return (
        select(func.max(Message.created_at))
        .where(Message.conversation_id == Conversation.id, Message.sender_type == "customer")
        .correlate(Conversation)
        .scalar_subquery()
    )


def _last_sender_type():
    return (
        select(Message.sender_type)
        .where(Message.conversation_id == Conversation.id)
        .order_by(Message.created_at.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )


async def _candidate_ids(db: AsyncSession, now: dt.datetime) -> list[uuid.UUID]:
    """Every rule that can be checked in SQL is: a batch filled with
    conversations that will be skipped anyway (the customer wrote last, say)
    would starve the ones that are due."""
    last_customer_at = _last_customer_message_at()
    rows = await db.execute(
        select(Conversation.id)
        .join(Lead, Lead.conversation_id == Conversation.id)
        .join(Business, Business.id == Conversation.business_id)
        .join(InstagramAccount, InstagramAccount.business_id == Business.id)
        .where(
            Conversation.status == "ai_active",
            Lead.status.in_(("warm", "hot")),
            Conversation.last_message_at <= now - _FOLLOW_UP_AFTER,
            _last_sender_type() == "ai",
            last_customer_at >= now - _MESSAGING_WINDOW,
            or_(Conversation.follow_up_sent_at.is_(None), Conversation.follow_up_sent_at < last_customer_at),
            Business.ai_enabled.is_(True),
            Business.ai_suspended.is_(False),
            Business.deleted_at.is_(None),
            or_(Business.subscription_expires_at.is_(None), Business.subscription_expires_at > now),
            InstagramAccount.status == "connected",
        )
        .order_by(Conversation.last_message_at.asc())
        .limit(_BATCH)
    )
    ids = list(dict.fromkeys(rows.scalars().all()))
    await db.commit()
    return ids


async def _follow_up_one(db: AsyncSession, conversation_id: uuid.UUID, client: MetaClient) -> bool:
    async with conversation_lock(conversation_id):
        now = dt.datetime.now(dt.timezone.utc)
        # The session is shared across the sweep: everything re-read fresh.
        conversation = await db.get(Conversation, conversation_id, populate_existing=True)
        if conversation is None or conversation.status != "ai_active":
            return False
        business = await db.get(Business, conversation.business_id, populate_existing=True)
        if business is None or not ai_may_reply(business, now):
            return False
        account = await db.scalar(
            select(InstagramAccount)
            .where(InstagramAccount.business_id == business.id, InstagramAccount.status == "connected")
            .execution_options(populate_existing=True)
        )
        customer = await db.get(Customer, conversation.customer_id, populate_existing=True)
        lead = await db.scalar(
            select(Lead).where(Lead.conversation_id == conversation.id).execution_options(populate_existing=True)
        )
        if account is None or customer is None or lead is None:
            return False

        # Re-checked under the lock: the customer may have written, or another
        # sweep may have followed up, since the candidate query ran.
        recent = await get_recent_messages(db, conversation.id, limit=6)
        if not recent or recent[-1].sender_type != "ai":
            return False
        last_customer = await db.scalar(
            select(func.max(Message.created_at)).where(
                Message.conversation_id == conversation.id, Message.sender_type == "customer"
            )
        )
        if last_customer is None or now - last_customer > _MESSAGING_WINDOW:
            return False
        if conversation.follow_up_sent_at is not None and conversation.follow_up_sent_at >= last_customer:
            return False
        if now - recent[-1].created_at < _FOLLOW_UP_AFTER:
            return False

        product = next(
            (p.get("name") for p in (lead.interested_products or []) if isinstance(p, dict) and p.get("name")),
            None,
        )
        lang = detect_preferred_language(business.language, [m.content for m in recent if m.content])
        with_product, generic = _TEXT.get(lang, _TEXT["uz"])
        text = with_product.format(product=product) if product else generic

        conversation.follow_up_sent_at = now
        message = await add_message(db, conversation, sender_type="ai", content=text, delivery_status=DELIVERY_PENDING)
        await db.commit()
        await send_outbound(
            db,
            client,
            ig_business_id=account.ig_business_id,
            access_token=decrypt_secret(account.access_token_encrypted),
            recipient_id=customer.ig_scoped_id,
            messages=[message],
        )
        logger.info("[follow_up] sent to conversation %s", conversation.id)
        return True


async def sweep_inactive_leads_follow_up(db: AsyncSession, meta_client: MetaClient | None = None) -> int:
    """Returns the number of follow-ups sent. Each conversation is handled on
    its own: one that fails is logged (and its message stays recorded as
    failed) without stopping the rest of the sweep."""
    client = meta_client or MetaClient()
    sent = 0
    for conversation_id in await _candidate_ids(db, dt.datetime.now(dt.timezone.utc)):
        try:
            if await _follow_up_one(db, conversation_id, client):
                sent += 1
        except DeliveryError as exc:
            logger.warning("[follow_up] conversation %s not delivered: %s", conversation_id, exc)
        except Exception:  # noqa: BLE001 — batch boundary, see docstring
            logger.exception("[follow_up] conversation %s failed", conversation_id)
            await db.rollback()
        # End the read transaction before waiting on the next lock.
        await db.commit()
    return sent
