import datetime as dt
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import Conversation
from app.discounts.models import Discount
from app.products.media import get_display_image_url
from app.products.models import Product
from app.products.search import check_availability, get_product_by_id, search_products


def _product_summary(product: Product) -> dict:
    summary: dict[str, Any] = {
        "id": str(product.id),
        "name": product.name,
        "description": product.description,
        "price": float(product.price) if product.price is not None else None,
        "currency": product.currency,
        "availability": product.availability,
        "has_photo": get_display_image_url(product) is not None,
        "variants": [
            {
                "sku": v.sku,
                "name": v.value,
                "variant_type": v.variant_type,
                "value": v.value,
                "stock_quantity": v.stock_quantity,
                "image_url": v.image_url,
                "has_photo": bool(v.image_url or (v.images and len(v.images) > 0)),
                "photos": v.images or ([v.image_url] if v.image_url else []),
                "availability": v.availability and (v.stock_quantity is None or v.stock_quantity > 0),
                "price": float(v.price_override) if v.price_override is not None else None,
                "attributes": v.attributes or {},
            }
            for v in product.variants
        ],
    }
    if product.attributes:
        if product.attributes.get("ai_instructions"):
            summary["ai_instructions"] = product.attributes["ai_instructions"]
        if product.attributes.get("material"):
            summary["material"] = product.attributes["material"]
        if product.attributes.get("fit"):
            summary["fit"] = product.attributes["fit"]
        if product.attributes.get("gender"):
            summary["gender"] = product.attributes["gender"]
    return summary


def build_tool_executor(db: AsyncSession, business_id: uuid.UUID, conversation: Conversation):
    """Returns (execute, escalation_state) — escalation_state is a plain dict
    the caller (run_turn) reads AFTER the turn to know whether request_human
    fired, so it can guarantee the customer actually gets told a human is
    coming instead of trusting the model's own final text alone (see the bug
    this guards against: the model has echoed its own internal `reason`
    argument back as the customer-facing reply, e.g. "Mijoz kimga murojaat
    qilishi kerakligini so'radi." — a note about the customer, read out loud
    to the customer)."""
    escalation_state: dict[str, Any] = {
        "escalated": False,
        "reason": None,
        "executed_tools": [],
        "active_discounts": [],
    }

    async def execute(tool_name: str, arguments: dict[str, Any]) -> dict:
        escalation_state["executed_tools"].append({"name": tool_name, "arguments": arguments})
        if tool_name == "search_products":
            requested_size = arguments.get("size")
            requested_color = arguments.get("color")
            products = await search_products(
                db,
                business_id,
                query=arguments.get("query"),
                color=requested_color,
                size=requested_size,
                price_max=arguments.get("price_max"),
            )
            response: dict[str, Any] = {"products": [_product_summary(p) for p in products]}

            # search_products() falls back to the query/price match alone when
            # the exact size/color isn't carried on anything, so the model still
            # sees the product's real variants instead of an empty list — but
            # without this note it can misread "product came back" as "the size
            # I asked for is in stock". Spell it out so it states the miss
            # honestly and offers what's actually available (plan §9's "never
            # invent availability" — see the bug this fixed: a customer asking
            # for a size that isn't carried getting told the whole product,
            # sizes included, was out of stock).
            def _has_variant(p, variant_type: str, value: str) -> bool:
                target = value.strip().lower()
                for v in p.variants:
                    is_available = v.availability and (v.stock_quantity is None or v.stock_quantity > 0)
                    if not is_available:
                        continue
                    if v.variant_type == variant_type and v.value.lower() == target:
                        return True
                    if target in v.value.lower():
                        return True
                    if v.attributes and target in str(v.attributes.get(variant_type, "")).lower():
                        return True
                return False

            if requested_size and not any(_has_variant(p, "size", requested_size) for p in products):
                response["note"] = (
                    f"None of these products have size '{requested_size}' in stock. "
                    "Tell the customer that size isn't available, and mention which sizes "
                    "each product's variants list actually shows as available instead."
                )
            elif requested_color and not any(_has_variant(p, "color", requested_color) for p in products):
                response["note"] = (
                    f"None of these products have color '{requested_color}' in stock. "
                    "Tell the customer that color isn't available, and mention which colors "
                    "each product's variants list actually shows as available instead."
                )
            return response

        # A model that never saw a real product UUID this turn (e.g. it's
        # working from a product name mentioned earlier in the chat text, not
        # from a search_products result it just got back) sometimes invents
        # an ID-shaped string instead of admitting it doesn't have one. A bare
        # "invalid product_id" doesn't tell it what to do next, so it has
        # reported that literal error to the customer before — spell out the
        # fix so it self-corrects within the same turn instead.
        _INVALID_PRODUCT_ID = {
            "error": "invalid product_id — this must be a real UUID from a search_products "
            "result, not a name or invented ID. Call search_products first to get it, then "
            "retry with the returned id."
        }

        if tool_name == "get_product":
            try:
                product_id = uuid.UUID(str(arguments["product_id"]))
            except (KeyError, ValueError):
                return _INVALID_PRODUCT_ID
            product = await get_product_by_id(db, business_id, product_id)
            return {"product": _product_summary(product) if product else None}

        if tool_name == "check_product_availability":
            try:
                product_id = uuid.UUID(str(arguments["product_id"]))
            except (KeyError, ValueError):
                return _INVALID_PRODUCT_ID
            available = await check_availability(
                db, business_id, product_id, arguments.get("variant_value")
            )
            return {"available": available}

        if tool_name == "get_active_discounts":
            now = dt.datetime.now(dt.timezone.utc)
            disc_res = await db.execute(
                select(Discount).where(
                    Discount.business_id == business_id,
                    Discount.active.is_(True),
                    or_(Discount.valid_until.is_(None), Discount.valid_until >= now),
                )
            )
            discounts = list(disc_res.scalars().all())
            disc_list = [
                {
                    "type": d.discount_type,
                    "value": float(d.value),
                    "description": d.description,
                    "conditions": d.conditions,
                    "code": d.code,
                }
                for d in discounts
            ]
            escalation_state["active_discounts"] = disc_list
            return {"discounts": disc_list}

        if tool_name == "request_human":
            reason = arguments.get("reason", "")
            escalation_state["escalated"] = True
            escalation_state["reason"] = reason
            conversation.status = "human_needed"
            await db.flush()
            return {"escalated": True, "reason": reason}

        return {"error": f"unknown tool: {tool_name}"}

    return execute, escalation_state
