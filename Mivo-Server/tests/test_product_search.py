"""Phase 6 acceptance test: full-text + filtered retrieval returns the correct
top-N products, never the whole catalog (plan §7)."""
import uuid

import pytest

from app.businesses.models import Business
from app.products.models import Product, ProductVariant
from app.products.search import check_availability, search_products

pytestmark = pytest.mark.asyncio


async def _seed_business(db_session) -> uuid.UUID:
    from app.auth.models import User

    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Search Test Biz")
    db_session.add(business)
    await db_session.flush()
    return business.id


from app.products.search_normalize import normalize_for_search


async def _add_product(db_session, business_id, name, description="", price=None, availability=True, variants=None):
    product = Product(
        business_id=business_id,
        name=name,
        description=description,
        price=price,
        currency="UZS",
        availability=availability,
        attributes={},
        source="manual",
        search_normalized=normalize_for_search(f"{name} {description or ''}"),
    )
    product.variants = [
        ProductVariant(variant_type=t, value=v) for t, v in (variants or [])
    ]
    db_session.add(product)
    await db_session.flush()
    return product


async def test_keyword_search_returns_matching_product(db_session) -> None:
    business_id = await _seed_business(db_session)
    await _add_product(
        db_session, business_id, "Nike Hoodie", "Issiq oversize hoodie",
        price=250000, variants=[("color", "black"), ("size", "M")],
    )
    await _add_product(db_session, business_id, "Adidas Sneakers", "Running shoes", price=400000)
    await db_session.commit()

    results = await search_products(db_session, business_id, query="hoodie")
    assert [p.name for p in results] == ["Nike Hoodie"]


async def test_multiword_query_falls_back_to_or_match_when_and_finds_nothing(db_session) -> None:
    """Regression: "sportivniy poyabzal" (sport shoe) returned zero results
    for a catalog that has "Sport krossovka" — plainto_tsquery ANDs every
    word, so a query whose terms land on different products' vocabulary
    matched nothing at all, and the AI told the customer to search again
    instead of finding an obviously relevant product. OR-ing the terms means
    a real word overlap (here, "poyabzal") is no longer thrown away just
    because the *other* word didn't also match — see app/products/search.py."""
    business_id = await _seed_business(db_session)
    await _add_product(
        db_session, business_id, "Sport krossovka Air Max",
        "Yengil va qulay, kundalik kiyish uchun mos sport krossovkasi.",
    )
    await _add_product(
        db_session, business_id, "Klassik charm poyabzal",
        "Tabiiy charmdan tikilgan klassik model.",
    )
    await db_session.commit()

    # A strict AND match finds nothing (no product has both words); the OR
    # fallback still surfaces the product that shares "poyabzal" instead of
    # leaving the customer with zero results. (The synonym expansion also
    # maps "poyabzal" to "krossovka", so the sport sneaker — arguably the
    # better answer to "sport shoe" — comes back too.)
    results = await search_products(db_session, business_id, query="sportivniy poyabzal")
    assert "Klassik charm poyabzal" in [p.name for p in results]


async def test_query_with_no_token_overlap_at_all_falls_back_to_trigram_similarity(db_session) -> None:
    """When even the OR fallback finds nothing — none of the query's words
    exist as exact tokens anywhere in the catalog — trigram similarity is the
    last resort, catching near-miss spelling/transliteration like
    "sportivniy" vs. a catalog that only ever says "sport"."""
    business_id = await _seed_business(db_session)
    await _add_product(
        db_session, business_id, "Sport krossovka Air Max",
        "Yengil va qulay, kundalik kiyish uchun mos sport krossovkasi.",
    )
    await _add_product(db_session, business_id, "Yozgi sandal", "Yengil yozgi sandal.")
    await db_session.commit()

    results = await search_products(db_session, business_id, query="sportivniy")
    assert [p.name for p in results] == ["Sport krossovka Air Max"]


