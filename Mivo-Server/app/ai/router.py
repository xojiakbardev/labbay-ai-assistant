import datetime as dt
import html
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai import limits
from app.ai.closing import decide_closing, may_be_closing, unanswered_burst, valid_reaction
from app.ai.models import AiFeedback
from app.ai.orchestrator import handle_customer_message
from app.ai.provider.base import LLMProvider, LLMProviderError
from app.ai.provider.factory import get_llm_provider
from app.ai.schemas import (
    FeedbackCreate,
    FeedbackOut,
    SandboxMessageOut,
    SandboxMessageRequest,
    SandboxProductOut,
    SandboxStateResponse,
    SandboxTurnResponse,
)
from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.conversations.models import MESSAGE_TYPE_REACTION, Conversation, Message
from app.conversations.service import add_message, get_recent_messages
from app.core.config import get_settings
from app.core.db import get_db
from app.customers.models import Customer
from app.leads.models import Lead
from app.leads.notifications import get_telegram_client
from app.products.media import get_display_image_url
from app.products.models import Product
from app.telegram.client import TelegramAPIError
from app.telegram.models import TelegramConnection

logger = logging.getLogger("app.ai.router")

# Sandbox sessions older than this will be auto-reset on next access
SANDBOX_TTL_HOURS: int = get_settings().sandbox_ttl_hours

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/feedback", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED)
async def submit_ai_feedback(
    body: FeedbackCreate,
    business: Annotated[Business, Depends(get_current_business)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AiFeedback:
    feedback = AiFeedback(
        business_id=business.id,
        conversation_id=body.conversation_id,
        message_id=body.message_id,
        rating=body.rating,
        customer_query=body.customer_query,
        ai_response=body.ai_response,
        correction=body.correction,
        is_active=True,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return feedback


@router.get("/learned-rules", response_model=list[FeedbackOut])
async def list_learned_rules(
    business: Annotated[Business, Depends(get_current_business)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[AiFeedback]:
    result = await db.execute(
        select(AiFeedback)
        .where(AiFeedback.business_id == business.id, AiFeedback.is_active == True)
        .order_by(AiFeedback.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/learned-rules/{feedback_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_learned_rule(
    feedback_id: uuid.UUID,
    business: Annotated[Business, Depends(get_current_business)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    feedback = await db.scalar(
        select(AiFeedback).where(AiFeedback.id == feedback_id, AiFeedback.business_id == business.id)
    )
    if feedback is None:
        raise HTTPException(status_code=404, detail="Learned rule not found")

    feedback.is_active = False
    await db.commit()


# =========================================================================
# AI Sandbox / Live Tester Endpoints
# =========================================================================


async def _get_or_create_sandbox_session(
    db: AsyncSession, business: Business
) -> tuple[Customer, Conversation, Lead | None]:
    """Retrieves or creates a dedicated, isolated sandbox session for this business."""
    from app.conversations.service import get_or_create_conversation
    from app.customers.service import get_or_create_customer

    customer = await get_or_create_customer(
        db, business.id, ig_scoped_id=f"sandbox_{business.id}", username="sandbox_tester", is_sandbox=True
    )
    conversation = await get_or_create_conversation(db, business.id, customer.id)

    lead = await db.scalar(
        select(Lead).where(Lead.business_id == business.id, Lead.customer_id == customer.id)
    )

    # Auto-cleanup: if the session has been idle for longer than the TTL, wipe it
    now = dt.datetime.now(dt.timezone.utc)
    ttl_cutoff = now - dt.timedelta(hours=SANDBOX_TTL_HOURS)
    if conversation.last_message_at and conversation.last_message_at < ttl_cutoff:
        logger.info(
            "Sandbox session for business %s expired (idle since %s). Auto-resetting.",
            business.id,
            conversation.last_message_at,
        )
        await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
        if lead:
            await db.execute(delete(Lead).where(Lead.id == lead.id))
            lead = None
        conversation.status = "ai_active"
        conversation.last_message_at = None
        conversation.last_answered_customer_message_at = None
        # The sale-in-progress outlives the messages it came from — a reset
        # that kept it would start the next session holding the previous
        # one's focus product, prices and open question.
        conversation.working_state = {}
        await db.flush()
    elif conversation.status == "human_needed":
        # Sandbox conversations should never stay in human_needed permanently.
        # Reset status so the AI is active again on page load.
        conversation.status = "ai_active"
        await db.flush()

    return customer, conversation, lead



async def _fetch_interested_products(
    db: AsyncSession, business_id: uuid.UUID, product_ids: list[str]
) -> list[SandboxProductOut]:
    if not product_ids:
        return []
    valid_uuids = []
    for pid in product_ids:
        try:
            valid_uuids.append(uuid.UUID(str(pid)))
        except (ValueError, TypeError):
            continue
    if not valid_uuids:
        return []

    result = await db.execute(
        select(Product)
        .options(selectinload(Product.images))
        .where(Product.business_id == business_id, Product.id.in_(valid_uuids))
    )
    products = result.scalars().all()
    out = []
    for p in products:
        img = get_display_image_url(p)
        out.append(
            SandboxProductOut(
                id=str(p.id),
                name=p.name,
                price=float(p.price) if p.price is not None else None,
                currency=p.currency or "UZS",
                image_url=img,
                availability=bool(p.availability),
            )
        )
    return out


@router.get("/sandbox", response_model=SandboxStateResponse)
async def get_sandbox_state(
    business: Annotated[Business, Depends(get_current_business)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SandboxStateResponse:
    """Returns the current state and history of the AI Sandbox."""
    customer, conversation, lead = await _get_or_create_sandbox_session(db, business)
    messages_raw = await get_recent_messages(db, conversation.id, limit=50)
    messages_out = [
        SandboxMessageOut(
            id=m.id,
            sender_type=m.sender_type,
            message_type=m.message_type,
            content=m.content,
            attachment_url=getattr(m, "attachment_url", None),
            created_at=m.created_at,
        )
        for m in messages_raw
    ]

    interested_products: list[SandboxProductOut] = []
    if lead and lead.interested_products:
        p_ids = [p.get("id") for p in lead.interested_products if isinstance(p, dict) and p.get("id")]
        interested_products = await _fetch_interested_products(db, business.id, p_ids)

    await db.commit()  # persist TTL-cleanup and status resets
    return SandboxStateResponse(
        conversation_id=conversation.id,
        messages=messages_out,
        lead_status=lead.status if lead else None,
        lead_score=lead.score if lead else None,
        phone=lead.phone if lead else None,
        qualification_reason=lead.qualification_reason if lead else None,
        known_facts=lead.known_facts if lead and lead.known_facts else [],
        interested_products=interested_products,
    )


@router.post("/sandbox/message", response_model=SandboxTurnResponse)
async def post_sandbox_message(
    body: SandboxMessageRequest,
    business: Annotated[Business, Depends(get_current_business)],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> SandboxTurnResponse:
    """Executes a simulated customer message in the Sandbox and returns detailed turn introspection."""
    if business.ai_suspended:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "AI is suspended for this business.")
    customer, conversation, lead = await _get_or_create_sandbox_session(db, business)
    # Sandbox turns cost the same as real ones and count against the same
    # daily budget.
    spent = await limits.cost_today_usd(db, business.id)
    if spent >= get_settings().ai_daily_cost_limit_usd:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Daily AI budget reached.")
    conversation.status = "active"

    # The same closing rule as on Instagram (app/ai/closing.py): a message that
    # just ends the conversation gets no reply, at most a reaction.
    recent = await get_recent_messages(db, conversation.id, limit=12)
    incoming = Message(
        sender_type="customer", content=body.content, attachment_url=body.attachment_url,
        attachment_type="image" if body.attachment_url else None,
        message_type="image" if body.attachment_url else "text",
    )
    burst, last_outbound = unanswered_burst([*recent, incoming])
    if may_be_closing(burst, last_outbound):
        try:
            decision = await decide_closing(provider, [*recent, incoming], business_id=business.id)
        except LLMProviderError as exc:
            logger.warning("Sandbox closing decision unavailable: %s", exc)
            decision = None
        if decision is not None and decision.conversation_finished:
            await add_message(db, conversation, sender_type="customer", content=body.content)
            reaction = valid_reaction(decision.reaction)
            if reaction:
                await add_message(
                    db, conversation, sender_type="ai", content=reaction, message_type=MESSAGE_TYPE_REACTION
                )
            messages_raw = await get_recent_messages(db, conversation.id, limit=50)
            await db.commit()
            return SandboxTurnResponse(
                reply="",
                lead_status=lead.status if lead else "cold",
                lead_score=lead.score if lead else 0,
                qualification_reason=(lead.qualification_reason or "") if lead else "",
                phone_detected=lead.phone if lead else None,
                known_facts=lead.known_facts if lead and lead.known_facts else [],
                messages=[
                    SandboxMessageOut(
                        id=m.id, sender_type=m.sender_type, message_type=m.message_type, content=m.content,
                        attachment_url=getattr(m, "attachment_url", None), created_at=m.created_at,
                    )
                    for m in messages_raw
                ],
                conversation_closed=True,
                reaction=reaction,
            )

    escalation_state: dict[str, Any] = {}
    turn_result = await handle_customer_message(
        db=db,
        provider=provider,
        business=business,
        conversation=conversation,
        customer_message_text=body.content,
        apply_lead_qualification=True,
        attachment_url=body.attachment_url,
        attachment_type="image" if body.attachment_url else None,
        escalation_state_out=escalation_state,
    )

    # Reload lead to get latest merged facts & status
    lead = await db.scalar(
        select(Lead).where(Lead.business_id == business.id, Lead.customer_id == customer.id)
    )

    lead_products = ([p.get("id") for p in lead.interested_products if isinstance(p, dict) and p.get("id")]
                     if lead and lead.interested_products else [])
    p_ids = turn_result.interested_product_ids or lead_products
    products_out = await _fetch_interested_products(db, business.id, p_ids)

    # Sandbox should never stay in human_needed — reset so next message still gets AI response
    if conversation.status != "active":
        conversation.status = "active"

    telegram_sent = False
    if body.simulate_telegram and lead:
        connection = await db.scalar(
            select(TelegramConnection).where(
                TelegramConnection.business_id == business.id,
                TelegramConnection.telegram_chat_id.is_not(None),
            )
        )
        if connection and connection.telegram_chat_id:
            sandbox_url = html.escape(f"{get_settings().frontend_url}/sandbox", quote=True)
            try:
                tg_client = get_telegram_client()
                text = (
                    f"🧪 <b>[AI SINOVCHISI - TEST XABARNOMASI]</b>\n\n"
                    f"👤 <b>Mijoz (Simulyatsiya):</b> @{html.escape(customer.username or '')}\n"
                    f"📞 <b>Telefon:</b> <code>{html.escape(lead.phone or '—')}</code>\n"
                    f"📊 <b>Niyat holati:</b> <code>{lead.status.upper()} ({lead.score}/100)</code>\n"
                    f"💡 <b>AI Xulosasi:</b> <i>{html.escape(lead.summary or lead.qualification_reason or '—')}</i>\n\n"
                    f"👉 <a href='{sandbox_url}'>Mivo AI Sinovchisini ochish</a>"
                )
                keyboard = [[{"text": "🧪 Sinovchini ochish", "url": sandbox_url}]]
                await tg_client.send_message(
                    connection.telegram_chat_id,
                    text,
                    reply_markup={"inline_keyboard": keyboard},
                )
                telegram_sent = True
            except TelegramAPIError as e:
                logger.warning("Failed to send sandbox telegram alert: %s", e)

    messages_raw = await get_recent_messages(db, conversation.id, limit=50)
    messages_out = [
        SandboxMessageOut(
            id=m.id,
            sender_type=m.sender_type,
            message_type=m.message_type,
            content=m.content,
            attachment_url=getattr(m, "attachment_url", None),
            created_at=m.created_at,
        )
        for m in messages_raw
    ]

    await db.commit()  # persist messages, lead, and conversation status changes
    return SandboxTurnResponse(
        reply=turn_result.reply,
        lead_status=turn_result.lead_status,
        lead_score=turn_result.lead_score,
        qualification_reason=turn_result.qualification_reason,
        phone_detected=turn_result.phone_detected,
        extracted_facts=turn_result.extracted_facts or [],
        known_facts=lead.known_facts if lead and lead.known_facts else [],
        interested_products=products_out,
        executed_tools=escalation_state.get("executed_tools", []),
        telegram_sent=telegram_sent,
        messages=messages_out,
    )


@router.post("/sandbox/reset")
async def reset_sandbox(
    business: Annotated[Business, Depends(get_current_business)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Resets the sandbox conversation, messages, and associated lead."""
    customer, conversation, lead = await _get_or_create_sandbox_session(db, business)

    await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    conversation.status = "active"
    # The sale-in-progress outlives the messages it came from, so a reset that
    # only clears the transcript would leave the AI still holding the previous
    # run's focus product, prices and open question.
    conversation.working_state = {}

    if lead:
        await db.execute(delete(Lead).where(Lead.id == lead.id))

    await db.commit()
    return {"status": "ok", "message": "Sandbox conversation reset successfully"}

