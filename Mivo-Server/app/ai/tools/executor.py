import datetime as dt
import json
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.conversation_state import fact_snapshot
from app.ai.usage_context import billing_to
from app.conversations.models import Conversation
from app.discounts.models import Discount
from app.products.discovery import (
    browse_products,
    list_categories,
    popular_products,
    similar_products,
)
from app.products.media import get_display_image_url
from app.products.models import Product
from app.products.search import check_availability, get_product_by_id, search_products, variant_matches
from app.core.config import get_settings
from app import prompts

# Tool results are re-sent on every later round, to the writer and to the
# analyst, so each product costs its size several times over. The model needs
# the facts, not the full marketing copy or the photo URLs (the backend sends
# photos itself — the model only ever names product ids).
_MAX_DESCRIPTION_CHARS = get_settings().tool_description_max_chars


def _variant_available(v) -> bool:
    return bool(v.availability and (v.stock_quantity is None or v.stock_quantity > 0))


def _product_summary(product: Product) -> dict:
    description = product.description or None
    if description and len(description) > _MAX_DESCRIPTION_CHARS:
        description = description[:_MAX_DESCRIPTION_CHARS].rstrip() + "…"
    # Empty fields are left out: every tool result is re-sent on each model
    # call, so nulls and {} are pure token cost.
    summary: dict[str, Any] = _compact({
        "id": str(product.id),
        "name": product.name,
        "description": description,
        "price": float(product.price) if product.price is not None else None,
        "currency": product.currency,
        "availability": product.availability,
        "has_photo": get_display_image_url(product) is not None,
    })
    summary["variants"] = [
        _compact({
            "sku": v.sku,
            "variant_type": v.variant_type,
            "value": v.value,
            "stock_quantity": v.stock_quantity,
            "has_photo": bool(v.image_url or v.images),
            "availability": _variant_available(v),
            "price": float(v.price_override) if v.price_override is not None else None,
            "attributes": v.attributes,
        })
        for v in product.variants
    ]
    if product.attributes:
        for key in ("ai_instructions", "material", "fit", "gender"):
            if product.attributes.get(key):
                summary[key] = product.attributes[key]
    return summary


def _compact(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in fields.items() if v is not None and v != {} and v != ""}


# A model that never saw a real product UUID this turn (e.g. it's working from a
# product name mentioned earlier in the chat text, not from a search_products
# result it just got back) sometimes invents an ID-shaped string instead of
# admitting it doesn't have one. A bare "invalid product_id" doesn't tell it what
# to do next, so it has reported that literal error to the customer before —
# spell out the fix so it self-corrects within the same turn instead.
_INVALID_PRODUCT_ID = {"error": prompts.load("tool_invalid_product_id.md")}


