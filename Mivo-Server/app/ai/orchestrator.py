"""The AI conversation engine's single agentic turn (plan §8).

Persists the customer message, builds bounded context, runs the tool-calling
loop against real product data, persists the AI's reply, and returns the fused
reply+qualification result. Lead persistence itself is NOT done here — Phase 8's
leads/service.py takes this result and deterministically decides what to write;
the LLM has no create_lead/update_lead tool (plan §9/§10).
"""

import datetime as dt
import logging
import re
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.ai.context.builder import (
    build_analyst_prompt,
    build_message_history,
    build_system_prompt_with_learnings,
    unseen_shared_media,
)
from app.ai.conversation_state import update_state
from app.ai.provider.base import LLMProvider, LLMProviderError
from app.ai import replies
from app.ai.reply_parts import split_reply
from app.ai.tools.definitions import ALL_TOOLS
from app.ai.tools.executor import build_tool_executor
from app.businesses.models import Business
from app.conversations.models import DELIVERY_PENDING, Conversation, Message, is_untranscribed_voice_note
from app.conversations.service import add_message, get_recent_messages
from app.leads.models import Lead
from app.leads.scoring import COLD_MAX_SCORE, WARM_MAX_SCORE
from app.core.config import get_settings
from app import prompts

_LANGUAGE_MARKERS = prompts.lexicon()["language_markers"]


def _has_word(text: str, words: list[str]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text) for w in words)


def detect_preferred_language(
    business_language: str | None = None,
    text_samples: list[str] | None = None,
) -> str:
    """'uz', 'ru' or 'en' from what the customer wrote (pass their messages
    only — our own replies would echo our language back), else the business's
    language. Uzbek written in Cyrillic is Uzbek, not Russian."""
    if text_samples:
        combined = " ".join(t for t in text_samples if t).lower()
        if re.search(r"[а-яёўқғҳ]", combined):
            if any(ch in combined for ch in _LANGUAGE_MARKERS["uz_cyrillic_letters"]) or _has_word(
                combined, _LANGUAGE_MARKERS["uz_cyrillic"]
            ):
                return "uz"
            return "ru"
        if _has_word(combined, _LANGUAGE_MARKERS["uz"]) or any(
            f in combined for f in _LANGUAGE_MARKERS["uz_fragments"]
        ):
            return "uz"
        if _has_word(combined, _LANGUAGE_MARKERS["en"]):
            return "en"

    biz_lang = (business_language or "").strip().lower()
    if biz_lang.startswith("uz") or "o'zbek" in biz_lang or "ozbek" in biz_lang:
        return "uz"
    if biz_lang.startswith("ru") or "rus" in biz_lang:
        return "ru"
    if biz_lang.startswith("en") or "eng" in biz_lang:
        return "en"
    return "uz"


