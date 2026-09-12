"""The sale-in-progress carried between turns (`conversations.working_state`).

Message history is not enough to keep a conversation coherent. Tool results are
never persisted, so on every new customer message the model starts with no
product facts in view: it re-searches, may get a different result, and can
contradict a price or a stock answer it gave two messages earlier. The history
window compounds it — once the turn where a price was quoted scrolls out, the
quote is simply gone.

So the facts are kept here instead, and they are recorded from the tool results
themselves rather than asked of the model: a price in this state is a price the
catalog actually returned, not one the model remembered. Only the parts that
genuinely need judgement — what stage the sale is at, what question is
outstanding, what the customer pushed back on — come from the analyst pass.
"""
import datetime as dt
import uuid
from typing import Any, get_args

from app.core.config import DiscoverySlot, get_settings

# Enough to keep the current thread of the sale coherent without turning the
# system prompt into a second catalog.
MAX_TRACKED_PRODUCTS = get_settings().state_max_tracked_products
MAX_TRACKED_OBJECTIONS = get_settings().state_max_tracked_objections
MAX_VARIANTS_PER_PRODUCT = get_settings().state_max_variants_per_product

STAGES = ("greeting", "discovery", "recommendation", "objection", "closing", "handoff")

# What a salesperson needs to know before they can recommend anything. Tracked
# so the AI can see what it's still missing instead of waiting for the customer
# to volunteer it — a customer who has to supply all of this unprompted is
# writing a search query, not having a conversation.
DISCOVERY_SLOTS = get_args(DiscoverySlot)

# The subset actually worth chasing. Colour and who it's for are useful when
# offered but not worth interrogating anyone about.
ESSENTIAL_SLOTS = tuple(get_settings().essential_slots)


def fact_snapshot(product_summary: dict[str, Any]) -> dict[str, Any]:
    """Compresses a tool result into what a salesperson would actually remember:
    the name, what it costs, and what's on the shelf."""
    variants = product_summary.get("variants") or []
    in_stock = [
        str(v.get("value"))
        for v in variants
        if v.get("availability") and v.get("value")
    ]
    snapshot: dict[str, Any] = {
        "name": product_summary.get("name"),
        "price": product_summary.get("price"),
        "currency": product_summary.get("currency"),
        "available": bool(product_summary.get("availability")),
    }
    if in_stock:
        snapshot["in_stock"] = in_stock[:MAX_VARIANTS_PER_PRODUCT]
    # Variants priced differently from the product (a bigger size costing
    # more): a price told to the customer in an earlier turn stays one the
    # price guard recognises.
    variant_prices = sorted(
        {float(v["price"]) for v in variants if isinstance(v.get("price"), (int, float))}
        - {product_summary.get("price")}
    )
    if variant_prices:
        snapshot["variant_prices"] = variant_prices[:MAX_VARIANTS_PER_PRODUCT]
    return snapshot