def _price_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_tool_executor(db: AsyncSession, business_id: uuid.UUID, conversation: Conversation):
    """Returns (execute, escalation_state) — escalation_state is a plain dict
    the caller (run_turn) reads AFTER the turn to know whether request_human
    fired, which products/prices the catalog actually returned, and which
    discounts are real. The reply guards check the model's text against it."""
    escalation_state: dict[str, Any] = {
        "escalated": False,
        "reason": None,
        "executed_tools": [],
        "active_discounts": [],
        # product_id -> compact fact snapshot, recorded straight off the tool
        # results so the conversation state carries prices the catalog actually
        # returned rather than ones the model remembered
        # (app/ai/conversation_state.py).
        "product_facts": {},
        # Every product/variant price a tool returned this turn — the price
        # guard's whitelist.
        "catalog_prices": [],
    }
    results_cache: dict[str, dict] = {}

    def _record_facts(*summaries: dict) -> None:
        for summary in summaries:
            if not summary or not summary.get("id"):
                continue
            escalation_state["product_facts"][summary["id"]] = fact_snapshot(summary)
            prices = [summary.get("price"), *[v.get("price") for v in summary.get("variants") or []]]
            escalation_state["catalog_prices"].extend(p for p in map(_price_or_none, prices) if p is not None)

    async def _execute(tool_name: str, arguments: dict[str, Any]) -> dict:
        if tool_name == "search_products":
            requested_size = arguments.get("size")
            requested_color = arguments.get("color")
            products = await search_products(
                db,
                business_id,
                query=arguments.get("query"),
                color=requested_color,
                size=requested_size,
                price_max=_price_or_none(arguments.get("price_max")),
            )
            summaries = [_product_summary(p) for p in products]
            _record_facts(*summaries)
            response: dict[str, Any] = {"products": summaries}

            # search_products() falls back to the query/price match alone when
            # the exact size/color isn't carried on anything, so the model still
            # sees the product's real variants instead of an empty list — but
            # without this note it can misread "product came back" as "the size
            # I asked for is in stock". Spell it out so it states the miss
            # honestly and offers what's actually available.
            def _has(variant_type: str, value: str) -> bool:
                return any(
                    _variant_available(v) and variant_matches(v, variant_type, value)
                    for p in products
                    for v in p.variants
                )

            if requested_size and not _has("size", requested_size):
                response["note"] = (
                    f"None of these products have size '{requested_size}' in stock. "
                    "Tell the customer that size isn't available, and mention which sizes "
                    "each product's variants list actually shows as available instead."
                )
            elif requested_color and not _has("color", requested_color):
                response["note"] = (
                    f"None of these products have color '{requested_color}' in stock. "
                    "Tell the customer that color isn't available, and mention which colors "
                    "each product's variants list actually shows as available instead."
                )
            return response

        if tool_name == "browse_catalog":
            categories = await list_categories(db, business_id)
            sort_by = arguments.get("sort_by")
            category = arguments.get("category")
            price_max = _price_or_none(arguments.get("price_max"))

            if sort_by == "popular" and not category and price_max is None:
                products = await popular_products(db, business_id)
            else:
                products = await browse_products(db, business_id, category=category, price_max=price_max)

            summaries = [_product_summary(p) for p in products]
            _record_facts(*summaries)
            browse_response: dict[str, Any] = {"categories": categories, "products": summaries}
            if category and not products:
                # Don't let the model report an empty category as "we have
                # nothing" — the categories list right there says otherwise.
                browse_response["note"] = (
                    f"Nothing available in '{category}' right now. Offer one of the other "
                    "categories listed instead of telling the customer the shop is empty."
                )
            elif not categories and products:
                browse_response["note"] = (
                    "This business hasn't categorised its products, so the category list is "
                    "empty. Describe what you can see in the products instead of asking the "
                    "customer to pick a category."
                )
            return browse_response

        if tool_name == "get_similar_products":
            try:
                anchor_id = uuid.UUID(str(arguments["product_id"]))
            except (KeyError, ValueError):
                return _INVALID_PRODUCT_ID
            products = await similar_products(db, business_id, anchor_id)
            summaries = [_product_summary(p) for p in products]
            _record_facts(*summaries)
            return {"products": summaries}

        if tool_name == "get_product":
            try:
                product_id = uuid.UUID(str(arguments["product_id"]))
            except (KeyError, ValueError):
                return _INVALID_PRODUCT_ID
            product = await get_product_by_id(db, business_id, product_id)
            summary = _product_summary(product) if product else None
            if summary:
                _record_facts(summary)
            return {"product": summary}

        if tool_name == "check_product_availability":
            try:
                product_id = uuid.UUID(str(arguments["product_id"]))
            except (KeyError, ValueError):
                return _INVALID_PRODUCT_ID
            return await check_availability(db, business_id, product_id, arguments.get("variant_value"))

        if tool_name == "get_active_discounts":
            now = dt.datetime.now(dt.timezone.utc)
            disc_res = await db.execute(
                select(Discount).where(
                    Discount.business_id == business_id,
                    Discount.active.is_(True),
                    Discount.valid_from <= now,
                    or_(Discount.valid_until.is_(None), Discount.valid_until >= now),
                )
            )
            disc_list = [
                {
                    "type": d.discount_type,
                    "value": float(d.value),
                    "description": d.description,
                    "conditions": d.conditions,
                    "code": d.code,
                }
                for d in disc_res.scalars().all()
            ]
            escalation_state["active_discounts"] = disc_list
            return {"discounts": disc_list}

        if tool_name == "request_human":
            reason = str(arguments.get("reason", ""))[:500]
            escalation_state["escalated"] = True
            escalation_state["reason"] = reason
            # The status itself is set by the reply hook (run_turn), together
            # with the reply and only if no person took over meanwhile.
            return {"escalated": True, "reason": reason}

        return {"error": f"unknown tool: {tool_name}"}

    async def execute(tool_name: str, arguments: dict[str, Any]) -> dict:
        escalation_state["executed_tools"].append({"name": tool_name, "arguments": arguments})
        # The same call twice in one turn returns the same answer without
        # another round of queries (and the same result, so the model can't be
        # shown two different "truths" in one turn).
        key = f"{tool_name}:{json.dumps(arguments, sort_keys=True, default=str)}"
        if key in results_cache and tool_name != "request_human":
            return results_cache[key]
        with billing_to(business_id):
            result = await _execute(tool_name, arguments)
        results_cache[key] = result
        return result

    return execute, escalation_state