async def test_exact_multiword_match_does_not_need_the_or_fallback(db_session) -> None:
    """Sanity check that the OR fallback only kicks in when AND truly finds
    nothing — an exact multi-word match still returns its precise result."""
    business_id = await _seed_business(db_session)
    await _add_product(db_session, business_id, "Nike Running Hoodie", "Warm running hoodie")
    await _add_product(db_session, business_id, "Nike Sneakers", "Just sneakers")
    await db_session.commit()

    results = await search_products(db_session, business_id, query="running hoodie")
    assert [p.name for p in results] == ["Nike Running Hoodie"]


async def test_color_and_size_filters_narrow_results(db_session) -> None:
    business_id = await _seed_business(db_session)
    await _add_product(
        db_session, business_id, "Nike Hoodie", variants=[("color", "black"), ("size", "M")]
    )
    await _add_product(
        db_session, business_id, "Nike Hoodie White", variants=[("color", "white"), ("size", "M")]
    )
    await db_session.commit()

    results = await search_products(db_session, business_id, query="hoodie", color="black", size="M")
    assert [p.name for p in results] == ["Nike Hoodie"]


async def test_available_products_win_and_sold_out_ones_are_shown_as_sold_out(db_session) -> None:
    """Available stock is what search returns first; only when nothing
    available matches does a sold-out product come back — marked unavailable,
    so the AI says "we carry it, it's sold out" instead of "we don't sell it"."""
    business_id = await _seed_business(db_session)
    await _add_product(db_session, business_id, "Sold Out Hoodie", availability=False)
    await db_session.commit()

    results = await search_products(db_session, business_id, query="hoodie")
    assert [(p.name, p.availability) for p in results] == [("Sold Out Hoodie", False)]

    await _add_product(db_session, business_id, "Fresh Hoodie", availability=True)
    await db_session.commit()
    results = await search_products(db_session, business_id, query="hoodie")
    assert [p.name for p in results] == ["Fresh Hoodie"]


async def test_price_max_filter(db_session) -> None:
    business_id = await _seed_business(db_session)
    await _add_product(db_session, business_id, "Cheap Shirt", price=50000)
    await _add_product(db_session, business_id, "Expensive Jacket", price=900000)
    await db_session.commit()

    results = await search_products(db_session, business_id, price_max=100000)
    assert [p.name for p in results] == ["Cheap Shirt"]


async def test_search_is_tenant_scoped(db_session) -> None:
    business_a = await _seed_business(db_session)
    business_b = await _seed_business(db_session)
    await _add_product(db_session, business_a, "Business A Hoodie")
    await _add_product(db_session, business_b, "Business B Hoodie")
    await db_session.commit()

    results = await search_products(db_session, business_a, query="hoodie")
    assert [p.name for p in results] == ["Business A Hoodie"]


async def test_search_respects_limit(db_session) -> None:
    business_id = await _seed_business(db_session)
    for i in range(10):
        await _add_product(db_session, business_id, f"Hoodie {i}")
    await db_session.commit()

    results = await search_products(db_session, business_id, query="hoodie", limit=3)
    assert len(results) == 3


async def test_size_filter_falls_back_to_query_match_when_size_not_carried(db_session) -> None:
    """Regression: a customer asking for a size the product doesn't carry must
    still see the product (with its *real* variants) — not a silent empty
    result that leaves the AI unable to say what sizes actually exist.
    See app/ai/tools/executor.py for the corresponding "note" this feeds."""
    business_id = await _seed_business(db_session)
    await _add_product(
        db_session, business_id, "Air Max", "Sport krossovka",
        variants=[("size", "39"), ("size", "40"), ("size", "41")],
    )
    await db_session.commit()

    # Exact match still narrows normally.
    results = await search_products(db_session, business_id, query="Air Max", size="40")
    assert [p.name for p in results] == ["Air Max"]

    # A size that isn't carried falls back to the query match instead of [].
    results = await search_products(db_session, business_id, query="Air Max", size="42")
    assert [p.name for p in results] == ["Air Max"]
    sizes = {v.value for v in results[0].variants if v.variant_type == "size"}
    assert sizes == {"39", "40", "41"}  # the real stock, for the model to relay honestly


