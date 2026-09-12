import uuid
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

# Numeric(14, 2) holds up to 999 999 999 999.99.
_MAX_PRICE = Decimal("999999999999.99")

# Validated exactly as Decimal (no float rounding on the way in), sent to
# clients as a JSON number like every other price in the API.
Money = Annotated[
    Decimal,
    Field(ge=0, le=_MAX_PRICE, decimal_places=2),
    PlainSerializer(float, return_type=float, when_used="json"),
]
_MAX_IMAGES = 20


def _check_image_url(url: str) -> str:
    """Product/variant images are served to customers by Instagram fetching
    the URL, and every URL is part of what the AI's tools return — so only
    real http(s) links. A `data:` URL (a whole photo inlined as base64) is
    megabytes in every product lookup and can't be sent on Instagram at all;
    upload the file instead (POST /products/media)."""
    cleaned = url.strip()
    if not (cleaned.startswith("https://") or cleaned.startswith("http://")):
        raise ValueError("image URLs must be http(s) links; upload files via /products/media")
    if len(cleaned) > 2048:
        raise ValueError("image URL is too long")
    return cleaned


def _check_attributes(value: dict) -> dict:
    """attributes["image_url"] is used as the product photo when there are no
    images, so it gets the same rule as every other image URL."""
    image_url = value.get("image_url")
    if image_url is not None:
        if not isinstance(image_url, str):
            raise ValueError("attributes.image_url must be a string URL")
        value = {**value, "image_url": _check_image_url(image_url)}
    return value


class VariantIn(BaseModel):
    variant_type: str = Field(default="combination", max_length=50)  # combination | size | color | ...
    value: str = Field(min_length=1, max_length=100)
    attributes: dict = Field(default_factory=dict)
    sku: str | None = Field(default=None, max_length=100)
    barcode: str | None = Field(default=None, max_length=100)
    price_override: Money | None = None
    stock_quantity: int | None = Field(default=None, ge=0, le=10_000_000)
    image_url: str | None = None
    images: list[str] = Field(default_factory=list, max_length=_MAX_IMAGES)
    availability: bool = True

    @field_validator("image_url")
    @classmethod
    def _image_url(cls, value: str | None) -> str | None:
        return _check_image_url(value) if value else None

    @field_validator("images")
    @classmethod
    def _images(cls, value: list[str]) -> list[str]:
        return [_check_image_url(v) for v in value if v and v.strip()]


class VariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    variant_type: str
    value: str
    attributes: dict = Field(default_factory=dict)
    sku: str | None = None
    barcode: str | None = None
    price_override: float | None = None
    stock_quantity: int | None = None
    image_url: str | None = None
    images: list[str] = Field(default_factory=list)
    availability: bool = True


class ImageIn(BaseModel):
    url: str
    is_primary: bool = False

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _check_image_url(value)


class ImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: str
    is_primary: bool


class MediaUploadOut(BaseModel):
    url: str


def _normalize_images(v: Any) -> list[dict]:
    if not v:
        return []
    if isinstance(v, str):
        return [{"url": v.strip(), "is_primary": True}]
    if isinstance(v, dict):
        return [{"url": str(v.get("url", "")).strip(), "is_primary": v.get("is_primary", True)}]
    if isinstance(v, list):
        res = []
        for idx, item in enumerate(v):
            if isinstance(item, str):
                if item.strip():
                    res.append({"url": item.strip(), "is_primary": idx == 0})
            elif isinstance(item, dict):
                url = item.get("url") or item.get("image_url") or ""
                if isinstance(url, str) and url.strip():
                    res.append({"url": url.strip(), "is_primary": item.get("is_primary", idx == 0)})
            elif hasattr(item, "url"):
                res.append({"url": getattr(item, "url", "").strip(), "is_primary": getattr(item, "is_primary", idx == 0)})
        return res
    return v


def _normalize_variants(v: Any) -> list[dict]:
    if not v:
        return []
    if not isinstance(v, list):
        return v
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

            val = str(val).strip() or "Standart"
            v_type = item.get("variant_type") or (
                "combination" if len(attrs) > 1 else (list(attrs.keys())[0] if attrs else "combination")
            )
            stock = item.get("stock_quantity") if item.get("stock_quantity") is not None else item.get("stock")
            if stock is None:
                stock = item.get("quantity") or item.get("miqdor")

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

            if isinstance(stock, str):
                stock = stock.strip() or None
            # A variant with zero stock is not available, whatever the flag says.
            availability = bool(item.get("availability", True)) and str(stock) not in ("0",)

            res.append(
                {
                    "variant_type": str(v_type)[:50],
                    "value": val[:100],
                    "attributes": attrs,
                    "sku": item.get("sku"),
                    "barcode": barcode_str,
                    "price_override": item.get("price_override"),
                    # Left for the field validator: a non-numeric or negative
                    # stock is rejected, not silently turned into "unknown".
                    "stock_quantity": stock,
                    "image_url": sku_image_url,
                    "images": sku_images,
                    "availability": availability,
                }
            )
        elif hasattr(item, "value"):
            res.append(
                {
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
                }
            )
    return res


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    price: Money | None = None
    currency: str = Field(default="UZS", min_length=3, max_length=10)
    availability: bool = True
    attributes: dict = Field(default_factory=dict)
    variants: list[VariantIn] = Field(default_factory=list, max_length=200)
    images: list[ImageIn] = Field(default_factory=list, max_length=_MAX_IMAGES)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name may not be blank")
        return cleaned

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("attributes")
    @classmethod
    def _attributes(cls, value: dict) -> dict:
        return _check_attributes(value)

    @field_validator("images", mode="before")
    @classmethod
    def _normalize_images(cls, v: Any) -> list[dict]:
        return _normalize_images(v)

    @field_validator("variants", mode="before")
    @classmethod
    def _normalize_variants(cls, v: Any) -> list[dict]:
        return _normalize_variants(v)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    price: Money | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=10)
    availability: bool | None = None
    attributes: dict | None = None
    variants: list[VariantIn] | None = Field(default=None, max_length=200)  # replaces the full variant set
    images: list[ImageIn] | None = Field(default=None, max_length=_MAX_IMAGES)

    @field_validator("name", "currency", "availability", "attributes")
    @classmethod
    def _not_null(cls, value):
        # NOT NULL columns: an explicit null is a client bug, not "clear it".
        # (Validators only run for fields the client actually sent.)
        if value is None:
            raise ValueError("may not be null")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name may not be blank")
        return cleaned

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("attributes")
    @classmethod
    def _attributes(cls, value: dict) -> dict:
        return _check_attributes(value)

    @field_validator("variants", mode="before")
    @classmethod
    def _normalize_variants(cls, v: Any) -> list[dict] | None:
        return None if v is None else _normalize_variants(v)

    @field_validator("images", mode="before")
    @classmethod
    def _normalize_images(cls, v: Any) -> list[dict] | None:
        return None if v is None else _normalize_images(v)


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