class TurnAnalysis(BaseModel):
    """Everything the backend needs to know about a turn except the reply itself
    — the analyst pass's output (see LLMProvider.run_sales_turn).

    Every field here is a *proposal*: the phone override and score-band clamping
    (Phase 8) are what actually decide the persisted lead status, never this
    object directly.

    Field order is deliberate. `qualification_reason` comes first so the model
    writes the justification before the label it justifies, rather than picking
    a score and then rationalising it — with structured output the fields are
    generated in order, so a conclusion placed before its reasoning is a
    conclusion reached without any.
    """

    qualification_reason: str = Field(
        description=(
            "One short, concrete sentence for THIS score specifically — what they just said "
            "or did that justifies it. If interest cooled or they cancelled, say that "
            "plainly instead of repeating an earlier, now-stale reason."
        )
    )
    lead_status: str = Field(
        pattern="^(cold|warm|hot)$",
        description=(
            "Reassess from scratch every single reply, using only the conversation as it "
            "stands right now — never carry over or build on an earlier turn's status. "
            "hot: they gave a phone number, explicitly agreed to buy, or are asking how to "
            "pay/order right now. warm: real interest in a specific product (asked price, "
            "asked about a variant, compared options) but no firm commitment yet. cold: "
            "browsing, generic questions, or they've gone quiet/said no/cancelled — a "
            "conversation that was hot earlier but has since cooled off or fallen through "
            "must be scored cold or warm now, not hot just because it once was."
        ),
    )
    lead_score: int = Field(
        ge=0, le=100,
        description=(
            f"0-100, matching lead_status's band (0-{COLD_MAX_SCORE} cold, {COLD_MAX_SCORE + 1}-{WARM_MAX_SCORE} "
            f"warm, {WARM_MAX_SCORE + 1}-100 hot). Your "
            "honest read of how close this customer is to buying right now — based only on "
            "this exact message and the conversation so far, not on how many messages "
            "you've exchanged or how promising it looked a few turns ago."
        ),
    )
    stage: str | None = Field(
        default=None,
        description=(
            "Where this sale stands after the reply that was just sent, as one of: "
            "greeting (they've only said hello), discovery (you still need to know more "
            "before you can recommend anything), recommendation (a specific product is on "
            "the table), objection (they pushed back on price, fit, trust or timing), "
            "closing (they're deciding or ordering), handoff (it's going to a human). "
            "Judge it from where the conversation actually is now, not from where it started."
        ),
    )
    open_question: str | None = Field(
        default=None,
        description=(
            "If the reply that was just sent asked the customer something and is waiting on "
            "their answer, that question in a few words (e.g. 'qaysi razmer kerakligi', "
            "'sportga yoki kundalikka'). Null if the reply asked nothing."
        ),
    )
    slots_learned: list[str] | None = Field(
        default=None,
        description=(
            "What the customer revealed in THIS turn about what they're looking for, as "
            "\"key: value\" strings using only these keys: use_case, size, color, budget, "
            "recipient. E.g. [\"use_case: sportga\", \"size: 42\", \"budget: 500000 so'm\", "
            "\"recipient: ukasiga\"]. Only what they actually said — never inferred, never "
            "carried over from an earlier turn. Empty if they revealed nothing new."
        ),
    )
    new_objection: str | None = Field(
        default=None,
        description=(
            "If the customer pushed back in THIS turn, name it in two or three words "
            "(e.g. 'qimmat', 'razmer topilmadi', 'yetkazib berish sekin'). Null otherwise — "
            "only a genuine objection raised in this turn, not one from earlier."
        ),
    )
    summary_uz: str | None = Field(
        default=None,
        description="Short concrete 1-sentence lead summary in Uzbek reflecting the customer's actual inquiry, preferences, or intent."
    )
    summary_ru: str | None = Field(
        default=None,
        description="Short concrete 1-sentence lead summary in Russian reflecting the customer's actual inquiry, preferences, or intent."
    )
    summary_en: str | None = Field(
        default=None,
        description="Short concrete 1-sentence lead summary in English reflecting the customer's actual inquiry, preferences, or intent."
    )
    phone_detected: str | None = None
    interested_product_ids: list[str] = Field(default_factory=list)
    image_product_ids: list[str] = Field(default_factory=list)
    extracted_facts: list[str] | None = Field(
        default=None,
        description=(
            "Any meaningful facts, preferences, or personal details about this customer revealed in THIS turn "
            "(e.g. age: 'yoshi 20 da', preferences: 'qora rangni yoqtiradi', constraints: 'byudjeti 400 000 so\\'m', "
            "fit/sizing: '42 razmer kiyadi', intent/purpose: 'sovg\\'aga olyapti', lifestyle/style: 'sportcha uslub'). "
            "Capture any durable facts about the customer freely so future turns can personalize the conversation."
        ),
    )


class ConversationTurnResult(TurnAnalysis):
    """One turn's full output: the customer-facing reply fused with the analysis.

    This is what the rest of the backend consumes (leads/service.py, the
    sandbox, the Instagram pipeline), and it stays a single object regardless of
    how it was produced — two passes on a provider that supports them, one fused
    call on one that doesn't.

    `reply` is declared last on purpose. In the single-pass fallback the whole
    object comes out of one constrained generation, and a reply generated before
    any of the analysis is a reply written before the model has worked out what
    the sales move is.
    """

    reply: str


def merge_known_facts(
    existing_facts: list[dict] | None,
    new_facts: list[str] | None,
    max_facts: int = 15,
) -> list[dict]:
    """Merges new extracted facts into the lead's known_facts list.
    - Adds new facts as {"text": str, "noted_at": ISO datetime str}.
    - Discards duplicates using exact and substring matching (case-insensitive).
    - If list exceeds max_facts (default 15), drops oldest items (FIFO).
    """
    merged = list(existing_facts or [])
    if not new_facts:
        return merged

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()

    for fact in new_facts:
        if not fact or not isinstance(fact, str):
            continue
        cleaned = fact.strip()
        if not cleaned:
            continue
        cleaned_lower = cleaned.lower()

        # Deduplication: check against existing facts (exact or word-bounded substring)
        is_duplicate = False
        for ef in merged:
            ef_text = (ef.get("text") or "").strip().lower()
            if not ef_text:
                continue
            if cleaned_lower == ef_text:
                is_duplicate = True
                break
            if re.search(r"\b" + re.escape(cleaned_lower) + r"\b", ef_text):
                is_duplicate = True
                break
            if re.search(r"\b" + re.escape(ef_text) + r"\b", cleaned_lower):
                is_duplicate = True
                break

        if not is_duplicate:
            merged.append({"text": cleaned, "noted_at": now_iso})

    if len(merged) > max_facts:
        merged = merged[-max_facts:]

    return merged


