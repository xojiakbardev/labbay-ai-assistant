"""Product retrieval — lexical tiers fused with semantic search.

This is what the AI's search_products tool calls. It never returns more than
`limit` rows and never the whole catalog.

Four retrievers, in increasing order of how far they can stretch:

1. full-text (tsvector), strict AND — the customer used the catalog's words
2. full-text, OR + synonyms — the words are spread across different products
3. trigram over normalized text — Cyrillic/Latin, apostrophes, typos
4. embeddings — the customer described what they want instead of naming it

The first three can only match words the catalog already contains, which is why
"qishga issiq narsa kerak" used to return nothing at all: no product literally
says "qish". The customer was expected to guess the catalog's vocabulary. The
semantic tier is what removes that expectation.

The lexical cascade is unchanged and still runs first — an exact keyword match
is a stronger signal than a similar-sounding one, and the tiers each exist
because of a specific bug. Semantics is fused on top with weighted RRF, so it
adds recall without outranking precision, and it disappears entirely when
embeddings aren't configured.
"""
import logging
import re
import uuid
from collections import OrderedDict

from sqlalchemy import and_, cast, func, or_, select, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.embeddings.base import EmbeddingError, EmbeddingProvider
from app.ai.embeddings.factory import get_embedding_provider
from app.core.config import get_settings
from app.products.models import Product, ProductVariant

logger = logging.getLogger("app.products.search")

DEFAULT_LIMIT = 5

# Reciprocal Rank Fusion's damping constant. 60 is the value from the original
# paper and the usual default: high enough that rank 1 doesn't dominate, low
# enough that deep results stop mattering.
_RRF_K = 60

# How many candidates each retriever contributes to the fusion. Wider than
# `limit` so a product ranked 6th lexically and 1st semantically can still win.
_CANDIDATE_POOL = 20

# Cosine distance above which a "nearest" product isn't actually related.
# Vector search always returns its N closest rows, however far away they are —
# without this, a query matching nothing would still come back full of
# confident-looking rubbish, which is worse than an honest empty result.
# Tune against the eval suite (`python -m evals.run`), not by intuition.
_SEMANTIC_MAX_DISTANCE = 0.62

# Query embeddings are hit repeatedly — the same customer phrasing recurs across
# retries and tool calls within one turn. Small, process-local, best-effort.
_QUERY_VECTOR_CACHE: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()
_QUERY_VECTOR_CACHE_MAX = 256

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



async def _embed_query(provider: EmbeddingProvider, query: str) -> list[float]:
    key = (provider.model, query.strip().lower())
    cached = _QUERY_VECTOR_CACHE.get(key)
    if cached is not None:
        _QUERY_VECTOR_CACHE.move_to_end(key)
        return cached

    vector = await provider.embed_query(query)
    _QUERY_VECTOR_CACHE[key] = vector
    if len(_QUERY_VECTOR_CACHE) > _QUERY_VECTOR_CACHE_MAX:
        _QUERY_VECTOR_CACHE.popitem(last=False)
    return vector


async def _semantic_candidates(
    db, business_id, *, query, price_max, only_available, limit, color, size
) -> list[Product]:
    """Products whose meaning is close to the query, nearest first.

    Returns [] — never raises — whenever embeddings can't help: not configured,
    the API is down, nothing embedded yet. Semantic search is an enhancement on
    top of the lexical tiers, and a customer waiting on a reply must not lose
    their answer because an embedding call timed out.
    """
    provider = get_embedding_provider()
    if provider is None or not query or not query.strip():
        return []

    try:
        vector = await _embed_query(provider, query)
    except EmbeddingError as exc:
        logger.warning("[search] semantic tier skipped, embedding failed: %s", exc)
        return []

    distance = Product.embedding.cosine_distance(vector)
    stmt = (
        select(Product)
        .options(selectinload(Product.variants), selectinload(Product.images))
        .where(
            Product.business_id == business_id,
            Product.embedding.is_not(None),
            # Vectors from a different model live in a different space, so
            # comparing them is meaningless — ignore them until re-embedded.
            Product.embedding_model == provider.model,
            distance < _SEMANTIC_MAX_DISTANCE,
        )
    )
    if only_available:
        stmt = stmt.where(Product.availability.is_(True))
    if price_max is not None:
        stmt = stmt.where(Product.price.is_not(None), Product.price <= price_max)
    if color:
        stmt = stmt.where(_variant_filter("color", color))
    if size:
        stmt = stmt.where(_variant_filter("size", size))

    stmt = stmt.order_by(distance).limit(limit)
    try:
        result = await db.execute(stmt)
    except Exception as exc:  # noqa: BLE001 — a missing extension/column must not break search
        logger.warning("[search] semantic tier skipped, query failed: %s", exc)
        return []
    return list(result.scalars().unique().all())


def _rrf_fuse(rankings: list[tuple[list[Product], float]], limit: int) -> list[Product]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of weight / (k + rank).

    Ranks rather than scores, because a ts_rank and a cosine distance aren't on
    any common scale and normalising them is guesswork. A product both tiers
    agree on rises above one that only a single tier found, which is exactly
    the behaviour worth having.
    """
    scores: dict[uuid.UUID, float] = {}
    products: dict[uuid.UUID, Product] = {}
    for ranked, weight in rankings:
        for rank, product in enumerate(ranked, start=1):
            products.setdefault(product.id, product)
            scores[product.id] = scores.get(product.id, 0.0) + weight / (_RRF_K + rank)
    ordered = sorted(scores, key=lambda pid: scores[pid], reverse=True)
    return [products[pid] for pid in ordered[:limit]]

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

    # The semantic tier runs regardless of whether the lexical ones found
    # anything: it's the only one that can bridge "qishga issiq narsa" to a
    # winter coat, and it also promotes a product the keyword tiers ranked low.
    semantic = await _semantic_candidates(
        db, business_id, query=query, price_max=price_max,
        only_available=only_available, limit=_CANDIDATE_POOL, color=color, size=size,
    )
    if not semantic:
        return matches
    if not matches:
        # Nothing matched the words but something matches the meaning. This is
        # the case the whole tier exists for.
        return semantic[:limit]

    return _rrf_fuse(
        [(matches, 1.0), (semantic, get_settings().semantic_fusion_weight)], limit
    )


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
