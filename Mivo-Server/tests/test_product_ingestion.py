"""Phase 5 acceptance tests: AI-assisted product ingestion, extract -> preview ->
confirm, with the LLM stubbed (no real credentials in dev — plan's mock guidance)."""
import json

from app.ai.provider.base import LLMProvider
from app.ai.provider.factory import get_llm_provider
from app.main import app
from app.products.ingestion.schemas import RawExtractedProduct, RawExtractedProductList


class FakeLLMProvider(LLMProvider):
    """Returns a canned structured response regardless of prompt — exercises the
    extract -> normalize -> validate pipeline without a real network call."""

    def __init__(self, response=None, raise_error: bool = False):
        self._response = response
        self._raise_error = raise_error

    async def generate_structured(self, *, system_prompt, user_content, response_schema):
        if self._raise_error:
            from app.ai.provider.base import LLMProviderError

            raise LLMProviderError("simulated provider failure")
        return self._response

    async def run_agentic_turn(self, **kwargs):
        raise NotImplementedError


NIKE_HOODIE_TEXT = """Nike Hoodie

250 000 so'm

Qora va oq ranglar.

Razmerlar:
S, M, L, XL

Issiq oversize hoodie.
Yetkazib berish mavjud."""

NIKE_HOODIE_EXTRACTED = RawExtractedProductList(
    products=[
        RawExtractedProduct(
            name="Nike Hoodie",
            description="Issiq oversize hoodie",
            price=250000,
            currency="so'm",
            colors=["Qora", "Oq"],
            sizes=["S", "M", "L", "XL"],
            images=["https://example.com/nike-hoodie.jpg"],
            availability=True,
        )
    ]
)


def _auth_headers(client) -> dict:
    from tests.conftest import create_business_and_headers

    return create_business_and_headers("importer@test.com", "Importer")


def test_preview_extracts_and_normalizes_without_writing(client) -> None:
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(NIKE_HOODIE_EXTRACTED)
    try:
        headers = _auth_headers(client)
        resp = client.post("/products/import/preview", json={"text": NIKE_HOODIE_TEXT}, headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["products"]) == 1
        product = body["products"][0]
        assert product["name"] == "Nike Hoodie"
        assert product["currency"] == "UZS"  # normalized from "so'm"
        assert product["price"] == 250000
        assert len(product["images"]) == 1
        assert product["images"][0]["url"] == "https://example.com/nike-hoodie.jpg"
        variant_types = {v["variant_type"] for v in product["variants"]}
        assert variant_types == {"color", "size"}

        # Nothing written yet.
        listed = client.get("/products", headers=headers)
        assert listed.json() == []
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


def test_confirm_writes_previewed_products_with_ai_import_source(client) -> None:
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(NIKE_HOODIE_EXTRACTED)
    try:
        headers = _auth_headers(client)
        preview = client.post(
            "/products/import/preview", json={"text": NIKE_HOODIE_TEXT}, headers=headers
        ).json()

        confirm = client.post(
            "/products/import/confirm", json={"products": preview["products"]}, headers=headers
        )
        assert confirm.status_code == 201
        created = confirm.json()
        assert len(created) == 1
        assert created[0]["source"] == "ai_import"
        assert created[0]["name"] == "Nike Hoodie"
        assert len(created[0]["images"]) == 1
        assert created[0]["images"][0]["url"] == "https://example.com/nike-hoodie.jpg"

        listed = client.get("/products", headers=headers)
        assert len(listed.json()) == 1
        assert len(listed.json()[0]["images"]) == 1
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


def test_preview_and_confirm_direct_json_with_images_and_variants(client) -> None:
    sample_json = [
        {
            "name": "Oversize Basic T-Shirt",
            "description": "Paxtadan tayyorlangan",
            "price": 149000,
            "currency": "UZS",
            "availability": True,
            "attributes": {
                "material": "100% cotton",
                "fit": "oversize",
                "category": "T-shirt",
                "image_url": "https://pronk.in/cdn/shop/files/tshirt.jpg",
            },
            "variants": [
                {"variant_type": "color", "value": "Oq", "stock_quantity": 10, "availability": True},
                {"variant_type": "size", "value": "M", "stock_quantity": 12, "availability": True},
            ],
            "images": [
                {
                    "url": "https://pronk.in/cdn/shop/files/tshirt.jpg",
                    "is_primary": True,
                }
            ],
        }
    ]
    headers = _auth_headers(client)
    # Direct JSON preview
    preview_resp = client.post(
        "/products/import/preview", json={"text": json.dumps(sample_json)}, headers=headers
    )
    assert preview_resp.status_code == 200
    preview_data = preview_resp.json()
    assert len(preview_data["products"]) == 1
    p = preview_data["products"][0]
    assert p["name"] == "Oversize Basic T-Shirt"
    assert len(p["images"]) == 1
    assert p["images"][0]["url"] == "https://pronk.in/cdn/shop/files/tshirt.jpg"
    assert len(p["variants"]) == 2

    # Direct JSON confirm
    confirm_resp = client.post(
        "/products/import/confirm", json={"products": preview_data["products"]}, headers=headers
    )
    assert confirm_resp.status_code == 201
    created_list = confirm_resp.json()
    assert len(created_list) == 1
    assert len(created_list[0]["images"]) == 1
    assert created_list[0]["images"][0]["url"] == "https://pronk.in/cdn/shop/files/tshirt.jpg"
    assert created_list[0]["images"][0]["is_primary"] is True

    # Check list endpoint
    list_resp = client.get("/products", headers=headers)
    assert list_resp.status_code == 200
    all_products = list_resp.json()
    found = next((x for x in all_products if x["name"] == "Oversize Basic T-Shirt"), None)
    assert found is not None
    assert len(found["images"]) == 1
    assert found["images"][0]["url"] == "https://pronk.in/cdn/shop/files/tshirt.jpg"


def test_preview_rejects_when_llm_fails(client) -> None:
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(raise_error=True)
    try:
        headers = _auth_headers(client)
        resp = client.post("/products/import/preview", json={"text": "plain text not json"}, headers=headers)
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


def test_preview_rejects_empty_extraction(client) -> None:
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(
        RawExtractedProductList(products=[])
    )
    try:
        headers = _auth_headers(client)
        resp = client.post("/products/import/preview", json={"text": "gibberish text"}, headers=headers)
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


def test_import_endpoints_require_auth(client) -> None:
    resp = client.post("/products/import/preview", json={"text": "hi"})
    assert resp.status_code == 401