async def run_turn(
    db: AsyncSession,
    provider: LLMProvider,
    business: Business,
    conversation: Conversation,
    escalation_state_out: dict | None = None,
    deliver: Callable[[list[Message]], Awaitable[None]] | None = None,
    outbound: bool = False,
) -> ConversationTurnResult:
    """Runs one agentic LLM turn against whatever's already persisted in the
    conversation's message history, and persists the AI's reply. Does NOT
    persist a new customer message itself — the Instagram webhook path
    (app/instagram/service.py) debounces bursts of rapid customer messages,
    persisting each one immediately but only calling this once the customer
    has gone quiet for a few seconds, so one reply answers everything they
    sent instead of firing a separate reply per message. Callers that haven't
    already recorded the customer's message should use handle_customer_message
    instead.

    `deliver`, when given, is awaited with the persisted AI message(s) as soon
    as the reply is written and the safety guards have passed — before the
    analysis pass runs. Nothing after that point changes what the customer
    reads, so making them wait out the bookkeeping only ever cost them time.
    It is called at most once, and raising from it fails the turn (the reply is
    recorded but undelivered, which is what the webhook's retry is for).

    Once it has run, the turn is committed to that reply: any later failure
    degrades the analysis rather than sending a second message. When that
    happens `escalation_state_out["analysis_failed"]` is set and the caller
    must skip lead qualification — the lead's previous assessment is a better
    answer than one derived from nothing. The same flag is set when the
    provider failed outright and the customer got the handoff reply: there is
    no assessment then either.

    `outbound=True` records the reply parts as `pending` outbox messages (the
    Instagram path); `deliver` marks them sent/failed. In the same commit that
    records the reply, the conversation notes the newest customer message this
    turn saw — a retried event uses it to tell "already answered" from "still
    owed an answer".
    """
    history = await get_recent_messages(db, conversation.id)
    # The newest customer message this turn answers. A voice note that landed
    # after its burst was transcribed isn't answered here (the turn only sees
    # a placeholder) — its own event transcribes it and replies.
    latest_customer_at = max(
        (
            m.created_at
            for m in history
            if getattr(m, "sender_type", None) == "customer" and not is_untranscribed_voice_note(m)
        ),
        default=None,
    )
    system_prompt = await build_system_prompt_with_learnings(
        db, business, conversation.customer_id, working_state=conversation.working_state
    )
    phone_on_file = conversation.customer_id is not None and await db.scalar(
        select(Lead.phone).where(
            Lead.business_id == business.id,
            Lead.customer_id == conversation.customer_id,
            Lead.phone.is_not(None),
        )
    ) is not None
    messages = build_message_history(history)
    executor, escalation_state = build_tool_executor(db, business.id, conversation)

    latest = history[-1] if history else None
    has_latest_image = bool(
        latest is not None
        and getattr(latest, "sender_type", None) == "customer"
        and getattr(latest, "attachment_url", None)
        and (
            getattr(latest, "attachment_type", None) == "image"
            or getattr(latest, "message_type", None) == "image"
        )
    )
    # The customer's own words pick the reply language (ready-made replies).
    recent_texts = [
        m.content for m in history[-8:] if getattr(m, "sender_type", None) == "customer" and getattr(m, "content", None)
    ][-3:]
    # Did the customer, since our last message, share something the model
    # can't see (a Reel with no caption)? Then a reply describing it is made up.
    unseen_media = False
    for m in reversed(history):
        if getattr(m, "sender_type", None) != "customer":
            break
        unseen_media = unseen_media or unseen_shared_media(m)
    customer_texts = [
        m.content for m in history[-8:] if getattr(m, "sender_type", None) == "customer" and getattr(m, "content", None)
    ]

    delivered_reply: str | None = None
    flagged_for_review = False

    async def _guard_persist_and_deliver(reply_text: str) -> str:
        """Everything standing between the writer and the customer.

        Runs inside the provider call, between the writer and the analyst,
        because these guards are the last word on customer-facing text and the
        analysis has no say in it.
        """
        nonlocal delivered_reply, flagged_for_review

        safe_reply, flagged_for_review = _apply_reply_guards(
            reply_text,
            business,
            escalation_state,
            recent_texts,
            working_state=conversation.working_state,
            customer_texts=customer_texts,
            unseen_media=unseen_media,
        )
        if escalation_state["escalated"]:
            # Only from an AI-active status: someone who took the conversation
            # over while this turn was running (a reply from the Instagram app
            # sets human_active) keeps it.
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id, Conversation.status.in_(("ai_active", "active")))
                .values(status="human_needed")
                .execution_options(synchronize_session=False)
            )

        persisted = []
        for part in split_reply(safe_reply):
            persisted.append(
                await add_message(
                    db, conversation, sender_type="ai", content=part,
                    flagged_for_review=flagged_for_review,
                    delivery_status=DELIVERY_PENDING if outbound else None,
                )
            )
        if latest_customer_at is not None:
            conversation.last_answered_customer_message_at = latest_customer_at
        # Recorded before sent, never the other way round: a message the
        # customer has but the database doesn't is one the owner can't see.
        await db.commit()
        if escalation_state["escalated"]:
            await db.refresh(conversation, ["status"])

        delivered_reply = safe_reply
        if deliver is not None:
            try:
                await deliver(persisted)
            except Exception as exc:  # noqa: BLE001 — handed to the caller, see below
                # The reply is recorded (pending/failed) and will be resent by
                # the caller's retry. The turn itself carries on so the analysis
                # still happens; the caller gets the error via
                # escalation_state_out["delivery_error"] and decides.
                escalation_state["delivery_error"] = exc
        return safe_reply

    try:
        # Two passes, not one: the reply is written as prose, then analysed.
        # See LLMProvider.run_sales_turn for why they're separated.
        reply_text, analysis = await provider.run_sales_turn(
            system_prompt=system_prompt,
            messages=messages,
            tools=ALL_TOOLS,
            tool_executor=executor,
            fused_schema=ConversationTurnResult,
            analysis_schema=TurnAnalysis,
            analyst_system_prompt=build_analyst_prompt(business, phone_on_file=phone_on_file),
            on_reply=_guard_persist_and_deliver,
            business_id=business.id,
        )
        result = ConversationTurnResult(reply=reply_text, **analysis.model_dump())

    except LLMProviderError as exc:
        if delivered_reply is not None:
            # The customer already has a good answer and only the bookkeeping
            # failed. Sending a second, apologetic message on top of a reply
            # that worked would be worse than having no lead score for one turn.
            logger.warning(
                "Analysis pass failed after the reply was delivered for business %s: %s",
                business.id,
                exc,
            )
            escalation_state["analysis_failed"] = True
            result = ConversationTurnResult(
                reply=delivered_reply,
                lead_status="cold",
                lead_score=0,
                qualification_reason=(
                    "Analysis pass failed after delivery — these values are placeholders and "
                    "must not be persisted; see escalation_state['analysis_failed']."
                ),
            )
        else:
            logger.error(
                "AI provider turn failed after all retries and fallbacks for business %s: %s",
                business.id,
                exc,
            )
            # No analysis exists for this turn; the placeholder scores below
            # only satisfy the result type and must never reach the lead.
            escalation_state["analysis_failed"] = True
            pref_lang = detect_preferred_language(business.language, recent_texts)

            if has_latest_image:
                # Ask for a text description instead of escalating.
                result = ConversationTurnResult(
                    reply=replies.reply_text(business, "photo_unreadable", pref_lang),
                    lead_status="warm",
                    lead_score=40,
                    qualification_reason="Image processing fallback — asked customer to describe product in text.",
                )
            else:
                escalation_state["escalated"] = True
                escalation_state["reason"] = f"AI provider error: {exc}"
                result = ConversationTurnResult(
                    reply=replies.reply_text(business, "handoff", pref_lang),
                    lead_status="cold",
                    lead_score=0,
                    qualification_reason="AI provider failed; escalated to human operator.",
                )

            # The fallback reply hasn't been through the hook, so it still needs
            # persisting and delivering.
            await _guard_persist_and_deliver(result.reply)

    finally:
        # Whatever happened, the caller learns what the turn did (escalated?
        # delivery failed? analysis missing?) — it needs that to alert the
        # owner even when this function raises.
        if escalation_state_out is not None:
            escalation_state_out.update(escalation_state)

    # Carry the sale forward. Product facts come off the tool results rather
    # than the model's own output, so a price recorded here is one the catalog
    # actually returned this turn (app/ai/conversation_state.py).
    conversation.working_state = update_state(
        conversation.working_state,
        product_facts=escalation_state.get("product_facts"),
        stage=result.stage,
        open_question=result.open_question,
        new_objection=result.new_objection,
        interested_product_ids=result.interested_product_ids,
        slots_learned=result.slots_learned,
        escalated=escalation_state["escalated"],
    )
    await db.commit()

    if escalation_state_out is not None:
        escalation_state_out.update(escalation_state)
    return result