async def test_size_fallback_does_not_apply_when_query_itself_has_no_match(db_session) -> None:
    business_id = await _seed_business(db_session)
    await _add_product(db_session, business_id, "Air Max", variants=[("size", "40")])
    await db_session.commit()

    results = await search_products(db_session, business_id, query="sandal", size="40")
    assert results == []


async def test_check_availability_true_for_matching_variant(db_session) -> None:
    business_id = await _seed_business(db_session)
    product = await _add_product(
        db_session, business_id, "Nike Hoodie", variants=[("size", "M"), ("size", "L")]
    )
    await db_session.commit()

    matched = await check_availability(db_session, business_id, product.id, "M")
    assert matched["available"] is True and matched["matched_variants"] == ["M"]

    # Regression: a size that isn't carried used to fall back to "the product
    # has *some* variant in stock -> available: true".
    missing = await check_availability(db_session, business_id, product.id, "XL")
    assert missing["available"] is False
    assert missing["available_variants"] == ["M", "L"]

    assert (await check_availability(db_session, business_id, product.id))["available"] is True


async def test_size_matches_whole_tokens_not_substrings(db_session) -> None:
    """Regression: substring matching made "S" match "XS" and "Sariq", and
    "L" match "XL" — the AI then told customers sizes were in stock that weren't."""
    business_id = await _seed_business(db_session)
    await _add_product(db_session, business_id, "Hoodie XS", variants=[("size", "XS"), ("color", "Sariq")])
    await _add_product(db_session, business_id, "Hoodie Combo", variants=[("combination", "Qora / S")])
    await db_session.commit()

    results = await search_products(db_session, business_id, query="hoodie", size="S")
    assert [p.name for p in results] == ["Hoodie Combo"]

    xs_only = (await search_products(db_session, business_id, query="hoodie xs"))[0]
    assert (await check_availability(db_session, business_id, xs_only.id, "S"))["available"] is False
    assert (await check_availability(db_session, business_id, xs_only.id, "XS"))["available"] is True


async def test_search_cyrillic_query_matches_latin_product(db_session) -> None:
    business_id = await _seed_business(db_session)
    await _add_product(db_session, business_id, "Sport kostyum", "Erkaklar uchun qulay sport kiyimi")
    await db_session.commit()

    # Query in Cyrillic should match Latin product via normalized trigram fallback
    results = await search_products(db_session, business_id, query="спорт костюм")
    assert len(results) >= 1
    assert results[0].name == "Sport kostyum"


async def test_search_typo_query_matches_product(db_session) -> None:
    business_id = await _seed_business(db_session)
    await _add_product(db_session, business_id, "Nike Krossovka", "Yugurish uchun qulay krossovka")
    await db_session.commit()

    # Typo with 'f' instead of 'v'
    results = await search_products(db_session, business_id, query="krossofka")
    assert len(results) >= 1
    assert results[0].name == "Nike Krossovka"


async def test_search_apostrophe_variants_match_product(db_session) -> None:
    business_id = await _seed_business(db_session)
    await _add_product(db_session, business_id, "Oq ko'ylak", "Klassik oq koʻylak")
    await db_session.commit()

    # Query without apostrophe
    results = await search_products(db_session, business_id, query="oq koylak")
    assert len(results) >= 1
    assert results[0].name == "Oq ko'ylak"

    # Query with curly apostrophe
    results2 = await search_products(db_session, business_id, query="oq koʻylak")
    assert len(results2) >= 1
    assert results2[0].name == "Oq ko'ylak"


async def test_search_reverse_cyrillic_catalog_matches_latin_query(db_session) -> None:
    business_id = await _seed_business(db_session)
    await _add_product(db_session, business_id, "Футболка", "Paxtali yozgi futbolka")
    await db_session.commit()

    # Query in Latin matches Cyrillic product
    results = await search_products(db_session, business_id, query="futbolka")
    assert len(results) >= 1
    assert results[0].name == "Футболка"

