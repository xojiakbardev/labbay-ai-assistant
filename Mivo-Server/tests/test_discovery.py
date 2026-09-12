"""Catalog browsing (app/products/discovery.py) and the discovery slots.

search_products can only answer "do you have X". These cover the way in for a
customer who hasn't named anything — "nima bor?", "sovg'aga nimadir kerak",
"boshqasi bormi?" — which is what stops the AI from having to ask people to
phrase their request as a search query.
"""
import datetime as dt
import uuid

import pytest

from app.ai.conversation_state import ESSENTIAL_SLOTS, parse_slots, render_state_block, update_state
from app.ai.tools.definitions import ALL_TOOLS
from app.businesses.models import Business
from app.leads.models import Lead
from app.products.discovery import (
    SIMILAR_PRICE_SPREAD,
    browse_products,
    list_categories,
    popular_products,
    similar_products,
)
from app.products.models import Product


async def _seed(db_session, owner_suffix: str = "") -> Business:
    from app.auth.models import User

    user = User(email=f"{uuid.uuid4()}{owner_suffix}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Sport Shop")
    db_session.add(business)
    await db_session.flush()
    return business


async def _product(db_session, business, name, price, category=None, available=True):
    product = Product(
        business_id=business.id,
        name=name,
        price=price,
        currency="so'm",
        availability=available,
        attributes={"category": category} if category else {},
    )
    db_session.add(product)
    await db_session.flush()
    return product


async def test_list_categories_counts_only_what_is_in_stock(db_session) -> None:
    business = await _seed(db_session)
    await _product(db_session, business, "Nike Air", 780000, "Krossovka")
    await _product(db_session, business, "Puma Rebound", 500000, "Krossovka")
    await _product(db_session, business, "Tote", 300000, "Sumka")
    await _product(db_session, business, "Eski model", 100000, "Krossovka", available=False)

    categories = await list_categories(db_session, business.id)

    assert [c["name"] for c in categories] == ["Krossovka", "Sumka"]
    assert categories[0]["count"] == 2  # the unavailable one is not offered


async def test_list_categories_is_empty_when_nothing_is_categorised(db_session) -> None:
    """Manually-entered products often carry no category. That's an empty list,
    not an error — the executor tells the model to describe products instead."""
    business = await _seed(db_session)
    await _product(db_session, business, "Nomsiz mahsulot", 100000)

    assert await list_categories(db_session, business.id) == []


async def test_browse_products_narrows_by_category_and_price(db_session) -> None:
    business = await _seed(db_session)
    await _product(db_session, business, "Nike Air", 780000, "Krossovka")
    await _product(db_session, business, "Puma Rebound", 500000, "Krossovka")
    await _product(db_session, business, "Tote", 300000, "Sumka")

    all_products = await browse_products(db_session, business.id)
    assert len(all_products) == 3

    krossovka = await browse_products(db_session, business.id, category="krossovka")
    assert {p.name for p in krossovka} == {"Nike Air", "Puma Rebound"}

    affordable = await browse_products(db_session, business.id, category="Krossovka", price_max=600000)
    assert {p.name for p in affordable} == {"Puma Rebound"}


async def test_browse_products_never_offers_out_of_stock(db_session) -> None:
    business = await _seed(db_session)
    await _product(db_session, business, "Bor", 100000, "Sumka")
    await _product(db_session, business, "Yo'q", 100000, "Sumka", available=False)

    assert {p.name for p in await browse_products(db_session, business.id)} == {"Bor"}


async def test_similar_products_offers_a_comparable_alternative(db_session) -> None:
    """This is what lets "qimmat ekan" get a real cheaper option in reply."""
    business = await _seed(db_session)
    anchor = await _product(db_session, business, "Nike Air", 800000, "Krossovka")
    await _product(db_session, business, "Puma Rebound", 600000, "Krossovka")
    await _product(db_session, business, "Lux model", 3000000, "Krossovka")  # far outside the band
    await _product(db_session, business, "Tote", 700000, "Sumka")  # right price, wrong kind

    similar = await similar_products(db_session, business.id, anchor.id)
    names = {p.name for p in similar}

    assert "Puma Rebound" in names
    assert "Nike Air" not in names  # never suggest the product they're looking at
    assert "Tote" not in names  # a bag is not an alternative to a shoe
    assert "Lux model" not in names
    assert 600000 >= 800000 * (1 - SIMILAR_PRICE_SPREAD)  # the band this relies on


