"""Normalize raw LLM extraction output into the same ProductCreate shape manual
entry uses — one write path for both (plan §6)."""
import re
from typing import Any

from app.products.ingestion.schemas import RawExtractedProduct, RawExtractedProductList
from app.products.schemas import ImageIn, ProductCreate, VariantIn
from app import prompts

_CURRENCY_ALIASES = prompts.lexicon()["currency_aliases"]


def _normalize_currency(raw: str | None) -> str:
    if not raw:
        return "UZS"
    cleaned = raw.strip().lower()
    return _CURRENCY_ALIASES.get(cleaned, raw.strip().upper())


def _parse_price_and_currency(
    raw_price: Any, raw_currency: str | None
) -> tuple[float | None, str]:
    if raw_price is None or raw_price == "":
        return None, _normalize_currency(raw_currency)

    if isinstance(raw_price, (int, float)):
        return round(float(raw_price), 2), _normalize_currency(raw_currency)

    text = str(raw_price).strip().lower()
    if not text:
        return None, _normalize_currency(raw_currency)

    curr = raw_currency
    if "$" in text or "usd" in text or "dollar" in text:
        curr = curr or "USD"
    elif any(s in text for s in ["so'm", "som", "сум", "sum", "uzs"]):
        curr = curr or "UZS"

    multiplier = 1.0
    if "k" in text:
        multiplier = 1000.0
        text = text.replace("k", "")

    for word in ["so'm", "som", "сум", "sum", "uzs", "usd", "$", "dollar", "ming", "минг"]:
        text = text.replace(word, "")

    text = text.strip()
    if re.match(r"^\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,2})?$", text):
        parts = re.split(r"[.,\s]", text)
        if len(parts[-1]) == 3:
            text = "".join(parts)
        else:
            text = "".join(parts[:-1]) + "." + parts[-1]
    else:
        text = text.replace(" ", "").replace(",", ".")

    try:
        val = round(float(text) * multiplier, 2)
    except ValueError as exc:
        # An unreadable price is reported, never silently turned into "no price".
        raise ValueError(f"Can't read the price {raw_price!r}") from exc
    return val, _normalize_currency(curr)


