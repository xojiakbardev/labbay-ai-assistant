"""Manual product/variant/image persistence — the single write path both manual
creation and AI-assisted ingestion (Phase 5) go through (plan §6)."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.products.embeddings import refresh_product_embedding
from app.products.models import Product, ProductImage, ProductVariant
from app.products.schemas import ProductCreate, ProductUpdate
from app.products.search_normalize import normalize_for_search


def _product_query():
    return select(Product).options(
        selectinload(Product.variants), selectinload(Product.images)
    )


async def create_product(
    db: AsyncSession, business_id: uuid.UUID, data: ProductCreate, source: str = "manual"
) -> Product:
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
    product.variants = [
        ProductVariant(**variant.model_dump()) for variant in data.variants
    ]

    images_to_add: list[ProductImage] = []
    if data.images:
        for idx, img in enumerate(data.images):
            if img.url and img.url.strip():
                is_prim = img.is_primary or (idx == 0 and not any(x.is_primary for x in data.images))
                images_to_add.append(
                    ProductImage(
                        r2_key=f"external/{uuid.uuid4()}",
                        url=img.url.strip(),
                        is_primary=is_prim,
                    )
                )
    elif attrs.get("image_url"):
        img_url = str(attrs["image_url"]).strip()
        if img_url:
            images_to_add.append(
                ProductImage(
                    r2_key=f"external/{uuid.uuid4()}",
                    url=img_url,
                    is_primary=True,
                )
            )

    product.images = images_to_add

    # Keep attributes["image_url"] populated for backwards compatibility / fast fallback
    if product.images and not attrs.get("image_url"):
        primary_img = next((img for img in product.images if img.is_primary), product.images[0])
        attrs["image_url"] = primary_img.url
        product.attributes = attrs

    db.add(product)
    await db.commit()
    await db.refresh(product, attribute_names=["variants", "images"])
    await refresh_product_embedding(db, product)
    return product


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

    if data.variants is not None:
        product.variants = [ProductVariant(**v.model_dump()) for v in data.variants]

    if data.images is not None:
        product.images = [
            ProductImage(
                r2_key=f"external/{uuid.uuid4()}",
                url=img.url.strip(),
                is_primary=img.is_primary or (idx == 0 and not any(x.is_primary for x in data.images)),
            )
            for idx, img in enumerate(data.images)
            if img.url and img.url.strip()
        ]
    elif product.attributes and "image_url" in product.attributes:
        img_url = str(product.attributes["image_url"]).strip() if product.attributes["image_url"] else None
        if img_url and not product.images:
            product.images = [
                ProductImage(
                    r2_key=f"external/{uuid.uuid4()}",
                    url=img_url,
                    is_primary=True,
                )
            ]

    # Ensure attributes["image_url"] matches primary image
    if product.images:
        primary_img = next((img for img in product.images if img.is_primary), product.images[0])
        attrs = dict(product.attributes) if product.attributes else {}
        attrs["image_url"] = primary_img.url
        product.attributes = attrs

    await db.commit()
    await db.refresh(product, attribute_names=["variants", "images"])
    await refresh_product_embedding(db, product)
    return product


async def delete_product(db: AsyncSession, product: Product) -> None:
    await db.delete(product)
    await db.commit()


async def add_product_image(
    db: AsyncSession, product: Product, r2_key: str, url: str, is_primary: bool = False
) -> ProductImage:
    image = ProductImage(product_id=product.id, r2_key=r2_key, url=url, is_primary=is_primary)
    db.add(image)
    await db.commit()
    await db.refresh(product, attribute_names=["images"])
    return image