# The model's notes about media (app/ai/context/builder.py) and the labels
# older messages were stored with must never reach a customer, however the
# model came to write them: the note or label itself is cut out, and a
# sentence that talks about one ("«Template yuborildi» deganingizni
# tushunmadim") is dropped.
_NOTE_SPAN_RE = re.compile(r"\(note:[^)]*\)[ \t]*", re.IGNORECASE)
_LABEL_SPAN_RE = re.compile(r"\[[^\]\n]{0,80}\][ \t]*")
_LABEL_PHRASE_RE = re.compile(
    r"template yuborildi|rasm yubordi|rasm yuborildi|ulashildi|story'da belgilandi|\(note:", re.IGNORECASE
)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+")


def strip_internal_markers(reply: str) -> str:
    """The reply without internal notes/labels. Returned unchanged when it
    carries none."""
    cleaned = _LABEL_SPAN_RE.sub("", _NOTE_SPAN_RE.sub("", reply))
    if _LABEL_PHRASE_RE.search(cleaned):
        lines = []
        for line in cleaned.split("\n"):
            kept = [s for s in _SENTENCE_END_RE.split(line) if not _LABEL_PHRASE_RE.search(s)]
            lines.append(" ".join(kept).strip())
        cleaned = "\n".join(line for line in lines if line)
    return reply if cleaned == reply else cleaned.strip()


# "Rasmdagi qora hoodie...", "на видео...", "in the reel..." — a reply that
# describes media the model was never shown.
_CLAIMS_TO_SEE_RE = re.compile(
    r"\b(?:rasm|surat|video|reels?|post|story)(?:da|dagi|ingizda|ingizdagi|ngizda|ngizdagi)\b"
    r"|ko['‘’`ʻ]?rinishidan|ko['‘’`ʻ]?ryapman|ko['‘’`ʻ]?rinyapti"
    r"|на (?:фото|видео|картинке|рилсе)|в (?:видео|рилсе|посте)"
    r"|in (?:the|your|this) (?:video|photo|picture|reel|post|story)|i can see",
    re.IGNORECASE,
)