def _dedupe_case_insensitive(values: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for v in values:
        if isinstance(v, str):
            key = v.strip().lower()
            if key and key not in seen:
                seen[key] = v.strip()
    return list(seen.values())


def normalize_extracted_product(raw: RawExtractedProduct | dict[str, Any]) -> ProductCreate:
    if isinstance(raw, dict):
        raw_name = str(raw.get("name", "")).strip()
        raw_desc = raw.get("description")
        raw_price = raw.get("price")
        raw_curr = raw.get("currency")
        raw_cat = raw.get("category")
        raw_colors = raw.get("colors") or []
        raw_sizes = raw.get("sizes") or []
        raw_variants = raw.get("variants") or []
        raw_images = raw.get("images") or []
        raw_attrs = raw.get("attributes") or {}
        raw_avail = raw.get("availability", True)
    else:
        raw_name = raw.name.strip()
        raw_desc = raw.description
        raw_price = raw.price
        raw_curr = raw.currency
        raw_cat = raw.category
        raw_colors = raw.colors or []
        raw_sizes = raw.sizes or []
        raw_variants = raw.variants or []
        raw_images = raw.images or []
        raw_attrs = raw.attributes or {}
        raw_avail = raw.availability

    price_val, currency_val = _parse_price_and_currency(raw_price, raw_curr)

    seen_variants = set()
    variants: list[VariantIn] = []

    # 1. Process explicit variants
    for v in raw_variants:
        if isinstance(v, dict):
            val = v.get("value") or v.get("name") or v.get("title") or ""
            attrs = dict(v.get("attributes") or {})
            for k in ["color", "size", "taste", "flavor", "volume", "storage", "weight", "material"]:
                if k in v and v[k]:
                    attrs[k] = str(v[k]).strip()
            if not val and attrs:
                val = " / ".join(str(val_item) for val_item in attrs.values() if str(val_item).strip())
            val = str(val).strip()
            if not val:
                continue

            v_type = str(v.get("variant_type") or ("combination" if len(attrs) > 1 else (list(attrs.keys())[0] if attrs else "combination"))).strip()
            stock = v.get("stock_quantity") if v.get("stock_quantity") is not None else v.get("stock")
            if stock is None:
                stock = v.get("quantity") or v.get("miqdor")
            try:
                stock_int = int(stock) if stock is not None and str(stock).strip() != "" else None
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Can't read the stock {stock!r} of variant {val!r}") from exc

            # Parse SKU images / photos
            sku_images: list[str] = []
            raw_imgs = v.get("images") or v.get("photos") or v.get("gallery") or []
            if isinstance(raw_imgs, str) and raw_imgs.strip():
                sku_images = [raw_imgs.strip()]
            elif isinstance(raw_imgs, list):
                for img_item in raw_imgs:
                    if isinstance(img_item, str) and img_item.strip():
                        sku_images.append(img_item.strip())
                    elif isinstance(img_item, dict) and (img_item.get("url") or img_item.get("image_url")):
                        u = str(img_item.get("url") or img_item.get("image_url")).strip()
                        if u:
                            sku_images.append(u)

            sku_image_url = v.get("image_url") or v.get("photo") or v.get("image")
            if isinstance(sku_image_url, str) and sku_image_url.strip():
                sku_image_url = sku_image_url.strip()
                if sku_image_url not in sku_images:
                    sku_images.insert(0, sku_image_url)
            sku_images = [u for u in sku_images if u.startswith(("https://", "http://"))]
            sku_image_url = sku_images[0] if sku_images else None

            barcode = v.get("barcode") or v.get("shtrixkod") or v.get("bar_code")
            barcode_str = str(barcode).strip() if barcode is not None and str(barcode).strip() else None

            key = (v_type.lower(), val.lower())
            if key not in seen_variants:
                seen_variants.add(key)
                variants.append(
                    VariantIn(
                        variant_type=v_type,
                        value=val,
                        attributes=attrs,
                        sku=v.get("sku"),
                        barcode=barcode_str,
                        price_override=v.get("price_override"),
                        stock_quantity=stock_int,
                        image_url=sku_image_url,
                        images=sku_images,
                        availability=bool(v.get("availability", True)) if (stock_int is None or stock_int > 0) else False,
                    )
                )
        elif hasattr(v, "value"):
            val = str(getattr(v, "value", "")).strip()
            if not val:
                continue
            v_type = getattr(v, "variant_type", "combination").strip().lower()
            key = (v_type, val.lower())
            if key not in seen_variants:
                seen_variants.add(key)
                variants.append(
                    VariantIn(
                        variant_type=v_type,
                        value=val,
                        attributes=getattr(v, "attributes", {}),
                        sku=getattr(v, "sku", None),
                        barcode=getattr(v, "barcode", None),
                        price_override=getattr(v, "price_override", None),
                        stock_quantity=getattr(v, "stock_quantity", None),
                        image_url=getattr(v, "image_url", None),
                        images=getattr(v, "images", []),
                        availability=bool(getattr(v, "availability", True)),
                    )
                )

    # 2. If both colors and sizes are present and no explicit combination variants exist, build combinations matrix!
    deduped_colors = _dedupe_case_insensitive(raw_colors)
    deduped_sizes = _dedupe_case_insensitive(raw_sizes)

    if deduped_colors and deduped_sizes and not variants:
        for c in deduped_colors:
            for s in deduped_sizes:
                combo_val = f"{c} / {s}"
                key = ("combination", combo_val.lower())
                if key not in seen_variants:
                    seen_variants.add(key)
                    variants.append(
                        VariantIn(
                            variant_type="combination",
                            value=combo_val,
                            attributes={"color": c, "size": s},
                            availability=True,
                        )
                    )
    else:
        # Otherwise add remaining colors / sizes individually if not yet represented
        for color in deduped_colors:
            key = ("color", color.lower())
            if key not in seen_variants and not any(v.value.lower() == color.lower() or color.lower() in v.value.lower() for v in variants):
                seen_variants.add(key)
                variants.append(VariantIn(variant_type="color", value=color, attributes={"color": color}))

        for size in deduped_sizes:
            key = ("size", size.lower())
            if key not in seen_variants and not any(v.value.lower() == size.lower() or size.lower() in v.value.lower() for v in variants):
                seen_variants.add(key)
                variants.append(VariantIn(variant_type="size", value=size, attributes={"size": size}))

    # 4. Process images
    seen_images = set()
    images: list[ImageIn] = []

    for idx, img in enumerate(raw_images):
        url = ""
        is_primary = idx == 0
        if isinstance(img, str):
            url = img.strip()
        elif isinstance(img, dict):
            url = str(img.get("url") or img.get("image_url") or "").strip()
            is_primary = bool(img.get("is_primary", idx == 0))
        elif hasattr(img, "url"):
            url = str(getattr(img, "url", "")).strip()
            is_primary = bool(getattr(img, "is_primary", idx == 0))

        # Only real links are images; anything else the extraction picked up
        # (a file name, a caption) isn't something Instagram can send.
        if url.startswith(("https://", "http://")) and url not in seen_images:
            seen_images.add(url)
            images.append(ImageIn(url=url, is_primary=is_primary))

    # Also check attributes["image_url"]
    attrs = dict(raw_attrs) if isinstance(raw_attrs, dict) else {}
    attr_img = attrs.get("image_url")
    if (
        attr_img
        and isinstance(attr_img, str)
        and attr_img.strip().startswith(("https://", "http://"))
        and attr_img.strip() not in seen_images
    ):
        url = attr_img.strip()
        seen_images.add(url)
        images.append(ImageIn(url=url, is_primary=(len(images) == 0)))

    # Synchronize category and primary image_url into attributes
    if raw_cat and "category" not in attrs:
        attrs["category"] = str(raw_cat).strip()
    if images and "image_url" not in attrs:
        primary_img = next((img for img in images if img.is_primary), images[0])
        attrs["image_url"] = primary_img.url

    return ProductCreate(
        name=raw_name,
        description=str(raw_desc).strip() if raw_desc else None,
        price=price_val,
        currency=currency_val,
        availability=bool(raw_avail),
        attributes=attrs,
        variants=variants,
        images=images,
    )


def normalize_extracted_list(raw: RawExtractedProductList | list[Any]) -> list[ProductCreate]:
    items = raw.products if isinstance(raw, RawExtractedProductList) else raw
    return [normalize_extracted_product(p) for p in items]
