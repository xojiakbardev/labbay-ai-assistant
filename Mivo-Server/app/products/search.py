"""Product retrieval service — full-text + structured filters, no vector DB
(plan §7). This is what the AI's search_products tool calls in Phase 7; it never
returns more than `limit` rows and never the whole catalog."""
import re
import uuid

from sqlalchemy import and_, cast, func, or_, select, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.products.models import Product, ProductVariant

DEFAULT_LIMIT = 5

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _or_tsquery(query: str):
    """`plainto_tsquery` ANDs every word together, which is too strict for a
    multi-word query whose terms happen to land on *different* products —
    e.g. "sportivniy poyabzal": one product's vocabulary has "sport", another
    has "poyabzal", but no single product has to have both, so the strict AND
    match returns nothing even though a product obviously fits. OR-ing the
    words instead still ranks documents that match more terms higher (via
    ts_rank), it just stops "matches none of the exact words together" from
    silently meaning "nothing to show" — see search_products' fallback."""
    words = _WORD_RE.findall(query.lower())
    if not words:
        return None
    from app.products.search_normalize import get_synonyms_for_word
    expanded_words = set(words)
    for w in words:
        for syn in get_synonyms_for_word(w):
            expanded_words.add(syn)
    return func.to_tsquery("simple", " | ".join(expanded_words))


def _base_query_stmt(
    business_id: uuid.UUID,
    *,
    price_max: float | None,
    only_available: bool,
    limit: int,
    ts_query=None,
):
    stmt = (
        select(Product)
        .options(selectinload(Product.variants), selectinload(Product.images))
        .where(Product.business_id == business_id)
    )

    if only_available:
        stmt = stmt.where(Product.availability.is_(True))

    if ts_query is not None:
        stmt = stmt.where(Product.search_vector.op("@@")(ts_query))

    if price_max is not None:
        stmt = stmt.where(Product.price.is_not(None), Product.price <= price_max)

    if ts_query is not None:
        stmt = stmt.order_by(func.ts_rank(Product.search_vector, ts_query).desc())
    else:
        stmt = stmt.order_by(Product.availability.desc(), Product.created_at.desc())

    return stmt.limit(limit)


def _variant_filter(variant_type: str, value: str):
    target = value.strip().lower()
    return (
        select(ProductVariant.id)
        .where(
            ProductVariant.product_id == Product.id,
            ProductVariant.availability.is_(True),
            or_(
                func.lower(ProductVariant.value) == target,
                func.lower(ProductVariant.value).contains(target),
                func.lower(cast(ProductVariant.attributes[variant_type], String)).contains(target),
            ),
        )
        .exists()
    )


_TRIGRAM_THRESHOLD = 0.25


async def _trigram_fallback(db, business_id, *, query, price_max, only_available, limit, color, size):
    """Last-resort fuzzy match via pg_trgm with normalized text:
    Catches Cyrillic vs Latin differences ('спорт костюм' <-> 'sport kostyum'),
    apostrophe variants (o'/oʻ/g'/gʻ), and spelling typos ('krossofka' <-> 'krossovka').
    Ranks by similarity so the closest match outranks weaker ones.
    """
    from app.products.search_normalize import normalize_for_search

    norm_query = normalize_for_search(query)
    if not norm_query:
        return []

    # Compare normalized query against search_normalized column (using greatest of similarity and word_similarity)
    norm_col = func.coalesce(Product.search_normalized, "")
    similarity_expr = func.greatest(
        func.similarity(norm_col, norm_query),
        func.word_similarity(norm_query, norm_col),
        func.word_similarity(query, Product.name),
        func.coalesce(func.word_similarity(query, Product.description), 0.0),
    )
    stmt = (
        select(Product)
        .options(selectinload(Product.variants), selectinload(Product.images))
        .where(Product.business_id == business_id, similarity_expr > _TRIGRAM_THRESHOLD)
    )
    if only_available:
        stmt = stmt.where(Product.availability.is_(True))
    if price_max is not None:
        stmt = stmt.where(Product.price.is_not(None), Product.price <= price_max)
    if color:
        stmt = stmt.where(_variant_filter("color", color))
    if size:
        stmt = stmt.where(_variant_filter("size", size))
    stmt = stmt.order_by(similarity_expr.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().unique().all())