def _clean_str(value: Any, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


def _valid_uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def parse_slots(slots_learned: list[str] | None) -> dict[str, str]:
    """Turns the analyst's "key: value" lines into known discovery slots.

    Takes the same shape as extracted_facts because that shape has proved
    reliable in practice — a flat list of short strings, rather than a nested
    object the model has to keep straight while generating constrained JSON.
    Anything that isn't a recognised slot is dropped rather than guessed at.
    """
    parsed: dict[str, str] = {}
    for entry in slots_learned or []:
        if not isinstance(entry, str) or ":" not in entry:
            continue
        key, _, value = entry.partition(":")
        key = key.strip().lower().replace(" ", "_").replace("-", "_")
        value = value.strip()
        if key in DISCOVERY_SLOTS and value:
            parsed[key] = value[:60]
    return parsed


def update_state(
    previous: dict[str, Any] | None,
    *,
    product_facts: dict[str, dict] | None = None,
    stage: str | None = None,
    open_question: str | None = None,
    new_objection: str | None = None,
    interested_product_ids: list[str] | None = None,
    slots_learned: list[str] | None = None,
    escalated: bool = False,
) -> dict[str, Any]:
    """Folds one turn into the state. Returns a NEW dict — `working_state` is a
    JSONB column and mutating it in place won't mark it dirty.

    Facts already recorded are kept unless this turn looked the product up
    again, in which case the fresh result wins: stock moves, and a stale
    snapshot is worse than none.
    """
    state: dict[str, Any] = dict(previous or {})

    facts: dict[str, dict] = dict(state.get("product_facts") or {})
    for raw_id, snapshot in (product_facts or {}).items():
        product_id = _valid_uuid(raw_id)
        if product_id:
            # Re-inserted so the newest lookups are the ones that survive the cap.
            facts.pop(product_id, None)
            facts[product_id] = snapshot
    if len(facts) > MAX_TRACKED_PRODUCTS:
        facts = dict(list(facts.items())[-MAX_TRACKED_PRODUCTS:])
    state["product_facts"] = facts

    # Whatever the model said the customer is interested in, but only if it's a
    # real product we actually looked up — never a name or an invented ID.
    focus = next(
        (pid for pid in (_valid_uuid(p) for p in (interested_product_ids or [])) if pid and pid in facts),
        None,
    )
    if focus:
        state["focus_product_id"] = focus
    elif state.get("focus_product_id") not in facts:
        state.pop("focus_product_id", None)

    if escalated:
        state["stage"] = "handoff"
    elif stage in STAGES:
        state["stage"] = stage

    # An open question only survives the turn that asked it: by the next turn
    # the customer has answered it, ignored it, or changed the subject, and a
    # stale "waiting on their size" makes the AI ask again.
    question = _clean_str(open_question)
    if question:
        state["open_question"] = question
    else:
        state.pop("open_question", None)

    # Slots accumulate — something the customer told you three messages ago is
    # still true, and re-asking it is the fastest way to sound like a form.
    newly_learned = parse_slots(slots_learned)
    if newly_learned:
        slots = {
            k: v
            for k, v in (state.get("slots") or {}).items()
            if k in DISCOVERY_SLOTS and isinstance(v, str)
        }
        slots.update(newly_learned)
        state["slots"] = slots

    objection = _clean_str(new_objection, limit=120)
    if objection:
        objections = [o for o in (state.get("objections") or []) if isinstance(o, str)]
        if objection.lower() not in {o.lower() for o in objections}:
            objections.append(objection)
        state["objections"] = objections[-MAX_TRACKED_OBJECTIONS:]

    state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return state


def _inert(value: Any, limit: int = 120) -> str:
    """Analyst-recorded text that ultimately came from the customer, rendered
    as a bounded quoted value — context, never an instruction."""
    cleaned = " ".join(str(value).split())[:limit]
    return '"' + cleaned.replace('"', "'") + '"'


def _price(value: Any) -> str:
    """1250000 -> "1 250 000" (never "1.25e+06"); cents kept when present."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"
    return text.replace(",", " ")


def render_state_block(state: dict[str, Any] | None) -> str:
    """Renders the state for the system prompt. Empty string when there's
    nothing worth saying, so a brand-new conversation carries no dead weight."""
    if not state:
        return ""

    lines: list[str] = []

    stage = state.get("stage")
    if stage in STAGES:
        lines.append(f"- Stage of this sale: {stage}")

    facts = state.get("product_facts") or {}
    focus_id = state.get("focus_product_id")
    if facts:
        lines.append(
            "- Products you looked up for this customer, with the price/stock the catalog returned "
            "at the time (you may not have mentioned all of them; re-check with a tool before "
            "repeating any of it — stock moves):"
        )
        for product_id, snapshot in facts.items():
            if not isinstance(snapshot, dict):
                continue
            bits = [str(snapshot.get("name") or "?")]
            price = snapshot.get("price")
            if price is not None:
                currency = snapshot.get("currency") or ""
                bits.append(f"{_price(price)} {currency}".strip())
            variant_prices = snapshot.get("variant_prices")
            if variant_prices:
                bits.append("some variants: " + ", ".join(_price(p) for p in variant_prices))
            if not snapshot.get("available", True):
                bits.append("was out of stock")
            in_stock = snapshot.get("in_stock")
            if in_stock:
                bits.append("available: " + ", ".join(str(v) for v in in_stock))
            marker = "  [IN FOCUS] " if product_id == focus_id else "  - "
            lines.append(f"{marker}{' | '.join(bits)} (id: {product_id})")
        if focus_id and focus_id in facts:
            lines.append(
                "  IN FOCUS is the product this conversation is currently about — when they say "
                "\"it\", \"shuni\", \"buni\" or \"этот\", that's what they mean."
            )

    slots = {k: v for k, v in (state.get("slots") or {}).items() if k in DISCOVERY_SLOTS}
    if slots:
        lines.append(
            "- What they told you they need (their words): "
            + " | ".join(f"{k}: {_inert(v)}" for k, v in slots.items())
            + "  (never ask for any of this again)"
        )

    question = state.get("open_question")
    if question:
        lines.append(f"- You asked them this and haven't been answered yet: {_inert(question)}")

    objections = state.get("objections")
    if objections:
        lines.append("- They've pushed back on: " + "; ".join(_inert(o) for o in objections))

    # Only chase the gaps once the conversation has actually started — a bare
    # "you don't know anything yet" on someone's first hello is noise, and the
    # opening message is the one place the prompt's own guidance is enough.
    if lines and state.get("stage") != "handoff":
        missing = [slot for slot in ESSENTIAL_SLOTS if slot not in slots]
        if missing:
            lines.append(
                "- Still unknown: "
                + ", ".join(missing)
                + ". Ask for what you genuinely need before recommending — one at a time, "
                "phrased as a choice, and only when it changes what you'd show them."
            )

    if not lines:
        return ""
    return (
        "\n\nCONVERSATION STATE (where this sale actually stands — carried over from earlier turns):\n"
        + "\n".join(lines)
    )