async def test_similar_products_falls_back_within_the_category(db_session) -> None:
    """One product in the price band would otherwise return nothing, which reads
    to the customer as "we have nothing else"."""
    business = await _seed(db_session)
    anchor = await _product(db_session, business, "Nike Air", 800000, "Krossovka")
    await _product(db_session, business, "Juda arzon", 50000, "Krossovka")

    similar = await similar_products(db_session, business.id, anchor.id)
    assert [p.name for p in similar] == ["Juda arzon"]


async def test_similar_products_of_an_unknown_id_is_empty(db_session) -> None:
    business = await _seed(db_session)
    assert await similar_products(db_session, business.id, uuid.uuid4()) == []


async def test_popular_products_ranks_by_what_customers_asked_about(db_session) -> None:
    """There are no orders in the system, so popularity is measured from the
    leads table — a real signal from real conversations, not a guess."""
    business = await _seed(db_session)
    quiet = await _product(db_session, business, "Hech kim so'ramagan", 100000, "Sumka")
    asked_once = await _product(db_session, business, "Bir marta", 200000, "Sumka")
    asked_twice = await _product(db_session, business, "Ikki marta", 300000, "Sumka")

    for interested in (
        [{"id": str(asked_twice.id), "name": "Ikki marta"}],
        [{"id": str(asked_twice.id), "name": "Ikki marta"}, {"id": str(asked_once.id), "name": "Bir marta"}],
    ):
        customer_id = uuid.uuid4()
        from app.customers.models import Customer

        from app.conversations.service import get_or_create_conversation

        now = dt.datetime.now(dt.timezone.utc)
        db_session.add(Customer(id=customer_id, business_id=business.id, ig_scoped_id=str(uuid.uuid4()),
                                first_seen_at=now, last_seen_at=now))
        await db_session.flush()
        conversation = await get_or_create_conversation(db_session, business.id, customer_id)
        db_session.add(
            Lead(business_id=business.id, customer_id=customer_id, conversation_id=conversation.id,
                 status="warm", score=50, interested_products=interested)
        )
    await db_session.flush()

    ranked = await popular_products(db_session, business.id)

    assert ranked[0].name == "Ikki marta"
    assert ranked[1].name == "Bir marta"
    # Products nobody asked about still get shown, just last.
    assert quiet.id in {p.id for p in ranked}


async def test_popular_products_falls_back_when_there_are_no_leads_yet(db_session) -> None:
    business = await _seed(db_session)
    await _product(db_session, business, "Yangi do'kon mahsuloti", 100000, "Sumka")

    ranked = await popular_products(db_session, business.id)
    assert [p.name for p in ranked] == ["Yangi do'kon mahsuloti"]


def test_catalog_tools_are_exposed_to_the_model() -> None:
    names = {t.name for t in ALL_TOOLS}
    assert {"browse_catalog", "get_similar_products"} <= names
    # Still retrieval + escalation only — no persistence tool has crept in.
    assert not {n for n in names if n.startswith(("create_", "update_", "delete_"))}


def test_parse_slots_keeps_only_recognised_keys() -> None:
    parsed = parse_slots([
        "use_case: sportga",
        "size: 42",
        "budget:  500000 so'm",
        "favourite_colour_of_their_cat: qora",  # not a slot
        "no colon here",
        "size:   ",  # empty value
        42,  # not even a string
    ])
    assert parsed == {"use_case": "sportga", "size": "42", "budget": "500000 so'm"}


def test_slots_accumulate_across_turns_and_are_never_re_asked() -> None:
    state = update_state(None, stage="discovery", slots_learned=["use_case: sportga"])
    state = update_state(state, stage="recommendation", slots_learned=["size: 42"])

    assert state["slots"] == {"use_case": "sportga", "size": "42"}

    block = render_state_block(state)
    assert "never ask for any of this again" in block
    # Rendered as the customer's own (quoted) words — data, not instructions.
    assert 'use_case: "sportga"' in block
    # The one essential still missing is the one it's told to chase.
    assert "Still unknown: budget" in block
    for known in ("use_case", "size"):
        assert f"Still unknown: {known}" not in block


def test_a_brand_new_conversation_is_not_nagged_about_missing_slots() -> None:
    """On someone's first hello there is nothing to be consistent with, and the
    prompt's own guidance covers the opening."""
    assert render_state_block(update_state(None)) == ""
    assert all(slot in ESSENTIAL_SLOTS for slot in ("use_case", "size", "budget"))


def test_handoff_stops_chasing_slots() -> None:
    """Once it's a human's conversation, the AI shouldn't still be working a
    discovery checklist."""
    state = update_state(None, stage="discovery", slots_learned=["use_case: sportga"])
    handed_off = update_state(state, escalated=True)

    assert "Still unknown" not in render_state_block(handed_off)
