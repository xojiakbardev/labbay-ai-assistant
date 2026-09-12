"""LLM call that turns pasted business text or catalog data into structured product data.

The LLM never touches PostgreSQL — it only ever returns JSON that this module
hands off to normalization + Pydantic validation (plan §6/§37).
"""
import uuid

from app.ai.provider.base import LLMProvider
from app.products.ingestion.schemas import RawExtractedProductList
from app import prompts

SYSTEM_PROMPT = prompts.load("catalog_extraction.md")


async def extract_products(
    raw_text: str, provider: LLMProvider, business_id: uuid.UUID | None = None
) -> RawExtractedProductList:
    return await provider.generate_structured(
        system_prompt=SYSTEM_PROMPT,
        user_content=raw_text,
        response_schema=RawExtractedProductList,
        business_id=business_id,  # so extraction shows up in the usage/cost figures
    )
