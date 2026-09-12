"""Unit tests for app/products/media.py — the boundary that turns model-named
product ids into real, business-scoped image URLs (or nothing)."""
import uuid

import pytest

from app.auth.models import User
from app.businesses.models import Business
from app.products.media import get_display_image_url, resolve_sendable_images
from app.products.models import Product, ProductImage


async def _seed_business(db_session) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Media Test Biz")
    db_session.add(business)
    await db_session.flush()
    return business.id


def test_get_display_image_url_prefers_primary_r2_image() -> None:
    product = Product(
        name="Shoe", currency="UZS", availability=True,
        attributes={"image_url": "https://example.com/fallback.jpg"}, source="manual",
    )
    product.images = [
        ProductImage(r2_key="k1", url="https://r2.example.com/secondary.jpg", is_primary=False),
        ProductImage(r2_key="k2", url="https://r2.example.com/primary.jpg", is_primary=True),
    ]
    assert get_display_image_url(product) == "https://r2.example.com/primary.jpg"


def test_get_display_image_url_falls_back_to_attributes_link() -> None:
    product = Product(
        name="Shoe", currency="UZS", availability=True,
        attributes={"image_url": "https://example.com/fallback.jpg"}, source="manual",
    )
    assert get_display_image_url(product) == "https://example.com/fallback.jpg"


def test_get_display_image_url_none_when_nothing_available() -> None:
    product = Product(name="Shoe", currency="UZS", availability=True, attributes={}, source="manual")
    assert get_display_image_url(product) is None


@pytest.mark.asyncio
async def test_resolve_sendable_images_is_tenant_scoped(db_session) -> None:
    business_a = await _seed_business(db_session)
    business_b = await _seed_business(db_session)
    product_a = Product(
        business_id=business_a, name="A", currency="UZS", availability=True,
        attributes={"image_url": "https://example.com/a.jpg"}, source="manual",
    )
    product_b = Product(
        business_id=business_b, name="B", currency="UZS", availability=True,
        attributes={"image_url": "https://example.com/b.jpg"}, source="manual",
    )
    db_session.add_all([product_a, product_b])
    await db_session.commit()

    photos = await resolve_sendable_images(db_session, business_a, [str(product_a.id), str(product_b.id)])

    assert photos == [("A", "https://example.com/a.jpg")]  # business_b's product dropped


@pytest.mark.asyncio
async def test_resolve_sendable_images_ignores_invalid_ids(db_session) -> None:
    business_id = await _seed_business(db_session)
    urls = await resolve_sendable_images(db_session, business_id, ["not-a-uuid", str(uuid.uuid4())])
    assert urls == []


@pytest.mark.asyncio
async def test_resolve_sendable_images_caps_at_max(db_session) -> None:
    business_id = await _seed_business(db_session)
    products = [
        Product(
            business_id=business_id, name=f"P{i}", currency="UZS", availability=True,
            attributes={"image_url": f"https://example.com/{i}.jpg"}, source="manual",
        )
        for i in range(5)
    ]
    db_session.add_all(products)
    await db_session.commit()

    urls = await resolve_sendable_images(db_session, business_id, [str(p.id) for p in products])
    assert len(urls) == 3