async def _run(db, business_id, *, ts_query, price_max, only_available, limit, color, size):
    stmt = _base_query_stmt(
        business_id, ts_query=ts_query, price_max=price_max, only_available=only_available, limit=limit
    )
    if color:
        stmt = stmt.where(_variant_filter("color", color))
    if size:
        stmt = stmt.where(_variant_filter("size", size))
    result = await db.execute(stmt)
    return list(result.scalars().unique().all())


async def search_products(
    db: AsyncSession,
    business_id: uuid.UUID,
    *,
    query: str | None = None,
    color: str | None = None,
    size: str | None = None,
    price_max: float | None = None,
    only_available: bool = True,
    limit: int = DEFAULT_LIMIT,
) -> list[Product]:
    and_query = func.plainto_tsquery("simple", query) if query else None
    matches = await _run(
        db, business_id, ts_query=and_query, price_max=price_max,
        only_available=only_available, limit=limit, color=color, size=size,
    )

    # A size/color that doesn't exist on an otherwise-matching product must
    # not make that product invisible to the AI — the tool result is the
    # model's *only* source of truth on real stock (plan §9's "never invent
    # availability"), so if the strict filter comes up empty, fall back to
    # the query/price match alone and let the model see the product's real
    # variants (and their own availability) instead of nothing at all. This
    # is what lets it correctly answer "no 42, but we do have 39/40/41"
    # instead of concluding — wrongly — that nothing is in stock.
    if (color or size) and not matches:
        matches = await _run(
            db, business_id, ts_query=and_query, price_max=price_max,
            only_available=only_available, limit=limit, color=None, size=None,
        )

    # A multi-word query that matches nothing under strict AND (its terms
    # spread across different products, or use different wording than the
    # catalog) falls back to an OR match instead of surfacing zero products —
    # see _or_tsquery. Ranking still favors closer matches, so this only ever
    # adds recall, never buries a strong exact match under weak ones.
    if query and not matches:
        or_query = _or_tsquery(query)
        if or_query is not None:
            matches = await _run(
                db, business_id, ts_query=or_query, price_max=price_max,
                only_available=only_available, limit=limit, color=color, size=size,
            )

    # Still nothing? One more tier: fuzzy trigram similarity, for wording the
    # full-text search can't bridge at all (see _trigram_fallback).
    if query and not matches:
        matches = await _trigram_fallback(
            db, business_id, query=query, price_max=price_max,
            only_available=only_available, limit=limit, color=color, size=size,
        )
        if (color or size) and not matches:
            matches = await _trigram_fallback(
                db, business_id, query=query, price_max=price_max,
                only_available=only_available, limit=limit, color=None, size=None,
            )

    # If still nothing and only_available was True, check without availability
    # so the model knows we carry it (and can inform customer it's out of stock)
    # rather than falsely saying we don't sell this product at all.
    if query and not matches and only_available:
        matches = await _trigram_fallback(
            db, business_id, query=query, price_max=price_max,
            only_available=False, limit=limit, color=None, size=None,
        )

    return matches


async def get_product_by_id(db: AsyncSession, business_id: uuid.UUID, product_id: uuid.UUID) -> Product | None:
    """Used by the get_product AI tool (Phase 7) — same tenant-scoped lookup as
    the manual CRUD getter, kept here so the retrieval module is self-contained."""
    from app.products.crud import get_product

    return await get_product(db, business_id, product_id)


async def check_availability(
    db: AsyncSession, business_id: uuid.UUID, product_id: uuid.UUID, variant_value: str | None = None
) -> bool:
    product = await get_product_by_id(db, business_id, product_id)
    if product is None:
        return False
    if not product.availability:
        return False
    if variant_value is None:
        return True
    variant_value_lower = variant_value.strip().lower()
    # Only do variant-level check when there are actual variants to check.
    # If no variants exist the product is available as-is.
    if not product.variants:
        return True
    matched_variants = [
        v for v in product.variants
        if v.value.lower() == variant_value_lower
    ]
    if not matched_variants:
        # variant_value didn't match any known variant — the AI probably passed
        # the product name or an unmapped attribute. Fall back to: product is
        # available if it has at least one available variant.
        return any(
            v.availability and (v.stock_quantity is None or v.stock_quantity > 0)
            for v in product.variants
        )
    return any(
        v.availability and (v.stock_quantity is None or v.stock_quantity > 0)
        for v in matched_variants
    )
