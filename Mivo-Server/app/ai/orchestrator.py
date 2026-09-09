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

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.ai.context.builder import build_message_history, build_system_prompt_with_learnings
from app.ai.provider.base import LLMProvider, LLMProviderError
from app.ai.tools.definitions import ALL_TOOLS
from app.ai.tools.executor import build_tool_executor
from app.businesses.models import Business
from app.conversations.models import Conversation
from app.conversations.service import add_message, get_recent_messages

FALLBACK_REPLY = "Kechirasiz, hozircha javob bera olmadim — tez orada siz bilan bog'lanamiz."


def detect_preferred_language(
    business_language: str | None = None,
    text_samples: list[str] | None = None,
) -> str:
    """Detects 'uz', 'ru', or 'en' based on recent conversation text and business language.
    Prevents accidentally defaulting to Russian when business has 'o'zbek, rus, ingliz'.
    """
    if text_samples:
        combined = " ".join(t for t in text_samples if t).lower()
        has_cyrillic = bool(re.search(r"[а-яё]", combined))
        uzbek_markers = [
            "salom", "assalomu", "rahmat", "qanaqa", "qancha", "bor mi", "bormi",
            "kerak", "narxi", "xa", "ha", "yoq", "yo'q", "bo'ladi", "boladi",
            "siz", "kepka", "yordam", "otding", "tilidan", "tushundim", "aka", "uka",
        ]
        has_uzbek = any(re.search(rf"\b{re.escape(m)}\b", combined) for m in uzbek_markers) or "o'" in combined or "g'" in combined or "oʻ" in combined or "gʻ" in combined
        if has_uzbek and not has_cyrillic:
            return "uz"
        if has_cyrillic:
            return "ru"
        english_markers = ["hello", "hi", "price", "how much", "discount", "thank you", "thanks", "do you have", "what", "please"]
        if any(re.search(rf"\b{re.escape(m)}\b", combined) for m in english_markers):
            return "en"

    biz_lang = (business_language or "").strip().lower()
    if biz_lang.startswith("uz") or "o'zbek" in biz_lang or "ozbek" in biz_lang:
        return "uz"
    if biz_lang.startswith("ru") or "rus" in biz_lang:
        return "ru"
    if biz_lang.startswith("en") or "eng" in biz_lang:
        return "en"
    return "uz"


_PHONE_CAPTURED_CONFIRMATION = {
    "uz": "Rahmat! Telefon raqamingiz qabul qilindi, operatorimiz tez orada siz bilan bog'lanadi.",
    "ru": "Спасибо! Ваш номер телефона принят, наш оператор скоро свяжется с вами.",
    "en": "Thank you! Your phone number has been received, our team will contact you shortly.",
}


