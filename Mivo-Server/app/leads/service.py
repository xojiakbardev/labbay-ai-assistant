"""Deterministic, backend-only lead persistence (plan §9/§10). The LLM never
calls create_lead/update_lead — this is the single place that writes to the
`leads` table, driven entirely by the orchestrator's structured output."""
import datetime as dt
import logging
import urllib.parse
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestrator import ConversationTurnResult, merge_known_facts
from app.leads.models import Lead
from app.leads.scoring import extract_valid_phone, status_from_score
from app.products.models import Product

logger = logging.getLogger("app.leads.service")


async def _translate_fallback(text: str, target_lang: str) -> str | None:
    if not text or not text.strip():
        return None
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={target_lang}&dt=t&q={urllib.parse.quote(text.strip())}"
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                translated = "".join(part[0] for part in data[0] if part[0]).strip()
                if translated:
                    return translated
    except Exception as exc:
        logger.debug("Translation fallback error: %s", exc)
    return None


async def _validate_interested_products(
    db: AsyncSession, business_id: uuid.UUID, product_ids: list[str]
) -> list[dict]:
    """Cross-checks the LLM-reported product IDs against real products for this
    business — IDs that don't resolve to a real product are dropped rather than
    trusted blindly (plan §10)."""
    candidate_uuids: list[uuid.UUID] = []
    for pid in product_ids:
        try:
            candidate_uuids.append(uuid.UUID(pid))
        except ValueError:
            continue
    if not candidate_uuids:
        return []

    result = await db.execute(
        select(Product.id, Product.name).where(
            Product.business_id == business_id, Product.id.in_(candidate_uuids)
        )
    )
    return [{"id": str(pid), "name": name} for pid, name in result.all()]


async def apply_qualification(
    db: AsyncSession,
    business_id: uuid.UUID,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    result: ConversationTurnResult,
    raw_message_text: str | None = None,
) -> tuple[Lead, bool]:
    """Applies the orchestrator's structured output and independent phone extraction
    to the lead record.

    Returns (lead, became_hot_just_now) — the second value is True only on the
    turn a lead first reaches HOT, so the caller (Telegram notifier) can gate
    a one-time notification off it.
    """
    from app.customers.models import Customer
    from app.conversations.service import get_recent_messages

    lead = await db.scalar(
        select(Lead)
        .where(Lead.business_id == business_id, Lead.customer_id == customer_id)
        .with_for_update()
    )
    if lead is None:
        lead = Lead(
            business_id=business_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            status="cold",
            score=0,
            interested_products=[],
        )
        db.add(lead)
        await db.flush()

    customer = await db.get(Customer, customer_id, with_for_update=True)

    # 1. Independent phone number extraction:
    # Priority: raw customer message -> LLM proposed phone_detected -> recent customer messages
    detected_phone = extract_valid_phone(raw_message_text) or extract_valid_phone(result.phone_detected)
    if not detected_phone and lead.phone is None:
        recent_msgs = await get_recent_messages(db, conversation_id, limit=5)
        for m in reversed(recent_msgs):
            if m.sender_type == "customer":
                extracted = extract_valid_phone(m.content)
                if extracted:
                    detected_phone = extracted
                    break

    is_first_phone_capture = detected_phone is not None and lead.phone is None
    phone = lead.phone or detected_phone  # preserve first captured phone, never overwrite or un-capture

    if phone:
        lead.phone = phone
        if customer and not customer.phone:
            customer.phone = phone

    prev_status = lead.status if lead.id is not None else None

    # Status calculation:
    # 1. The turn a phone is FIRST captured is always "hot" and triggers notification.
    # 2. On other turns, status tracks the model's live assessment (result.lead_score).
    if is_first_phone_capture:
        final_status = "hot"
    else:
        final_status = status_from_score(result.lead_score)

    lead.conversation_id = conversation_id
    lead.status = final_status
    lead.score = result.lead_score
    lead.qualification_reason = result.qualification_reason
    lead.summary = result.summary_uz or result.qualification_reason

    summaries_dict = {}
    uz_text = (result.summary_uz or result.qualification_reason or "").strip()
    ru_text = (result.summary_ru or "").strip()
    en_text = (result.summary_en or "").strip()

    if uz_text:
        summaries_dict["uz"] = uz_text

    # Provide Russian translation if missing or untranslated duplicate of Uzbek
    if ru_text and ru_text != uz_text:
        summaries_dict["ru"] = ru_text
    elif uz_text:
        translated_ru = await _translate_fallback(uz_text, "ru")
        summaries_dict["ru"] = translated_ru or uz_text

    # Provide English translation if missing or untranslated duplicate of Uzbek
    if en_text and en_text != uz_text:
        summaries_dict["en"] = en_text
    elif uz_text:
        translated_en = await _translate_fallback(uz_text, "en")
        summaries_dict["en"] = translated_en or uz_text

    lead.summaries = summaries_dict

    lead.interested_products = await _validate_interested_products(
        db, business_id, result.interested_product_ids
    )

    if result.extracted_facts:
        lead.known_facts = merge_known_facts(lead.known_facts, result.extracted_facts)

    # Deterministic HOT transition rule:
    # - First phone capture -> always notifies.
    # - Transition into HOT from non-hot (cold/warm/None) -> notifies.
    # - Remaining HOT across multiple messages (hot -> hot) -> does NOT re-notify.
    # - If status cooled to warm/cold and later rises back to HOT -> re-notifies.
    if is_first_phone_capture:
        became_hot_just_now = True
    elif final_status == "hot" and prev_status != "hot":
        became_hot_just_now = True
    else:
        became_hot_just_now = False

    await db.commit()
    await db.refresh(lead)
    return lead, became_hot_just_now


