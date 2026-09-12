"""Phase 4 acceptance tests: manual product CRUD + R2 image upload (plan §12)."""
import io
import uuid

from app.main import app
from app.storage.r2 import get_r2_storage


class FakeR2Storage:
    """No real R2 credentials in dev/test — stub the storage boundary per plan's
    'use mocks/stubs for external APIs where credentials are not available'."""

    def __init__(self) -> None:
        self.uploaded: dict[str, bytes] = {}

    def build_key(self, business_id, product_id, extension):
        folder = product_id or "unassigned"
        return f"{business_id}/products/{folder}/{uuid.uuid4()}.{extension}"

    async def upload_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self.uploaded[key] = data
        return f"https://fake-r2.example.com/{key}"

    def delete_keys(self, keys):
        for key in keys:
            self.uploaded.pop(key, None)


def _auth_headers(client) -> dict:
    from tests.conftest import create_business_and_headers

    return create_business_and_headers("shop@test.com", "Shop")


def test_create_and_list_product(client) -> None:
    headers = _auth_headers(client)
    resp = client.post(
        "/products",
        json={
            "name": "Nike Hoodie",
            "description": "Issiq oversize hoodie",
            "price": 250000,
            "currency": "UZS",
            "variants": [
                {"variant_type": "size", "value": "M"},
                {"variant_type": "color", "value": "black"},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Nike Hoodie"
    assert body["source"] == "manual"
    assert len(body["variants"]) == 2

    listed = client.get("/products", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_update_product(client) -> None:
    headers = _auth_headers(client)
    created = client.post("/products", json={"name": "Old Name"}, headers=headers).json()

    resp = client.patch(
        f"/products/{created['id']}", json={"name": "New Name", "price": 99000}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"
    assert resp.json()["price"] == 99000.0


def test_delete_product(client) -> None:
    headers = _auth_headers(client)
    created = client.post("/products", json={"name": "Temp"}, headers=headers).json()

    resp = client.delete(f"/products/{created['id']}", headers=headers)
    assert resp.status_code == 204

    listed = client.get("/products", headers=headers)
    assert listed.json() == []


def test_product_endpoints_are_tenant_scoped(client) -> None:
    from tests.conftest import create_business_and_headers

    a_headers = _auth_headers(client)
    b_headers = create_business_and_headers("other@test.com", "Other")

    a_product = client.post("/products", json={"name": "A's Product"}, headers=a_headers).json()

    # B must not be able to see or modify A's product.
    resp = client.patch(f"/products/{a_product['id']}", json={"name": "Hijacked"}, headers=b_headers)
    assert resp.status_code == 404
    resp = client.delete(f"/products/{a_product['id']}", headers=b_headers)
    assert resp.status_code == 404


def test_image_upload_validates_type_and_size(client) -> None:
    fake_storage = FakeR2Storage()
    app.dependency_overrides[get_r2_storage] = lambda: fake_storage
    try:
        headers = _auth_headers(client)
        product = client.post("/products", json={"name": "With Image"}, headers=headers).json()

        # Rejected content type
        bad = client.post(
            f"/products/{product['id']}/images",
            files={"file": ("doc.pdf", io.BytesIO(b"not an image"), "application/pdf")},
            headers=headers,
        )
        assert bad.status_code == 400

        # Oversized file
        too_big = client.post(
            f"/products/{product['id']}/images",
            files={"file": ("big.jpg", io.BytesIO(b"x" * (6 * 1024 * 1024)), "image/jpeg")},
            headers=headers,
        )
        assert too_big.status_code == 400

        # Claims to be a JPEG but isn't one: the type comes from the bytes,
        # never from the client's Content-Type header.
        spoofed = client.post(
            f"/products/{product['id']}/images",
            files={"file": ("evil.jpg", io.BytesIO(b"<svg onload=alert(1)>"), "image/jpeg")},
            headers=headers,
        )
        assert spoofed.status_code == 400

        # Valid upload (real JPEG magic bytes); stored as .jpg whatever the
        # uploaded file name said.
        good = client.post(
            f"/products/{product['id']}/images",
            files={"file": ("hoodie.html", io.BytesIO(b"\xff\xd8\xff\xe0" + b"0" * 64), "image/jpeg")},
            headers=headers,
        )
        assert good.status_code == 201
        assert good.json()["url"].startswith("https://fake-r2.example.com/")
        assert good.json()["url"].endswith(".jpg")
        assert len(fake_storage.uploaded) == 1
        assert good.json()["is_primary"] is True  # the first image becomes primary
    finally:
        app.dependency_overrides.pop(get_r2_storage, None)


def test_product_validation_rejects_bad_values(client) -> None:
    headers = _auth_headers(client)
    for bad in (
        {"name": "Neg", "price": -1},
        {"name": ""},
        {"name": "Big", "price": 10**13},
        {"name": "Img", "images": ["data:image/png;base64,AAAA"]},
        {"name": "Var", "variants": [{"value": "M", "stock_quantity": -3}]},
    ):
        assert client.post("/products", json=bad, headers=headers).status_code == 422, bad

    product = client.post("/products", json={"name": "Ok"}, headers=headers).json()
    assert client.patch(f"/products/{product['id']}", json={"name": None}, headers=headers).status_code == 422
    assert client.patch(f"/products/{product['id']}", json={"currency": None}, headers=headers).status_code == 422


def test_media_upload_returns_a_url_for_variant_photos(client) -> None:
    fake_storage = FakeR2Storage()
    app.dependency_overrides[get_r2_storage] = lambda: fake_storage
    try:
        headers = _auth_headers(client)
        resp = client.post(
            "/products/media",
            files={"file": ("v.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 32), "image/png")},
            headers=headers,
        )
        assert resp.status_code == 201
        assert "/products/unassigned/" in resp.json()["url"] and resp.json()["url"].endswith(".png")
    finally:
        app.dependency_overrides.pop(get_r2_storage, None)


def test_editing_images_keeps_existing_rows(client) -> None:
    headers = _auth_headers(client)
    product = client.post(
        "/products",
        json={"name": "Two pics", "images": [{"url": "https://cdn/a.jpg", "is_primary": True}, {"url": "https://cdn/b.jpg"}]},
        headers=headers,
    ).json()
    ids = {img["url"]: img["id"] for img in product["images"]}
    updated = client.patch(
        f"/products/{product['id']}",
        json={"images": [{"url": "https://cdn/b.jpg", "is_primary": True}, {"url": "https://cdn/c.jpg"}]},
        headers=headers,
    ).json()
    by_url = {img["url"]: img for img in updated["images"]}
    assert set(by_url) == {"https://cdn/b.jpg", "https://cdn/c.jpg"}
    assert by_url["https://cdn/b.jpg"]["id"] == ids["https://cdn/b.jpg"]  # same row kept
    assert by_url["https://cdn/b.jpg"]["is_primary"] is True
    assert updated["attributes"]["image_url"] == "https://cdn/b.jpg"