class ConversationTurnResult(BaseModel):
    """The LLM's fused final output for one turn — reply + self-assessed
    qualification. FastAPI treats every field here as a *proposal*: the phone
    override and score-band clamping (Phase 8) are what actually decide the
    persisted lead status, never this object directly."""

    reply: str
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
            "0-100, matching lead_status's band (0-34 cold, 35-69 warm, 70-100 hot). Your "
            "honest read of how close this customer is to buying right now — based only on "
            "this exact message and the conversation so far, not on how many messages "
            "you've exchanged or how promising it looked a few turns ago."
        ),
    )
    qualification_reason: str = Field(
        description=(
            "One short, concrete sentence for THIS score specifically — what they just said "
            "or did that justifies it. If interest cooled or they cancelled, say that "
            "plainly instead of repeating an earlier, now-stale reason."
        )
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
) -> ConversationTurnResult:
    """Runs one agentic LLM turn against whatever's already persisted in the
    conversation's message history, and persists the AI's reply. Does NOT
    persist a new customer message itself — the Instagram webhook path
    (app/instagram/service.py) debounces bursts of rapid customer messages,
    persisting each one immediately but only calling this once the customer
    has gone quiet for a few seconds, so one reply answers everything they
    sent instead of firing a separate reply per message. Callers that haven't
    already recorded the customer's message should use handle_customer_message
    instead."""
    history = await get_recent_messages(db, conversation.id)
    system_prompt = await build_system_prompt_with_learnings(db, business, conversation.customer_id)
    messages = build_message_history(history)
    executor, escalation_state = build_tool_executor(db, business.id, conversation)

    try:
        result = await provider.run_agentic_turn(
            system_prompt=system_prompt,
            messages=messages,
            tools=ALL_TOOLS,
            response_schema=ConversationTurnResult,
            tool_executor=executor,
            business_id=business.id,
        )
    except LLMProviderError as exc:
        logger.error(
            "AI provider turn failed after all retries and fallbacks for business %s: %s",
            business.id,
            exc,
        )

        recent_texts = [getattr(m, "content", "") for m in (history[-3:] if history else []) if getattr(m, "content", None)]
        pref_lang = detect_preferred_language(business.language, recent_texts)

        if has_latest_image:
            # Friendly fallback asking for text description instead of immediate escalation failure
            if pref_lang == "ru":
                reply_text = "Извините, возникла ошибка при обработке фото. Опишите, пожалуйста, нужный товар (тип, цвет, модель) текстом — я сразу проверю наличие!"
            elif pref_lang == "en":
                reply_text = "Apologies, there was an issue viewing the photo. Could you please describe what product (type, color, model) you're looking for in text?"
            else:
                reply_text = "Kechirasiz, yuborgan rasmingizni ochishda texnik nosozlik bo'ldi. Iltimos, qidirayotgan mahsulotingizni (turi, rangi, modeli) matn ko'rinishida yozib yubora olasizmi?"

            result = ConversationTurnResult(
                reply=reply_text,
                lead_status="warm",
                lead_score=40,
                qualification_reason="Image processing fallback — asked customer to describe product in text.",
            )
        else:
            escalation_state["escalated"] = True
            escalation_state["reason"] = f"AI provider error: {exc}"
            conversation.status = "human_needed"
            await db.flush()

            reply_text = _ESCALATION_CLOSING.get(pref_lang, _ESCALATION_CLOSING["uz"])

            result = ConversationTurnResult(
                reply=reply_text,
                lead_status="cold",
                lead_score=0,
                qualification_reason="AI provider failed; escalated to human operator.",
            )

    if escalation_state["escalated"]:
        conversation.status = "human_needed"
        await db.flush()
        recent_texts = [getattr(m, "content", "") for m in (history[-3:] if history else []) if getattr(m, "content", None)]
        fixed_reply = _ensure_escalation_reply(result.reply, escalation_state["reason"], business, text_samples=recent_texts)
        if fixed_reply != result.reply:
            result = result.model_copy(update={"reply": fixed_reply})

    # Layer 2: Defense-in-depth post-generation discount guard
    executed_tools = escalation_state.get("executed_tools", [])
    active_discounts = escalation_state.get("active_discounts", [])
    flagged_for_review = False

    if _contains_unverified_discount_claim(result.reply, executed_tools, active_discounts):
        logger.warning(
            "Unverified discount claim intercepted in reply for business %s. Original: %s",
            business.id,
            result.reply,
        )
        flagged_for_review = True
        recent_texts = [getattr(m, "content", "") for m in (history[-3:] if history else []) if getattr(m, "content", None)]
        disc_lang = detect_preferred_language(business.language, recent_texts)
        if disc_lang == "ru":
            safe_reply = "Извините, точную информацию о скидках я сейчас подтвердить не могу, наш оператор проверит и обязательно свяжется с вами."
        elif disc_lang == "en":
            safe_reply = "Apologies, I cannot confirm a specific discount right now. Our team will verify and get back to you shortly."
        else:
            safe_reply = "Aniq chegirma haqida hozir tasdiqlab bera olmayman, operatorimiz tekshirib sizga ma'lumot beradi."

        result = result.model_copy(update={"reply": safe_reply})

    await add_message(
        db, conversation, sender_type="ai", content=result.reply, flagged_for_review=flagged_for_review
    )
    if escalation_state_out is not None:
        escalation_state_out.update(escalation_state)
    return result


