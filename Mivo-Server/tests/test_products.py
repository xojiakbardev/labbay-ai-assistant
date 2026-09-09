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

    def build_key(self, business_id, product_id, filename):
        return f"{business_id}/products/{product_id}/{uuid.uuid4()}.jpg"

    def upload_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self.uploaded[key] = data
        return f"https://fake-r2.example.com/{key}"


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

        # Valid upload
        good = client.post(
            f"/products/{product['id']}/images",
            files={"file": ("hoodie.jpg", io.BytesIO(b"fake-jpeg-bytes"), "image/jpeg")},
            headers=headers,
        )
        assert good.status_code == 201
        assert good.json()["url"].startswith("https://fake-r2.example.com/")
        assert len(fake_storage.uploaded) == 1
    finally:
        app.dependency_overrides.pop(get_r2_storage, None)
