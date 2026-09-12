"""Catalog browsing — retrieval for customers who haven't named a product.

search_products answers "do you have X". It cannot answer "what do you have",
"nima bor o'zi?", "sovg'aga nimadir kerak" or "shunga o'xshashi bormi?", because
all of those arrive with no usable search term. Without a way in, the AI could
only ask the customer to be more specific, which is exactly the "you have to
phrase it like a query" problem — the shop assistant's job is to open a drawer
and show them something, not to demand better keywords.

Everything here is tenant-scoped and capped, same as search.py: the model never
sees the whole catalog.
"""
import uuid
from typing import Any

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.products.models import Product
from app.core.config import get_settings

BROWSE_LIMIT = get_settings().browse_limit
SIMILAR_LIMIT = get_settings().similar_limit
MAX_CATEGORIES = get_settings().max_categories

# How far either side of a product's price still counts as "something like it" —
# wide enough to offer a cheaper alternative when someone balks at the price,
# narrow enough not to answer "anything similar?" with the whole shop.
SIMILAR_PRICE_SPREAD = get_settings().similar_price_spread

_CATEGORY = Product.attributes["category"].astext


def _loaded(stmt):
    return stmt.options(selectinload(Product.variants), selectinload(Product.images))


async def list_categories(db: AsyncSession, business_id: uuid.UUID) -> list[dict[str, Any]]:
    """The kinds of products this business actually stocks, with counts.

    Categories come from `attributes["category"]`, which AI import fills in;
    manually-entered products often have none, so this can legitimately come
    back empty and callers must cope rather than treat it as "no stock".
    """
    result = await db.execute(
        select(_CATEGORY.label("category"), func.count().label("count"))
        .where(
            Product.business_id == business_id,
            Product.availability.is_(True),
            _CATEGORY.is_not(None),
            func.trim(_CATEGORY) != "",
        )
        .group_by(_CATEGORY)
        .order_by(func.count().desc())
        .limit(MAX_CATEGORIES)
    )
    return [{"name": row.category, "count": row.count} for row in result.all()]


async def browse_products(
    db: AsyncSession,
    business_id: uuid.UUID,
    *,
    category: str | None = None,
    price_max: float | None = None,
    limit: int = BROWSE_LIMIT,
) -> list[Product]:
    """A sample of what's in stock, optionally narrowed to one category."""
    stmt = _loaded(
        select(Product).where(
            Product.business_id == business_id,
            Product.availability.is_(True),
        )
    )
    if category:
        stmt = stmt.where(func.lower(_CATEGORY) == category.strip().lower())
    if price_max is not None:
        stmt = stmt.where(Product.price.is_not(None), Product.price <= price_max)

    stmt = stmt.order_by(Product.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def popular_products(
    db: AsyncSession, business_id: uuid.UUID, *, limit: int = BROWSE_LIMIT
) -> list[Product]:
    """What other customers have actually asked about, most-asked first.

    There are no orders in the system, so "popular" is measured from the leads
    table: every qualified turn records the products a customer showed interest
    in, which across a business's whole history is a real signal and not a
    guess. Falls back to a plain browse while a new business has no leads yet.
    """
    from app.leads.models import Lead

    rows = await db.execute(
        select(Lead.interested_products).where(Lead.business_id == business_id)
    )

    counts: dict[str, int] = {}
    for (interested,) in rows.all():
        for entry in interested or []:
            product_id = (entry or {}).get("id") if isinstance(entry, dict) else None
            if not product_id:
                continue
            try:
                normalized = str(uuid.UUID(str(product_id)))
            except (ValueError, TypeError):
                continue
            counts[normalized] = counts.get(normalized, 0) + 1

    if not counts:
        return await browse_products(db, business_id, limit=limit)

    ranked = sorted(counts, key=lambda pid: counts[pid], reverse=True)[: limit * 2]
    found = list(
        (
            await db.execute(
                _loaded(select(Product)).where(
                    Product.business_id == business_id,
                    Product.availability.is_(True),
                    Product.id.in_([uuid.UUID(pid) for pid in ranked]),
                )
            )
        )
        .scalars()
        .all()
    )
    found.sort(key=lambda p: counts.get(str(p.id), 0), reverse=True)

    if len(found) < limit:
        seen = {p.id for p in found}
        filler = await browse_products(db, business_id, limit=limit)
        found.extend(p for p in filler if p.id not in seen)

    return found[:limit]


async def similar_products(
    db: AsyncSession,
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    *,
    limit: int = SIMILAR_LIMIT,
) -> list[Product]:
    """Alternatives to one product — same category where known, comparable price.

    This is what answers "boshqasi bormi?" and, more valuably, what lets the AI
    respond to "qimmat ekan" with a real cheaper option instead of a shrug.
    """
    anchor = await db.scalar(
        select(Product).where(Product.business_id == business_id, Product.id == product_id)
    )
    if anchor is None:
        return []

    stmt = _loaded(
        select(Product).where(
            Product.business_id == business_id,
            Product.availability.is_(True),
            Product.id != anchor.id,
        )
    )

    category = (anchor.attributes or {}).get("category")
    if category:
        stmt = stmt.where(func.lower(_CATEGORY) == str(category).strip().lower())

    if anchor.price is not None:
        anchor_price = float(anchor.price)
        low = anchor_price * (1 - SIMILAR_PRICE_SPREAD)
        high = anchor_price * (1 + SIMILAR_PRICE_SPREAD)
        stmt = stmt.where(
            Product.price.is_not(None), Product.price >= low, Product.price <= high
        ).order_by(func.abs(cast(Product.price, Float) - anchor_price))
    else:
        stmt = stmt.order_by(Product.created_at.desc())

    matches = list((await db.execute(stmt.limit(limit))).scalars().all())

    # A category with only one product in the price band would otherwise return
    # nothing at all, which reads to the customer as "we have nothing else".
    if not matches and category:
        fallback = _loaded(
            select(Product)
            .where(
                Product.business_id == business_id,
                Product.availability.is_(True),
                Product.id != anchor.id,
                func.lower(_CATEGORY) == str(category).strip().lower(),
            )
            .order_by(Product.created_at.desc())
            .limit(limit)
        )
        matches = list((await db.execute(fallback)).scalars().all())

    return matches