_DISCOUNT_TERMS = [
    "chegirma", "aksiya", "arzon qilib", "skidka", "скидк", "акци",
    "discount", "% off", "foiz chegirma", "promo", "promokod", "kupon", "купон"
]


def extract_discount_numbers(text: str) -> list[float]:
    # Normalize thousand separators like "50 000" -> "50000"
    cleaned = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    pct_matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:%|foiz|процент|percent)", cleaned, re.IGNORECASE)
    numbers = [float(x.replace(",", ".")) for x in pct_matches]
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


def _contains_unverified_discount_claim(
    response_text: str,
    executed_tools: list[dict],
    active_discounts: list[dict],
) -> bool:
    """Detects whether the LLM generated an unverified discount or promotional promise."""
    t = response_text.lower()
    has_discount_term = any(term in t for term in _DISCOUNT_TERMS)
    claimed_numbers = extract_discount_numbers(t)

    # 1. If text explicitly mentions discount terms and quotes numbers
    if has_discount_term and claimed_numbers:
        called_tool = any(call.get("name") == "get_active_discounts" for call in executed_tools)
        if not called_tool:
            return True
        verified_values = {float(d.get("value", 0)) for d in active_discounts}
        if any(num not in verified_values for num in claimed_numbers):
            return True

    # 2. If it is an explicit denial (e.g. 'chegirma yo'q', 'chegirma mavjud emas'), do not flag
    if re.search(r"(?:chegirma|skidka|скидк|discount)\w*\s+(?:yo'?q|emas|bo'?lmaydi|нет|не\s+предусмотрен|none|not\s+available)", t):
        return False

    # 3. Check promises of discounts without specific numbers if no active discounts exist
    promise_patterns = [
        r"chegirma\s+(?:qilib\s+)?ber(?:amiz|aman|iladi)",
        r"chegirma\s+mavjud(?!\s+(?:emas|yo'?q))",
        r"aksiyamiz\s+bor(?!\s+(?:emas|yo'?q))",
        r"arzon\s+qilib\s+ber(?:amiz|aman)",
        r"сдела(?:ем|ю)\s+вам\s+скидку",
        r"скидк[ау]\s+предостав",
        r"give\s+you\s+a\s+discount",
        r"offer\s+a\s+discount",
    ]
    if any(re.search(pat, t) for pat in promise_patterns):
        called_tool = any(call.get("name") == "get_active_discounts" for call in executed_tools)
        if not called_tool or not active_discounts:
            return True

    return False


_ESCALATION_CLOSING = {
    "ru": "Хорошо, я передал(а) информацию нашему оператору — он скоро свяжется с вами.",
    "en": "Got it — I've passed this to our team, they'll reach out to you shortly.",
    "uz": "Tushunarli, ma'lumotingizni operatorimizga uzatdim — tez orada siz bilan bog'lanishadi.",
}


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
    return _ESCALATION_CLOSING.get(lang, _ESCALATION_CLOSING["uz"])


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
) -> ConversationTurnResult:
    """Persists the customer's message, then immediately runs one turn — the
    whole persist+reply pipeline in a single call. Used by tests and any
    non-debounced caller; the Instagram webhook path persists the message
    itself and calls run_turn() separately after the debounce window."""
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
    result = await run_turn(db, provider, business, conversation, escalation_state_out=escalation_state_out)
    if apply_lead_qualification and conversation.customer_id:
        from app.leads.service import apply_qualification
        await apply_qualification(
            db, business.id, conversation.customer_id, conversation.id, result, raw_message_text=customer_message_text
        )
    return result