def claims_to_see_media(reply: str) -> bool:
    return bool(_CLAIMS_TO_SEE_RE.search(reply))


def _apply_reply_guards(
    reply: str,
    business: Business,
    escalation_state: dict,
    recent_texts: list[str],
    working_state: dict | None = None,
    customer_texts: list[str] | None = None,
    unseen_media: bool = False,
) -> tuple[str, bool]:
    """The last checks on text about to reach a customer.

    Returns (reply_to_send, flagged_for_review). The guards replace the
    model's words outright rather than trusting them, which is the same
    deterministic-backend-decides pattern the phone override and score clamping
    use — the model proposes, the backend decides what actually goes out.

    A reply the discount or price guard rewrites promises that an operator will
    follow up, so the guard also escalates for real: the conversation goes to
    human_needed and the owner is alerted. The promise is never an empty one.

    `unseen_media`: the customer shared something the model couldn't see (a
    Reel with no caption) — a reply describing it is invented, and is replaced
    with an honest "I can't open it, which product was it?".
    """
    flagged = False
    lang = detect_preferred_language(business.language, recent_texts)

    cleaned = strip_internal_markers(reply)
    if cleaned != reply:
        logger.warning("Reply carried an internal label/note for business %s. Original: %s", business.id, reply)
        flagged = True
        reply = cleaned or replies.reply_text(business, "neutral", lang)

    if unseen_media and claims_to_see_media(reply):
        logger.warning("Reply described media the model never saw, business %s. Original: %s", business.id, reply)
        flagged = True
        reply = replies.reply_text(business, "media_unseen", lang)

    if escalation_state["escalated"]:
        fixed = _ensure_escalation_reply(
            reply, escalation_state["reason"], business, text_samples=recent_texts
        )
        if fixed != reply:
            reply = fixed

    guard_reply: str | None = None
    if _contains_unverified_discount_claim(
        reply,
        escalation_state.get("executed_tools", []),
        escalation_state.get("active_discounts", []),
        _catalog_prices(escalation_state, working_state),
    ):
        guard_reply, reason = "discount_unconfirmed", "AI tasdiqlanmagan chegirma aytmoqchi bo'ldi — tekshirib javob bering."
    elif find_unverified_prices(reply, escalation_state, working_state, business, customer_texts or []):
        guard_reply, reason = "price_unconfirmed", "AI katalogda yo'q narx aytmoqchi bo'ldi — narxni tasdiqlab javob bering."

    if guard_reply is not None:
        logger.warning("Reply guard intercepted a reply for business %s. Original: %s", business.id, reply)
        flagged = True
        reply = replies.reply_text(business, guard_reply, lang)
        escalation_state["escalated"] = True
        escalation_state["reason"] = reason

    return reply, flagged


_DISCOUNT_TERMS = prompts.lexicon()["discount_terms"]
# Ways to lower a price without saying "discount" — "10% arzonroq", "уступлю".
_PRICE_LOWERING_TERMS = prompts.lexicon()["price_lowering_terms"]
# "chegirma yo'q", "скидок нет", "no discount right now" — honest denials.
# Stripped before looking for claims, so a denial can't hide a claim elsewhere
# in the same reply, and "100% paxta, chegirma yo'q" isn't read as a 100% one.
_DENIAL_RE = re.compile(
    r"(?:chegirma\w*|skidka\w*|скидк\w*|discount\w*|aksiya\w*|акци\w*)\s+"
    r"(?:hozircha\s+|hozir\s+|umuman\s+|сейчас\s+)?"
    r"(?:mavjud\s+emas|yo'?q|emas|bo'?lmaydi|нет|не\s+предусмотрен\w*|none|not\s+available)"
    r"|(?:no|нет)\s+(?:active\s+)?(?:discount|скидок)\w*",
    re.IGNORECASE,
)
_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:%|foiz|процент\w*|percent)", re.IGNORECASE)


# Punctuation that ends a clause — but not a "." or "," inside a number.
_CLAUSE_BREAK_RE = re.compile(r"(?<!\d)[.,;!?\n]|[.,;!?\n](?!\d)")


def _clause_around(text: str, start: int, end: int) -> str:
    left = max((m.end() for m in _CLAUSE_BREAK_RE.finditer(text, 0, start)), default=0)
    right = next((m.start() for m in _CLAUSE_BREAK_RE.finditer(text, end)), len(text))
    return text[left:right]


