"""search_products tool executor: the "note" it attaches when a requested
size/color isn't carried is what stops the model from concluding — wrongly —
that everything is out of stock (see app/products/search.py's fallback and
the real conversation bug it fixed: a customer asking for a size that wasn't
carried got told the whole catalog was empty)."""
import uuid

import pytest

from app.ai.tools.executor import build_tool_executor
from app.auth.models import User
from app.businesses.models import Business
from app.conversations.service import get_or_create_conversation
from app.customers.service import get_or_create_customer
from app.products.models import Product, ProductVariant

pytestmark = pytest.mark.asyncio


async def _seed(db_session):
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Executor Test Biz")
    db_session.add(business)
    await db_session.flush()

    product = Product(
        business_id=business.id, name="Air Max", description="Sport krossovka",
        price=620000, currency="UZS", availability=True, attributes={}, source="manual",
    )
    product.variants = [ProductVariant(variant_type="size", value=v) for v in ("39", "40", "41")]
    db_session.add(product)
    await db_session.commit()

    customer = await get_or_create_customer(db_session, business.id, "cust-1")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await db_session.commit()
    return business, conversation


async def test_note_added_when_requested_size_not_carried(db_session) -> None:
    business, conversation = await _seed(db_session)
    execute, _escalation_state = build_tool_executor(db_session, business.id, conversation)

    result = await execute("search_products", {"query": "Air Max", "size": "42"})

    assert len(result["products"]) == 1
    assert "note" in result
    assert "42" in result["note"]
    sizes = {v["value"] for v in result["products"][0]["variants"] if v["variant_type"] == "size"}
    assert sizes == {"39", "40", "41"}


async def test_no_note_when_requested_size_is_carried(db_session) -> None:
    business, conversation = await _seed(db_session)
    execute, _escalation_state = build_tool_executor(db_session, business.id, conversation)

    result = await execute("search_products", {"query": "Air Max", "size": "40"})

    assert len(result["products"]) == 1
    assert "note" not in result


async def test_no_note_when_no_size_or_color_requested(db_session) -> None:
    business, conversation = await _seed(db_session)
    execute, _escalation_state = build_tool_executor(db_session, business.id, conversation)

    result = await execute("search_products", {"query": "Air Max"})

    assert len(result["products"]) == 1
    assert "note" not in result
