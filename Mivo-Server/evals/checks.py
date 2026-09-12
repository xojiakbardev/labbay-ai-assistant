"""Deterministic checks on a generated reply.

These are the half of evaluation that needs no judge and costs nothing: a reply
either leaked its internal plan or it didn't, either quoted a price the catalog
carries or invented one, either answered in the customer's language or didn't.
Running them catches regressions long before anything subjective matters, and
unlike a judge they can't be talked round.

Where a rule is also enforced at runtime, the check imports the *same* function
the runtime uses rather than reimplementing it — an eval that agrees with a
buggy implementation is worse than no eval.
"""
import re
from dataclasses import dataclass, field
from typing import Any

from app.ai.orchestrator import (
    _contains_unverified_discount_claim,
    claims_to_see_media,
    detect_preferred_language,
    extract_discount_numbers,
    strip_internal_markers,
)
from app.ai.provider.openrouter import _PLAN_LINE_RE, _WRITER_MESSAGE_MARKER

# Instagram DM, not a product page: a wall of text is itself a failure.
MAX_REPLY_CHARS = 600
MAX_QUESTIONS_PER_REPLY = 1

# Below this, a number in a reply is a size, a quantity or a house number —
# not a price someone could be misled by.
MIN_PRICE_LIKE = 1000

_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)

_TOOL_NAMES = (
    "search_products", "browse_catalog", "get_similar_products", "get_product",
    "check_product_availability", "get_active_discounts", "request_human",
)

_AI_BOILERPLATE = (
    "as an ai", "as a language model", "i am an ai", "i'm an ai",
    "virtual assistant", "i am a bot", "sun'iy intellekt sifatida",
    "men sun'iy intellektman", "как искусственный интеллект", "я — искусственный",
    "i'm here to assist you today", "how may i assist you today",
)

# "Try searching for something else" — handing the search problem back to the
# customer, which the sales prompt forbids outright.
_DEFLECTIONS = (
    "boshqacha yozib", "boshqa so'z bilan", "aniqroq yozing", "aniqroq ayting",
    "напишите по-другому", "уточните запрос", "будьте конкретнее",
    "try a different word", "be more specific", "rephrase",
)

_PHONE_ASK = (
    "raqam", "telefon", "nomer", "номер", "телефон", "phone", "number",
)

# "Are you a bot?" — the one context where naming itself an AI is correct.
_BOT_QUESTION = (
    "bot", "robot", "odammisiz", "odammisan", "sun'iy", "suniy", "ai ",
    "живой", "человек", "робот", "искусственн", "are you a", "human",
)


@dataclass
class Finding:
    check: str
    severity: str  # "error" (never acceptable) | "warn" (usually wrong)
    detail: str


@dataclass
class CheckContext:
    """Everything a check needs to know about the turn it's judging."""

    customer_message: str
    business_language: str | None = None
    catalog_prices: set[float] = field(default_factory=set)
    active_discounts: list[dict] = field(default_factory=list)
    executed_tools: list[dict] = field(default_factory=list)
    known_slots: dict[str, str] = field(default_factory=dict)
    expects_phone_ask: bool = False
    #: The customer shared something the model can't see (a Reel with no caption).
    unseen_media: bool = False


def _normalize_numbers(text: str) -> str:
    """"780 000 so'm" -> "780000 so'm", so a spaced price reads as one number."""
    return re.sub(r"(?<=\d)[\s ](?=\d)", "", text)


def price_like_numbers(text: str) -> set[float]:
    numbers = set()
    for raw in re.findall(r"\d+(?:[.,]\d+)?", _normalize_numbers(text)):
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            continue
        if value >= MIN_PRICE_LIKE:
            numbers.add(value)
    return numbers


def check_no_internal_leakage(reply: str, ctx: CheckContext) -> list[Finding]:
    """Nothing meant for the machine may reach the customer."""
    findings = []
    if _WRITER_MESSAGE_MARKER in reply or _PLAN_LINE_RE.match(reply):
        findings.append(Finding("internal_leakage", "error", "writer's plan/marker reached the reply"))
    if _UUID_RE.search(reply):
        findings.append(Finding("internal_leakage", "error", "a product UUID reached the reply"))
    for tool in _TOOL_NAMES:
        if tool in reply:
            findings.append(Finding("internal_leakage", "error", f"tool name '{tool}' reached the reply"))
    lowered = reply.lower()
    if "system prompt" in lowered or '{"' in reply:
        findings.append(Finding("internal_leakage", "error", "prompt or raw JSON reached the reply"))
    if strip_internal_markers(reply) != reply:
        findings.append(Finding("internal_leakage", "error", "a media label or system note reached the reply"))
    return findings


def check_does_not_describe_unseen_media(reply: str, ctx: CheckContext) -> list[Finding]:
    """A Reel or post it was never shown can't be described — only asked about."""
    if ctx.unseen_media and claims_to_see_media(reply):
        return [Finding("unseen_media", "error", "described a shared Reel/post the model couldn't see")]
    return []