async def mark_hot_notified(db: AsyncSession, lead: Lead) -> None:
    """Called only after a Telegram notification actually sends —
    gates the lead from ever notifying again for this HOT transition."""
    lead.hot_notified_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()


async def capture_phone_without_ai_turn(
    db: AsyncSession,
    business_id: uuid.UUID,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_text: str,
) -> tuple[Lead | None, bool]:
    """Captures phone number from inbound customer message when AI turn is skipped
    (e.g., AI disabled, paused, human handoff, subscription lapsed)."""
    from app.customers.models import Customer

    phone = extract_valid_phone(message_text)
    if phone is None:
        return None, False

    lead = await db.scalar(
        select(Lead)
        .where(Lead.business_id == business_id, Lead.customer_id == customer_id)
        .with_for_update()
    )
    customer = await db.get(Customer, customer_id, with_for_update=True)
    if customer and not customer.phone:
        customer.phone = phone

    if lead is None:
        lead = Lead(
            business_id=business_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            status="hot",
            score=70,
            phone=phone,
            interested_products=[],
            summary="Telefon raqami qoldirildi.",
            qualification_reason="Telefon raqami qoldirildi.",
            summaries={
                "uz": "Telefon raqami qoldirildi.",
                "ru": "Оставлен номер телефона.",
                "en": "Phone number was provided.",
            },
        )
        db.add(lead)
        await db.flush()
        became_hot_just_now = True
    elif lead.phone is not None:
        return lead, False  # already on file — nothing new to capture or notify
    else:
        lead.phone = phone
        lead.status = "hot"
        if not lead.summaries or not lead.summaries.get("ru"):
            current_uz = (lead.summaries or {}).get("uz") or lead.summary or "Telefon raqami qoldirildi."
            lead.summaries = {
                "uz": current_uz,
                "ru": "Оставлен номер телефона.",
                "en": "Phone number was provided.",
            }
        became_hot_just_now = lead.hot_notified_at is None

    await db.commit()
    await db.refresh(lead)
    return lead, became_hot_just_now
