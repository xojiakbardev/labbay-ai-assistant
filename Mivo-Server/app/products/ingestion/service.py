"""Single orchestration point for AI-assisted product ingestion (plan §6):
extract() -> preview (nothing written) ; confirm() -> validated write via the
same products/crud.py functions manual creation uses.
"""
import json
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider.base import LLMProvider, LLMProviderError
from app.products import crud
from app.products.ingestion.extraction import extract_products
from app.products.ingestion.normalization import normalize_extracted_list
from app.products.models import Product
from app.products.schemas import ProductCreate


class IngestionError(Exception):
    """Raised when extraction fails or returns nothing usable — caller maps to 422."""


def _try_parse_json_products(text: str) -> list[Any] | None:
    trimmed = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", trimmed)
    candidate = match.group(1).strip() if match else trimmed

    if (candidate.startswith("[") and candidate.endswith("]")) or (candidate.startswith("{") and candidate.endswith("}")):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                if all(isinstance(x, dict) for x in parsed) and any("name" in x for x in parsed):
                    return parsed
            elif isinstance(parsed, dict):
                for key in ["products", "items", "data", "catalog", "goods"]:
                    if key in parsed and isinstance(parsed[key], list) and all(isinstance(x, dict) for x in parsed[key]):
                        return parsed[key]
                if "product" in parsed and isinstance(parsed["product"], dict) and "name" in parsed["product"]:
                    return [parsed["product"]]
                if "name" in parsed:
                    return [parsed]
        except Exception:
            pass
    return None


async def preview_import(raw_text: str, provider: LLMProvider) -> list[ProductCreate]:
    # 1. Fast and deterministic JSON parser for direct structured inputs
    direct_json = _try_parse_json_products(raw_text)
    if direct_json:
        try:
            products = normalize_extracted_list(direct_json)
            if products:
                return products
        except Exception:
            pass

    # 2. Advanced LLM extraction for unstructured / messy / multi-language text
    try:
        raw = await extract_products(raw_text, provider)
    except LLMProviderError as exc:
        raise IngestionError(str(exc)) from exc

    products = normalize_extracted_list(raw)
    if not products:
        raise IngestionError("No products could be extracted from the provided text.")
    return products


async def confirm_import(
    db: AsyncSession, business_id: uuid.UUID, products: list[ProductCreate]
) -> list[Product]:
    """Re-validates (Pydantic already did on request parse) and writes — only
    ever called after the business owner has reviewed/edited the preview."""
    return [
        await crud.create_product(db, business_id, product, source="ai_import")
        for product in products
    ]
