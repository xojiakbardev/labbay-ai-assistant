"""LLM-facing extraction schema (plan §6).

This is the raw shape the model returns. `normalization.py` converts this into
the same `ProductCreate` shape manual entry uses, so both write paths share one
CRUD function and one validation schema below this layer.
"""
from typing import Any
from pydantic import BaseModel, Field


class RawExtractedVariant(BaseModel):
    variant_type: str = Field(default="size", description="size | color | material | style")
    value: str = Field(description="The variant value, e.g. 'M', '42', 'Qora', 'White'")
    sku: str | None = None
    price_override: float | None = None
    stock_quantity: int | None = None
    availability: bool = True


class RawExtractedProduct(BaseModel):
    name: str = Field(description="Product title / name")
    description: str | None = Field(default=None, description="Detailed product description, materials, features")
    price: float | None = Field(default=None, description="Numeric price without currency symbols")
    currency: str | None = Field(default="UZS", description="Currency code (e.g. UZS, USD)")
    category: str | None = Field(default=None, description="Product category (e.g. T-shirt, Hoodie, Poyabzal, Kiyim)")
    colors: list[str] = Field(default_factory=list, description="List of available color names")
    sizes: list[str] = Field(default_factory=list, description="List of available size names")
    variants: list[RawExtractedVariant] = Field(default_factory=list, description="Full variant list with type, value, stock count")
    images: list[str] = Field(default_factory=list, description="List of image URLs or CDN links found in the text or data")
    attributes: dict[str, Any] = Field(default_factory=dict, description="Key-value product attributes like material, fit, brand, origin")
    availability: bool = Field(default=True, description="Whether the product is currently in stock")


class RawExtractedProductList(BaseModel):
    products: list[RawExtractedProduct] = Field(default_factory=list)
