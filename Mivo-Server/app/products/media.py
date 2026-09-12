"""Resolves the one image URL to show for a product, for both the AI's tool
results and the Instagram send-photo flow. Prefers a real uploaded (R2)
`ProductImage`; falls back to the ad-hoc `attributes["image_url"]` link some
products still carry (products entered with just a URL, or seeded before R2
write access was configured) — either way, callers get back one public URL
or None, never having to know which source it came from."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.products.models import Product

MAX_IMAGES_PER_REPLY = 3


def get_display_image_url(product: Product) -> str | None:
    if product.images:
        primary = next((img for img in product.images if img.is_primary), product.images[0])
        return primary.url
    if product.attributes:
        attr_url = product.attributes.get("image_url")
        if attr_url:
            return attr_url
    return None


async def resolve_sendable_images(
    db: AsyncSession, business_id: uuid.UUID, product_ids: list[str]
) -> list[tuple[str, str]]:
    """The model only ever names product ids (never a URL itself — plan §9's
    "never invent" applies to media too); this is the one place that turns
    those ids into real, business-scoped, actually-existing image URLs. Ids
    that don't parse, don't belong to this business, or resolve to no photo
    are dropped rather than trusted. Capped at MAX_IMAGES_PER_REPLY so one
    reply can't turn into a photo dump. Returns (product name, url) pairs in
    the order the model named them."""
    candidate_uuids: list[uuid.UUID] = []
    for pid in product_ids[:MAX_IMAGES_PER_REPLY]:
        try:
            candidate_uuids.append(uuid.UUID(str(pid)))
        except ValueError:
            continue
    if not candidate_uuids:
        return []

    result = await db.execute(
        select(Product)
        .options(selectinload(Product.images))
        .where(Product.business_id == business_id, Product.id.in_(candidate_uuids))
    )
    by_id = {p.id: p for p in result.scalars().unique().all()}
    pairs: list[tuple[str, str]] = []
    for pid in dict.fromkeys(candidate_uuids):
        product = by_id.get(pid)
        url = get_display_image_url(product) if product else None
        if url and url.startswith("https://"):
            pairs.append((product.name, url))
    return pairs[:MAX_IMAGES_PER_REPLY]
