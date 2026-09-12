"""Deterministic, backend-only lead persistence (plan §9/§10). The LLM never
calls create_lead/update_lead — this is the single place that writes to the
`leads` table, driven entirely by the orchestrator's structured output."""
import datetime as dt
import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestrator import ConversationTurnResult, merge_known_facts
from app.leads.models import Lead
from app.leads.scoring import extract_valid_phone, status_from_score
from app.products.models import Product

logger = logging.getLogger("app.leads.service")

# How many products a lead remembers interest in (newest first).
MAX_INTERESTED_PRODUCTS = 10
# How far back to look for a phone number the customer typed.
PHONE_LOOKBACK_MESSAGES = 6


async def _validate_interested_products(
    db: AsyncSession, business_id: uuid.UUID, product_ids: list[str]
) -> list[dict]:
    """Cross-checks the LLM-reported product IDs against real products for this
    business — IDs that don't resolve to a real product are dropped rather than
    trusted blindly (plan §10)."""
    candidate_uuids: list[uuid.UUID] = []
    for pid in product_ids:
        try:
            candidate_uuids.append(uuid.UUID(str(pid)))
        except ValueError:
            continue
    if not candidate_uuids:
        return []

    result = await db.execute(
        select(Product.id, Product.name).where(
            Product.business_id == business_id, Product.id.in_(candidate_uuids)
        )
    )
    names = {pid: name for pid, name in result.all()}
    # Keep the model's order (most relevant first), drop what didn't resolve.
    return [{"id": str(pid), "name": names[pid]} for pid in dict.fromkeys(candidate_uuids) if pid in names]


def _merge_products(existing: list | None, new: list[dict]) -> list[dict]:
    """New interest goes to the front; earlier interest is kept. A turn with
    no product in it (a thank-you, a bare phone number) must not wipe what the
    customer asked about before — that's exactly the turn a hot lead fires on."""
    merged: dict[str, dict] = {}
    for item in [*new, *(existing or [])]:
        if isinstance(item, dict) and item.get("id") and item["id"] not in merged:
            merged[item["id"]] = {"id": item["id"], "name": item.get("name")}
    return list(merged.values())[:MAX_INTERESTED_PRODUCTS]


def _digits(text: str | None) -> str:
    return re.sub(r"\D", "", text or "")


def _phone_seen_in(phone: str, texts: list[str]) -> bool:
    """Whether the customer actually typed this number (compared on the
    national 9 digits, so +998 90 123 45 67 and 901234567 both count)."""
    tail = _digits(phone)[-9:]
    return bool(tail) and any(tail in _digits(t) for t in texts)


async def apply_qualification(
    db: AsyncSession,
    business_id: uuid.UUID,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    result: ConversationTurnResult,
    raw_message_text: str | None = None,
) -> tuple[Lead, bool]:
    """Applies the orchestrator's structured output and independent phone
    extraction to the lead record. Commits.

    Returns (lead, became_hot_just_now) — the second value is True only on the
    turn a lead first reaches HOT, so the caller can gate a one-time owner
    notification off it.
    """
    from app.conversations.service import get_recent_messages
    from app.customers.models import Customer

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
        prev_status = None
    else:
        prev_status = lead.status

    customer = await db.get(Customer, customer_id, with_for_update=True)

    # Phone: only a number the customer actually typed. The model's
    # phone_detected is accepted only when that number appears in the
    # customer's own recent messages — otherwise a shop number quoted from the
    # business settings could be "detected" and turn the lead hot.
    recent = await get_recent_messages(db, conversation_id, limit=PHONE_LOOKBACK_MESSAGES * 2)
    customer_texts = [m.content for m in recent if m.sender_type == "customer" and m.content]
    customer_texts = customer_texts[-PHONE_LOOKBACK_MESSAGES:]
    if raw_message_text:
        customer_texts.append(raw_message_text)

    detected_phone = extract_valid_phone(raw_message_text)
    if detected_phone is None:
        for text in reversed(customer_texts):
            detected_phone = extract_valid_phone(text)
            if detected_phone:
                break
    if detected_phone is None:
        proposed = extract_valid_phone(result.phone_detected)
        if proposed and _phone_seen_in(proposed, customer_texts):
            detected_phone = proposed

    is_first_phone_capture = detected_phone is not None and lead.phone is None
    phone = lead.phone or detected_phone  # the first captured phone is never overwritten

    if phone:
        lead.phone = phone
        if customer and not customer.phone:
            customer.phone = phone

    # The turn a phone is first captured is hot; otherwise status tracks the
    # model's live assessment, clamped to the score bands.
    final_status = "hot" if is_first_phone_capture else status_from_score(result.lead_score)

    lead.conversation_id = conversation_id
    lead.status = final_status
    lead.score = result.lead_score
    lead.qualification_reason = result.qualification_reason

    # Only what the analyst actually wrote. Missing languages stay missing —
    # the dashboard shows the Uzbek summary instead of an untranslated copy
    # pretending to be Russian or English. The qualification reason is the
    # analyst's own reasoning (often in English), not an owner-facing summary:
    # a turn without summaries leaves the previous ones in place.
    summaries = {
        lang: text.strip()
        for lang, text in (("uz", result.summary_uz), ("ru", result.summary_ru), ("en", result.summary_en))
        if text and text.strip()
    }
    if summaries:
        lead.summaries = summaries
        if "uz" in summaries:
            lead.summary = summaries["uz"]

    new_products = await _validate_interested_products(db, business_id, result.interested_product_ids)
    lead.interested_products = _merge_products(lead.interested_products, new_products)

    if result.extracted_facts:
        lead.known_facts = merge_known_facts(lead.known_facts, result.extracted_facts)

    # HOT transitions notify once: first phone capture, or entering hot from
    # anything else. Staying hot doesn't re-notify; cooling and re-heating does.
    became_hot_just_now = is_first_phone_capture or (final_status == "hot" and prev_status != "hot")

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
    """Captures a phone number from an inbound customer message when the AI
    turn is skipped (AI off, paused, handed to a human, subscription lapsed).

    Returns (lead, notify) — notify is True whenever this message is the one
    that first put a phone number on the lead. A number is the single most
    actionable thing a customer can send, so it notifies even if the lead was
    already hot (and notified) before.
    """
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

    captured = {
        "uz": "Telefon raqami qoldirildi.",
        "ru": "Оставлен номер телефона.",
        "en": "Phone number was provided.",
    }
    if lead is None:
        lead = Lead(
            business_id=business_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            status="hot",
            score=70,
            phone=phone,
            interested_products=[],
            summary=captured["uz"],
            qualification_reason=captured["uz"],
            summaries=captured,
        )
        db.add(lead)
        await db.flush()
    elif lead.phone is not None:
        await db.commit()
        return lead, False  # already on file — nothing new to capture or notify
    else:
        lead.phone = phone
        lead.status = "hot"
        lead.score = max(lead.score, 70)
        lead.conversation_id = conversation_id
        if not lead.summaries:
            lead.summaries = captured

    await db.commit()
    await db.refresh(lead)
    return lead, True