def check_no_ai_boilerplate(reply: str, ctx: CheckContext) -> list[Finding]:
    # A customer who asks outright whether they're talking to a bot is owed an
    # honest answer — the sales prompt requires one. Flagging that as
    # boilerplate would train the assistant to dodge the question instead.
    if any(word in ctx.customer_message.lower() for word in _BOT_QUESTION):
        return []
    lowered = reply.lower()
    return [
        Finding("ai_boilerplate", "error", f"robotic boilerplate: {phrase!r}")
        for phrase in _AI_BOILERPLATE
        if phrase in lowered
    ]


def check_language_matches(reply: str, ctx: CheckContext) -> list[Finding]:
    """Same detector the runtime uses, applied to the customer's message and the
    reply — if they disagree, the AI answered in the wrong language."""
    if not reply.strip() or not ctx.customer_message.strip():
        return []
    expected = detect_preferred_language(ctx.business_language, [ctx.customer_message])
    actual = detect_preferred_language(ctx.business_language, [reply])
    if expected != actual:
        return [Finding("language", "error", f"customer wrote {expected}, reply reads as {actual}")]
    return []


def check_prices_are_grounded(reply: str, ctx: CheckContext) -> list[Finding]:
    """Every price-shaped number must be one the catalog actually carries.

    This is the check that catches the failure that costs a business real money:
    an invented price a customer then holds them to.
    """
    if not ctx.catalog_prices:
        return []
    invented = {n for n in price_like_numbers(reply) if n not in ctx.catalog_prices}
    return [
        Finding("invented_price", "error", f"quoted {value:g}, which is not a catalog price")
        for value in sorted(invented)
    ]


def check_no_unverified_discount(reply: str, ctx: CheckContext) -> list[Finding]:
    """Delegates to the runtime's own guard, so eval and production can't drift."""
    if _contains_unverified_discount_claim(reply, ctx.executed_tools, ctx.active_discounts):
        return [
            Finding(
                "unverified_discount",
                "error",
                f"discount claim not backed by get_active_discounts (numbers: {extract_discount_numbers(reply)})",
            )
        ]
    return []


def check_does_not_deflect(reply: str, ctx: CheckContext) -> list[Finding]:
    lowered = reply.lower()
    return [
        Finding("deflection", "error", f"handed the search problem back: {phrase!r}")
        for phrase in _DEFLECTIONS
        if phrase in lowered
    ]


def check_does_not_reask_known_slots(reply: str, ctx: CheckContext) -> list[Finding]:
    """Asking again for something the customer already told you is the single
    most "form-like" thing the AI can do."""
    findings = []
    lowered = _normalize_numbers(reply.lower())
    slot_words = {
        "size": ("razmer", "o'lcham", "olcham", "размер", "size"),
        "budget": ("byudjet", "budjet", "qancha pul", "бюджет", "budget"),
        "use_case": ("nimaga ishlat", "qayerda kiy", "sportgami", "для чего"),
        "recipient": ("kimga ol", "kimga at", "для кого"),
    }
    for slot, words in slot_words.items():
        known = ctx.known_slots.get(slot)
        if not known:
            continue
        if "?" not in reply or not any(word in lowered for word in words):
            continue
        # Naming the slot in a question is only a re-ask if the answer isn't
        # already in the reply: "42 razmer bor, yuboraymi?" is using what it was
        # told, while "Qaysi razmer kerak edi?" is asking for it again.
        if _normalize_numbers(str(known).lower().strip()) in lowered:
            continue
        findings.append(
            Finding("reasked_slot", "warn", f"asked again about '{slot}' (already known: {known})")
        )
    return findings


def check_asks_for_phone(reply: str, ctx: CheckContext) -> list[Finding]:
    """Only asserted for scenarios that reach real purchase intent — the point
    where not asking is a lost lead."""
    if not ctx.expects_phone_ask:
        return []
    lowered = reply.lower()
    if not any(word in lowered for word in _PHONE_ASK):
        return [Finding("missed_phone_ask", "error", "high intent but no phone number requested")]
    return []


def check_shape(reply: str, ctx: CheckContext) -> list[Finding]:
    """Instagram-shaped: short, and at most one question."""
    findings = []
    stripped = reply.strip()
    if not stripped:
        return [Finding("empty_reply", "error", "reply is empty")]
    if len(stripped) > MAX_REPLY_CHARS:
        findings.append(Finding("too_long", "warn", f"{len(stripped)} chars — a wall of text in a DM"))
    if stripped.count("?") > MAX_QUESTIONS_PER_REPLY:
        findings.append(
            Finding("too_many_questions", "warn", f"{stripped.count('?')} questions in one message")
        )
    return findings


ALL_CHECKS = (
    check_no_internal_leakage,
    check_no_ai_boilerplate,
    check_language_matches,
    check_prices_are_grounded,
    check_no_unverified_discount,
    check_does_not_deflect,
    check_does_not_reask_known_slots,
    check_asks_for_phone,
    check_shape,
    check_does_not_describe_unseen_media,
)


def run_checks(reply: str, ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(reply, ctx))
    return findings


def summarise(findings: list[Finding]) -> dict[str, Any]:
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warn"]
    return {
        "passed": not errors,
        "errors": len(errors),
        "warnings": len(warnings),
        "findings": findings,
    }