def extract_discount_numbers(text: str) -> list[float]:
    """Percentages and amounts the text presents as a discount. A percentage
    only counts with discount/price-lowering wording in the same clause —
    "100% paxtadan, 10% chegirma bilan" offers 10%, not 100%."""
    lowered = _DENIAL_RE.sub(" ", text.lower())
    # Normalize thousand separators like "50 000" -> "50000"
    cleaned = re.sub(r"(?<=\d)\s+(?=\d)", "", lowered)
    numbers: list[float] = []
    for m in _PCT_RE.finditer(cleaned):
        clause = _clause_around(cleaned, m.start(), m.end())
        if any(term in clause for term in _DISCOUNT_TERMS + _PRICE_LOWERING_TERMS):
            numbers.append(float(m.group(1).replace(",", ".")))
    for term in _DISCOUNT_TERMS:
        # e.g. "chegirma: 50000", "skidka 10000", "aksiya 20000"
        for m in re.finditer(rf"{term}\s*(?:narxi|miqdori|summasi|—|-|:)?\s*(\d+(?:[.,]\d+)?)", cleaned, re.IGNORECASE):
            try:
                numbers.append(float(m.group(1).replace(",", ".")))
            except ValueError:
                pass
        # e.g. "50000 so'm chegirma", "20000 chegirma", "10000 rubl skidka"
        for m in re.finditer(rf"(?:^|[^\d.,])(\d+(?:[.,]\d+)?)\s*(?:so'?m|сум|sum|%|foiz|rubl|rub|usd|\$)?\s*(?:lik\s+)?{term}", cleaned, re.IGNORECASE):
            try:
                numbers.append(float(m.group(1).replace(",", ".")))
            except ValueError:
                pass
    return list(dict.fromkeys(numbers))


_PROMISE_PATTERNS = [
    r"chegirma\s+(?:qilib\s+)?ber(?:amiz|aman|iladi)",
    r"chegirma\s+mavjud(?!\s+(?:emas|yo'?q))",
    r"aksiyamiz\s+bor(?!\s+(?:emas|yo'?q))",
    r"arzon(?:roq)?\s+qilib\s+ber(?:amiz|aman)",
    r"(?:tushirib|kamaytirib)\s+ber(?:amiz|aman)",
    r"сдела(?:ем|ю)\s+вам\s+скидку",
    r"скидк[ау]\s+предостав",
    r"уступ(?:лю|им)",
    r"give\s+you\s+a\s+discount",
    r"offer\s+a\s+discount",
]


def _verified_discount_numbers(active_discounts: list[dict], catalog_prices: set[float]) -> set[float]:
    """What a real discount lets the reply say next to discount wording: its
    value, the list price it applies to ("780 000 so'm chegirmadan keyin
    702 000"), what it comes to on that price (amount off, price after), and
    the amounts in its own description/conditions ("500 000 so'mdan ortiq")."""
    verified: set[float] = set(catalog_prices)
    for d in active_discounts:
        value = float(d.get("value") or 0)
        verified.add(value)
        for p in catalog_prices:
            if d.get("type") == "percentage":
                verified.add(round(p * value / 100, 2))
                verified.add(round(p * (1 - value / 100), 2))
            else:
                verified.add(p - value)
        verified.update(_amounts_in(d.get("description"), d.get("conditions")))
    return verified


def _contains_unverified_discount_claim(
    response_text: str,
    executed_tools: list[dict],
    active_discounts: list[dict],
    catalog_prices: set[float] | None = None,
) -> bool:
    """Detects whether the LLM generated an unverified discount or promotional promise."""
    t = _DENIAL_RE.sub(" ", response_text.lower())
    called_tool = any(call.get("name") == "get_active_discounts" for call in executed_tools)

    has_term = any(term in t for term in _DISCOUNT_TERMS + _PRICE_LOWERING_TERMS)
    claimed_numbers = extract_discount_numbers(response_text)
    if has_term and claimed_numbers:
        if not called_tool:
            return True
        verified = _verified_discount_numbers(active_discounts, catalog_prices or set())
        if any(not any(abs(num - v) <= max(0.01, v * _PRICE_TOLERANCE) for v in verified) for num in claimed_numbers):
            return True

    if any(re.search(pat, t) for pat in _PROMISE_PATTERNS):
        if not called_tool or not active_discounts:
            return True

    return False


# "780 000 so'm", "780.000 сум", "1 250 000 UZS", "500 ming so'm", "25 $".
_PRICE_RE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[  .,]\d{3})+|\d+)\s*(ming\s*)?(?:so['‘’`ʻ]?m|sum\b|сум|uzs|usd|\$|dollar)",
    re.IGNORECASE,
)
# "1,2 mln so'm", "1.5 млн", "2 million" — a price with or without a currency.
_MILLION_RE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(?:mln|million|млн)(?![a-zа-я])", re.IGNORECASE)
_BARE_NUMBER_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[  .,]\d{3})+|\d{4,})(?![\d])")
_GROUP_SPLIT_RE = re.compile(r"[  ]")

_PRICE_TOLERANCE = get_settings().guard_price_tolerance


def _parse_amount(digits: str, thousands: bool = False) -> float | None:
    cleaned = re.sub(r"[  .,]", "", digits)
    if not cleaned.isdigit():
        return None
    value = float(cleaned)
    return value * 1000 if thousands else value


