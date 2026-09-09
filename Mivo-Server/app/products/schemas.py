import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VariantIn(BaseModel):
    variant_type: str = Field(default="combination", max_length=50)  # combination | size | color | ...
    value: str = Field(max_length=100)
    attributes: dict = Field(default_factory=dict)
    sku: str | None = None
    barcode: str | None = None
    price_override: float | None = None
    stock_quantity: int | None = None
    image_url: str | None = None
    images: list[str] = Field(default_factory=list)
    availability: bool = True


class VariantOut(VariantIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    attributes: dict = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)


class ImageIn(BaseModel):
    url: str
    is_primary: bool = False


class ImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: str
    is_primary: bool


class ProductCreate(BaseModel):
    name: str = Field(max_length=255)
    description: str | None = None
    price: float | None = None
    currency: str = "UZS"
    availability: bool = True
    attributes: dict = Field(default_factory=dict)
    variants: list[VariantIn] = Field(default_factory=list)
    images: list[ImageIn] = Field(default_factory=list)

    @field_validator("images", mode="before")
    @classmethod
    def _normalize_images(cls, v: Any) -> list[dict]:
        if not v:
            return []
        if isinstance(v, str):
            return [{"url": v.strip(), "is_primary": True}]
        if isinstance(v, dict):
            return [{"url": v.get("url", "").strip(), "is_primary": v.get("is_primary", True)}]
        if isinstance(v, list):
            res = []
            for idx, item in enumerate(v):
                if isinstance(item, str):
                    if item.strip():
                        res.append({"url": item.strip(), "is_primary": idx == 0})
                elif isinstance(item, dict):
                    url = item.get("url") or item.get("image_url") or ""
                    if isinstance(url, str) and url.strip():
                        res.append({
                            "url": url.strip(),
                            "is_primary": item.get("is_primary", idx == 0)
                        })
                elif hasattr(item, "url"):
                    res.append({
                        "url": getattr(item, "url", "").strip(),
                        "is_primary": getattr(item, "is_primary", idx == 0)
                    })
            return res
        return v

    @field_validator("variants", mode="before")
    @classmethod
    def _normalize_variants(cls, v: Any) -> list[dict]:
        if not v:
            return []
        if isinstance(v, list):
            res = []
            for item in v:
                if isinstance(item, str):
                    if item.strip():
                        res.append({"variant_type": "combination", "value": item.strip(), "attributes": {}})
                elif isinstance(item, dict):
                    val = item.get("value") or item.get("name") or item.get("title") or ""
                    attrs = dict(item.get("attributes") or {})

                    for k in ["color", "size", "taste", "flavor", "volume", "storage", "weight", "material"]:
                        if k in item and item[k]:
                            attrs[k] = str(item[k]).strip()

                    if not val and attrs:
                        val = " / ".join(str(val_item) for val_item in attrs.values() if str(val_item).strip())

                    val = str(val).strip()
                    if not val:
                        val = "Standart"

                    v_type = item.get("variant_type") or ("combination" if len(attrs) > 1 else (list(attrs.keys())[0] if attrs else "combination"))
                    stock = item.get("stock_quantity") if item.get("stock_quantity") is not None else item.get("stock")
                    if stock is None:
                        stock = item.get("quantity") or item.get("miqdor")

                    try:
                        stock_int = int(stock) if stock is not None and str(stock).strip() != "" else None
                    except (ValueError, TypeError):
                        stock_int = None

                    # Parse SKU images / photos
                    sku_images: list[str] = []
                    raw_images = item.get("images") or item.get("photos") or item.get("gallery") or []
                    if isinstance(raw_images, str) and raw_images.strip():
                        sku_images = [raw_images.strip()]
                    elif isinstance(raw_images, list):
                        for img in raw_images:
                            if isinstance(img, str) and img.strip():
                                sku_images.append(img.strip())
                            elif isinstance(img, dict) and (img.get("url") or img.get("image_url")):
                                u = str(img.get("url") or img.get("image_url")).strip()
                                if u:
                                    sku_images.append(u)

                    sku_image_url = item.get("image_url") or item.get("photo") or item.get("image")
                    if isinstance(sku_image_url, str) and sku_image_url.strip():
                        sku_image_url = sku_image_url.strip()
                        if sku_image_url not in sku_images:
                            sku_images.insert(0, sku_image_url)
                    elif sku_images:
                        sku_image_url = sku_images[0]
                    else:
                        sku_image_url = None

                    barcode = item.get("barcode") or item.get("shtrixkod") or item.get("bar_code")
                    barcode_str = str(barcode).strip() if barcode is not None and str(barcode).strip() else None

                    res.append({
                        "variant_type": str(v_type)[:50],
                        "value": val[:100],
                        "attributes": attrs,
                        "sku": item.get("sku"),
                        "barcode": barcode_str,
                        "price_override": item.get("price_override"),
                        "stock_quantity": stock_int,
                        "image_url": sku_image_url,
                        "images": sku_images,
                        "availability": bool(item.get("availability", True)) if (stock_int is None or stock_int > 0) else False,
                    })
                elif hasattr(item, "value"):
                    res.append({
                        "variant_type": getattr(item, "variant_type", "combination"),
                        "value": getattr(item, "value", "Standart"),
                        "attributes": getattr(item, "attributes", {}),
                        "sku": getattr(item, "sku", None),
                        "barcode": getattr(item, "barcode", None),
                        "price_override": getattr(item, "price_override", None),
                        "stock_quantity": getattr(item, "stock_quantity", None),
                        "image_url": getattr(item, "image_url", None),
                        "images": getattr(item, "images", []),
                        "availability": bool(getattr(item, "availability", True)),
                    })
            return res
        return v


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price: float | None = None
    currency: str | None = None
    availability: bool | None = None
    attributes: dict | None = None
    variants: list[VariantIn] | None = None  # replaces the full variant set if provided
    images: list[ImageIn] | None = None

    @field_validator("variants", mode="before")
    @classmethod
    def _normalize_variants(cls, v: Any) -> list[dict] | None:
        if v is None:
            return None
        return ProductCreate._normalize_variants(v)

    @field_validator("images", mode="before")
    @classmethod
    def _normalize_images(cls, v: Any) -> list[dict] | None:
        if v is None:
            return None
        if isinstance(v, str):
            return [{"url": v.strip(), "is_primary": True}]
        if isinstance(v, dict):
            return [{"url": v.get("url", "").strip(), "is_primary": v.get("is_primary", True)}]
        if isinstance(v, list):
            res = []
            for idx, item in enumerate(v):
                if isinstance(item, str):
                    if item.strip():
                        res.append({"url": item.strip(), "is_primary": idx == 0})
                elif isinstance(item, dict):
                    url = item.get("url") or item.get("image_url") or ""
                    if isinstance(url, str) and url.strip():
                        res.append({
                            "url": url.strip(),
                            "is_primary": item.get("is_primary", idx == 0)
                        })
                elif hasattr(item, "url"):
                    res.append({
                        "url": getattr(item, "url", "").strip(),
                        "is_primary": getattr(item, "is_primary", idx == 0)
                    })
            return res
        return v


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    price: float | None
    currency: str
    availability: bool
    attributes: dict
    source: str
    variants: list[VariantOut]
    images: list[ImageOut]
