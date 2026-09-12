"""Manual product/variant/image persistence — the single write path both manual
creation and AI-assisted ingestion (Phase 5) go through (plan §6)."""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.usage_context import billing_to
from app.core.db import async_session_factory
from app.products.embeddings import refresh_many, refresh_product_embedding
from app.products.models import Product, ProductImage, ProductVariant
from app.products.schemas import ImageIn, ProductCreate, ProductUpdate
from app.products.search_normalize import normalize_for_search
from app.storage.r2 import delete_objects, stored_key_for

logger = logging.getLogger("app.products.crud")


def _product_query():
    return select(Product).options(selectinload(Product.variants), selectinload(Product.images))


def _new_images(images: list[ImageIn]) -> list[ProductImage]:
    has_primary = any(img.is_primary for img in images)
    return [
        ProductImage(
            r2_key=stored_key_for(img.url),
            url=img.url,
            is_primary=img.is_primary or (idx == 0 and not has_primary),
        )
        for idx, img in enumerate(images)
    ]


def _variant_photo_keys(variants) -> set[str]:
    """Bucket keys of variant photos we store ourselves (external URLs have
    none)."""
    keys: set[str] = set()
    for variant in variants:
        for url in [variant.image_url, *(variant.images or [])]:
            if url:
                key = stored_key_for(url)
                if not key.startswith("external/"):
                    keys.add(key)
    return keys


def _sync_image_url_attribute(product: Product) -> None:
    """attributes["image_url"] mirrors the primary image (older readers use it)."""
    attrs = dict(product.attributes or {})
    if product.images:
        primary = next((img for img in product.images if img.is_primary), product.images[0])
        attrs["image_url"] = primary.url
    else:
        attrs.pop("image_url", None)
    product.attributes = attrs


def _build(business_id: uuid.UUID, data: ProductCreate, source: str) -> Product:
    attrs = dict(data.attributes) if data.attributes else {}
    product = Product(
        business_id=business_id,
        name=data.name,
        description=data.description,
        price=data.price,
        currency=data.currency,
        availability=data.availability,
        attributes=attrs,
        source=source,
        search_normalized=normalize_for_search(f"{data.name} {data.description or ''}"),
    )
    product.variants = [ProductVariant(**variant.model_dump()) for variant in data.variants]
    images = list(data.images)
    if not images and attrs.get("image_url"):
        # Already validated as an http(s) URL by ProductCreate.
        images = [ImageIn(url=attrs["image_url"], is_primary=True)]
    product.images = _new_images(images)
    _sync_image_url_attribute(product)
    return product


async def _store_embeddings(db: AsyncSession, products: list[Product]) -> None:
    """Embeds and commits, after the products themselves are committed.

    Deliberately a second transaction: the product write must never wait on,
    or fail because of, a third-party embedding API (CLAUDE.md). If storing the
    vector fails, the product stays saved and keyword-searchable and
    backfill_embeddings.py picks it up — the failure is logged, and the
    session and the product objects are left usable for the caller."""
    if not products:
        return
    try:
        with billing_to(products[0].business_id):
            if len(products) == 1:
                changed = await refresh_product_embedding(db, products[0])
            else:
                changed = await refresh_many(db, products)
        if changed:
            await db.commit()
    except SQLAlchemyError:
        logger.exception("[products] storing embeddings failed for %d product(s)", len(products))
        await db.rollback()
        for product in products:
            await db.refresh(product)
            await db.refresh(product, attribute_names=["variants", "images"])


async def create_product(
    db: AsyncSession, business_id: uuid.UUID, data: ProductCreate, source: str = "manual"
) -> Product:
    product = _build(business_id, data, source)
    db.add(product)
    await db.commit()
    await db.refresh(product, attribute_names=["variants", "images"])
    await _store_embeddings(db, [product])
    return product


async def create_products(
    db: AsyncSession, business_id: uuid.UUID, items: list[ProductCreate], source: str
) -> list[Product]:
    """All-or-nothing: one commit for the whole batch. Embedding a large
    import can take longer than the proxy lets a request live, so it isn't
    done here — see embed_products_later."""
    products = [_build(business_id, item, source) for item in items]
    db.add_all(products)
    await db.commit()
    for product in products:
        await db.refresh(product, attribute_names=["variants", "images"])
    return products


async def embed_products_later(product_ids: list[uuid.UUID]) -> None:
    """Background task after an import: embed in batches on its own session."""
    async with async_session_factory() as db:
        products = list(
            (await db.execute(_product_query().where(Product.id.in_(product_ids)))).scalars().unique().all()
        )
        for start in range(0, len(products), 64):
            await _store_embeddings(db, products[start : start + 64])


async def list_products(db: AsyncSession, business_id: uuid.UUID) -> list[Product]:
    result = await db.execute(
        _product_query().where(Product.business_id == business_id).order_by(Product.created_at.desc())
    )
    return list(result.scalars().unique().all())


async def get_product(db: AsyncSession, business_id: uuid.UUID, product_id: uuid.UUID) -> Product | None:
    result = await db.execute(
        _product_query().where(Product.business_id == business_id, Product.id == product_id)
    )
    return result.scalars().unique().one_or_none()


async def update_product(db: AsyncSession, product: Product, data: ProductUpdate) -> Product:
    updates = data.model_dump(exclude_unset=True, exclude={"variants", "images"})
    for field, value in updates.items():
        setattr(product, field, value)

    if "name" in updates or "description" in updates:
        product.search_normalized = normalize_for_search(f"{product.name} {product.description or ''}")

    orphaned_keys: set[str] = set()
    if data.variants is not None:
        old_keys = _variant_photo_keys(product.variants)
        product.variants = [ProductVariant(**v.model_dump()) for v in data.variants]
        orphaned_keys |= old_keys - _variant_photo_keys(product.variants)

    if data.images is not None:
        # Images already on the product keep their row (and their real R2
        # key); only URLs that are new get rows, and rows whose URL is gone are
        # removed — along with their stored file, after the commit.
        existing = {img.url: img for img in product.images}
        has_primary = any(img.is_primary for img in data.images)
        kept: list[ProductImage] = []
        for idx, img in enumerate(data.images):
            row = existing.pop(img.url, None) or ProductImage(r2_key=stored_key_for(img.url), url=img.url)
            row.is_primary = img.is_primary or (idx == 0 and not has_primary)
            kept.append(row)
        orphaned_keys |= {img.r2_key for img in existing.values()}
        product.images = kept
        _sync_image_url_attribute(product)

    await db.commit()
    await db.refresh(product, attribute_names=["variants", "images"])
    await delete_objects(list(orphaned_keys))
    await _store_embeddings(db, [product])
    return product


async def delete_product(db: AsyncSession, product: Product) -> None:
    keys = {img.r2_key for img in product.images} | _variant_photo_keys(product.variants)
    await db.delete(product)
    await db.commit()
    await delete_objects(list(keys))


async def add_product_image(
    db: AsyncSession, product: Product, r2_key: str, url: str, is_primary: bool = False
) -> ProductImage:
    """Adds an uploaded image. The first image, or one uploaded as primary,
    becomes the only primary."""
    make_primary = is_primary or not product.images
    if make_primary:
        for img in product.images:
            img.is_primary = False
    image = ProductImage(product_id=product.id, r2_key=r2_key, url=url, is_primary=make_primary)
    product.images.append(image)
    _sync_image_url_attribute(product)
    await db.commit()
    await db.refresh(product, attribute_names=["images"])
    return image