def _price_readings(text: str) -> list[list[float]]:
    """Every price in the text, each as its possible readings. A number stuck
    in front of a space-grouped price is often not part of it — "Air Max 90
    780 000 so'm" is 780 000, not 90 780 000 — so such a price can also be
    read without its leading group."""
    readings: list[list[float]] = []
    for m in _PRICE_RE.finditer(text):
        thousands = bool(m.group(2))
        full = _parse_amount(m.group(1), thousands)
        if full is None:
            continue
        options = [full]
        groups = _GROUP_SPLIT_RE.split(m.group(1))
        if len(groups) >= 3:
            tail = _parse_amount("".join(groups[1:]), thousands)
            if tail is not None:
                options.append(tail)
        readings.append(options)
    for m in _MILLION_RE.finditer(text):
        readings.append([float(m.group(1).replace(",", ".")) * 1_000_000])
    return readings


def _quoted_prices(text: str) -> list[float]:
    """Prices as written (first reading of each)."""
    return [options[0] for options in _price_readings(text)]


def _amounts_in(*texts: str | None) -> set[float]:
    """Every amount written in free text — a business's delivery fee, a
    discount's threshold — whether or not a currency follows it."""
    found: set[float] = set()
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        found.update(_quoted_prices(text))
        for m in _BARE_NUMBER_RE.finditer(text):
            value = _parse_amount(m.group(1))
            if value is not None:
                found.add(value)
        for m in re.finditer(r"(\d+)\s*ming", text, re.IGNORECASE):
            found.add(float(m.group(1)) * 1000)
    return found


def _catalog_prices(escalation_state: dict, working_state: dict | None) -> set[float]:
    """Product and variant prices the catalog returned this turn or told this
    customer in an earlier one."""
    catalog = {float(p) for p in escalation_state.get("catalog_prices") or [] if isinstance(p, (int, float))}
    for snapshot in ((working_state or {}).get("product_facts") or {}).values():
        if not isinstance(snapshot, dict):
            continue
        for value in [snapshot.get("price"), *(snapshot.get("variant_prices") or [])]:
            if isinstance(value, (int, float)):
                catalog.add(float(value))
    return catalog


# Words that make a customer's number their budget ("500 minggacha",
# "около 300 000", "budget 50$"). Only those numbers are theirs to repeat —
# anything else they typed (a phone, an order code, a size) is not a price.
_BUDGET_TERMS = tuple(prompts.lexicon()["budget_terms"])
_BUDGET_WINDOW = get_settings().guard_budget_window_chars


def _customer_budget_numbers(text: str) -> set[float]:
    lowered = text.lower()
    found: set[float] = set()
    candidates = [(m, bool(m.group(2))) for m in _PRICE_RE.finditer(lowered)]
    candidates += [(m, False) for m in _BARE_NUMBER_RE.finditer(lowered)]
    for m, thousands in candidates:
        window = lowered[max(0, m.start() - _BUDGET_WINDOW) : m.end() + _BUDGET_WINDOW]
        if not any(term in window for term in _BUDGET_TERMS):
            continue
        value = _parse_amount(m.group(1), thousands)
        if value is not None:
            found.add(value)
    for m in re.finditer(r"(\d+)\s*ming", lowered):
        window = lowered[max(0, m.start() - _BUDGET_WINDOW) : m.end() + _BUDGET_WINDOW]
        if any(term in window for term in _BUDGET_TERMS):
            found.add(float(m.group(1)) * 1000)
    return found


# What a reply has to say for arithmetic on catalog prices to be legitimate:
# a quantity ("2 ta", "x3"), a total ("jami"), delivery ("yetkazib berish bilan").
_ARITHMETIC = prompts.lexicon()["price_arithmetic"]
_QUANTITY_RE = re.compile(
    r"(?<![\d.,])(\d{1,2})\s*-?\s*(?:"
    + "|".join(re.escape(u) for u in sorted(_ARITHMETIC["quantity_units"], key=len, reverse=True))
    + r")(?!\w)|(?<!\w)x\s*(\d{1,2})(?!\w)",
    re.IGNORECASE,
)
_TOTAL_WORDS = tuple(_ARITHMETIC["total_terms"])
_DELIVERY_WORDS = tuple(_ARITHMETIC["delivery_terms"])
_MAX_QUANTITY = 20


def _quantities(reply: str) -> set[int]:
    found = {1}
    for m in _QUANTITY_RE.finditer(reply):
        k = int(m.group(1) or m.group(2))
        if 1 <= k <= _MAX_QUANTITY:
            found.add(k)
    return found


