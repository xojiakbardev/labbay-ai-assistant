"""What of a product actually gets embedded, and how we know it's stale.

Not just the name. A customer asking for "qishga issiq narsa" is describing a
season and a property, not a product name, so the vector has to carry the
category and the attributes the AI import extracts (material, fit, season,
gender, brand) or there's nothing for that query to match. Variant values go in
too, so "43 raqamli qora" has something to land on.

The hash exists so re-embedding is driven by the text actually changing rather
than by any write: editing a product's stock count shouldn't cost an API call,
and a model change should re-embed everything.
"""
import hashlib
from app import prompts

# Attributes worth embedding. Deliberately a list rather than "everything in
# attributes" — internal keys like ai_instructions and image_url would add
# noise to the vector without helping any customer's query.
_EMBEDDED_ATTRIBUTES = tuple(prompts.lexicon()["embedded_attributes"])


def build_embedding_text(product) -> str:
    """The document text for one product. Stable ordering — the hash depends on it."""
    parts: list[str] = [str(product.name or "").strip()]

    description = (getattr(product, "description", None) or "").strip()
    if description:
        parts.append(description)

    attributes = getattr(product, "attributes", None) or {}
    for key in _EMBEDDED_ATTRIBUTES:
        value = attributes.get(key)
        if value:
            parts.append(f"{key}: {value}")

    # Variant values are what a customer names when they ask for "qora, 42".
    variants = getattr(product, "variants", None) or []
    values = []
    for variant in variants:
        value = str(getattr(variant, "value", "") or "").strip()
        if value and value not in values:
            values.append(value)
    if values:
        parts.append(", ".join(values))

    return "\n".join(p for p in parts if p)


def embedding_hash(text: str, model: str, dimensions: int) -> str:
    """Identifies the exact (text, model, width) an embedding was made from.

    Model and dimensions are in the hash on purpose: vectors from two different
    models don't live in the same space, so comparing them is meaningless, and
    a config change has to invalidate every stored vector.
    """
    digest = hashlib.sha256()
    digest.update(text.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(f"{model}:{dimensions}".encode("utf-8"))
    return digest.hexdigest()


def needs_reembedding(product, text: str, model: str, dimensions: int) -> bool:
    """Decided from the hash alone — the vector column is deferred (never
    loaded with the product), and the hash is written together with it, so a
    missing hash is a missing vector."""
    return getattr(product, "embedding_hash", None) != embedding_hash(text, model, dimensions)
