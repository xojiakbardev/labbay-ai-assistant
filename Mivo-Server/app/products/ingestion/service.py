"""Single orchestration point for AI-assisted product ingestion (plan §6):
extract() -> preview (nothing written) ; confirm() -> validated write via the
same products/crud.py functions manual creation uses.
"""
import json
import re
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider.base import LLMProvider, LLMProviderError
from app.products import crud
from app.products.ingestion.extraction import extract_products
from app.products.ingestion.normalization import normalize_extracted_list
from app.products.models import Product
from app.products.schemas import ProductCreate


class IngestionError(Exception):
    """Raised when extraction fails or returns nothing usable — caller maps to 422."""


def _parse_json_products(text: str) -> list[Any] | None:
    """Direct JSON input (a list of products, or an object wrapping one).
    Returns None when the text isn't JSON at all — that's what the LLM path is
    for. Text that IS JSON but not a product list is not guessed at."""
    trimmed = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", trimmed)
    candidate = match.group(1).strip() if match else trimmed
    if not (candidate.startswith(("[", "{")) and candidate.endswith(("]", "}"))):
        return None
    try:
        parsed = json.loads(candidate)
    except ValueError as exc:
        # It's meant to be JSON and it's broken — say so, rather than quietly
        # paying the LLM to guess at it.
        raise IngestionError(f"The JSON is invalid: {exc}") from exc

    if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
        return parsed
    if isinstance(parsed, dict):
        for key in ("products", "items", "data", "catalog", "goods"):
            if isinstance(parsed.get(key), list) and all(isinstance(x, dict) for x in parsed[key]):
                return parsed[key]
        if isinstance(parsed.get("product"), dict):
            return [parsed["product"]]
        if "name" in parsed:
            return [parsed]
    raise IngestionError("The JSON doesn't contain a list of products.")


def _validation_message(exc: ValueError) -> str:
    if not isinstance(exc, ValidationError):
        return str(exc)
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first.get("loc", ()))
    return f"Invalid product data at {where}: {first.get('msg')}"


async def preview_import(
    raw_text: str, provider: LLMProvider, business_id: uuid.UUID | None = None
) -> list[ProductCreate]:
    # 1. Structured input is parsed deterministically — and if it's invalid,
    #    the owner is told what's wrong instead of it being sent to the LLM.
    direct_json = _parse_json_products(raw_text)
    if direct_json is not None:
        try:
            products = normalize_extracted_list(direct_json)
        except ValueError as exc:  # includes pydantic's ValidationError
            raise IngestionError(_validation_message(exc)) from exc
        if not products:
            raise IngestionError("No products found in the JSON.")
        return products

    # 2. LLM extraction for unstructured / messy / multi-language text
    try:
        raw = await extract_products(raw_text, provider, business_id=business_id)
    except LLMProviderError as exc:
        raise IngestionError(str(exc)) from exc

    try:
        products = normalize_extracted_list(raw)
    except ValueError as exc:  # includes pydantic's ValidationError
        raise IngestionError(_validation_message(exc)) from exc
    if not products:
        raise IngestionError("No products could be extracted from the provided text.")
    return products


async def confirm_import(
    db: AsyncSession, business_id: uuid.UUID, products: list[ProductCreate]
) -> list[Product]:
    """Writes the reviewed products in one transaction (all or nothing), then
    embeds them in one batched call."""
    return await crud.create_products(db, business_id, products, source="ai_import")