def _allowed_prices(
    escalation_state: dict,
    working_state: dict | None,
    business: Business | None,
    customer_texts: list[str],
    reply: str,
) -> set[float]:
    """Every amount this reply may legitimately state: catalog and discounted
    prices, the delivery fee and discount thresholds the shop wrote, the
    customer's budget — and arithmetic only when the reply says so: a quantity
    it names ("2 ta"), a total ("jami") of two items, a price plus delivery."""
    catalog = _catalog_prices(escalation_state, working_state)
    discounts = escalation_state.get("active_discounts") or []
    lowered = reply.lower()

    unit_prices = set(catalog)
    discount_amounts: set[float] = set()
    for d in discounts:
        value = float(d.get("value") or 0)
        if d.get("type") == "percentage":
            unit_prices.update(round(p * (1 - value / 100), 2) for p in catalog)
            discount_amounts.update(round(p * value / 100, 2) for p in catalog)
        else:
            unit_prices.update(p - value for p in catalog)
            discount_amounts.add(value)

    # Only amounts the shop wrote with a currency, in the fields that hold fees.
    delivery_info = getattr(business, "delivery_info", None)
    fees = set(_quoted_prices(delivery_info)) if isinstance(delivery_info, str) else set()
    thresholds: set[float] = set()
    for d in discounts:
        for text in (d.get("description"), d.get("conditions")):
            if isinstance(text, str):
                thresholds.update(_quoted_prices(text))

    totals = {p * k for p in unit_prices for k in _quantities(reply)}
    if any(word in lowered for word in _TOTAL_WORDS):
        ordered = sorted(catalog)[:40]
        totals.update(a + b for i, a in enumerate(ordered) for b in ordered[i + 1 :])

    allowed = totals | fees | thresholds | discount_amounts
    if any(word in lowered for word in _DELIVERY_WORDS):
        allowed.update(t + f for t in totals for f in fees)
    for text in customer_texts:
        if isinstance(text, str) and text:
            allowed.update(_customer_budget_numbers(text))
    return allowed


def find_unverified_prices(
    reply: str,
    escalation_state: dict,
    working_state: dict | None,
    business: Business | None,
    customer_texts: list[str],
) -> list[float]:
    """Prices in the reply that nothing backs — the runtime guard and the eval
    check both use this."""
    allowed = _allowed_prices(escalation_state, working_state, business, customer_texts, reply)
    return [
        options[0] for options in _price_readings(reply)
        if not any(abs(q - a) <= max(1.0, a * _PRICE_TOLERANCE) for q in options for a in allowed)
    ]


def _normalize_for_comparison(text: str) -> str:
    return text.strip().lower().rstrip(".!?… ")


def _ensure_escalation_reply(
    reply: str,
    reason: str | None,
    business: Business,
    text_samples: list[str] | None = None,
) -> str:
    """request_human escalates the conversation to a human and the model's
    reply for that same turn is the customer's only signal that anything is
    happening before the AI goes quiet — but that reply has, in practice,
    sometimes come back as a near-verbatim echo of the tool call's own
    internal `reason` argument (a note *about* the customer, written for the
    backend — e.g. "Mijoz kimga murojaat qilishi kerakligini so'radi." read
    out loud *to* that same customer) or something too short/empty to tell
    them a human is actually coming. Rather than trust free-form LLM text
    alone for something this important, fall back to a guaranteed, clear
    closing line whenever the reply looks broken — matching the
    deterministic-backend-decides pattern used for phone/score (plan §8/§10),
    just applied to the handoff message instead."""
    reply_clean = (reply or "").strip()
    reason_clean = (reason or "").strip()

    looks_like_reason_echo = bool(reason_clean) and (
        _normalize_for_comparison(reply_clean) == _normalize_for_comparison(reason_clean)
    )
    looks_too_thin = len(reply_clean) < 15

    if reply_clean and not looks_like_reason_echo and not looks_too_thin:
        return reply

    lang = detect_preferred_language(business.language, text_samples)
    return replies.reply_text(business, "handoff", lang)


async def handle_customer_message(
    db: AsyncSession,
    provider: LLMProvider,
    business: Business,
    conversation: Conversation,
    customer_message_text: str,
    external_message_id: str | None = None,
    apply_lead_qualification: bool = False,
    attachment_url: str | None = None,
    attachment_type: str | None = None,
    escalation_state_out: dict | None = None,
    deliver: Callable[[list[Message]], Awaitable[None]] | None = None,
) -> ConversationTurnResult:
    """Persists the customer's message, then immediately runs one turn — the
    whole persist+reply pipeline in a single call. Used by tests and any
    non-debounced caller; the Instagram webhook path persists the message
    itself and calls run_turn() separately after the debounce window.

    `deliver` is passed straight through to run_turn — see there for when it
    fires and what raising from it means."""
    await add_message(
        db,
        conversation,
        sender_type="customer",
        content=customer_message_text,
        message_type=attachment_type if attachment_type else "text",
        external_message_id=external_message_id,
        attachment_url=attachment_url,
        attachment_type=attachment_type,
    )
    state: dict = {}
    result = await run_turn(
        db, provider, business, conversation, escalation_state_out=state, deliver=deliver
    )
    if escalation_state_out is not None:
        escalation_state_out.update(state)

    # A turn whose analysis failed after delivery carries placeholder scores —
    # persisting them would downgrade a lead on the strength of nothing.
    if apply_lead_qualification and conversation.customer_id and not state.get("analysis_failed"):
        from app.leads.service import apply_qualification
        await apply_qualification(
            db, business.id, conversation.customer_id, conversation.id, result, raw_message_text=customer_message_text
        )
    return result
