"""Keeping product embeddings in step with the catalog.

Called from the product write path, so a product edited in the dashboard is
searchable by meaning immediately rather than after some nightly job.

Every function here is best-effort by design. An embedding is an enhancement on
top of lexical search, and the API behind it is a third party that can be slow
or down — so a failure to embed must never fail the write that triggered it. The
product saves, `embedding` stays null, the product is still findable by keyword,
and the next write (or `backfill_embeddings.py`) picks it up.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings.base import EmbeddingError, EmbeddingProvider
from app.ai.embeddings.factory import get_embedding_provider
from app.products.embedding_text import build_embedding_text, embedding_hash, needs_reembedding
from app.products.models import EMBEDDING_DIMENSIONS, Product

logger = logging.getLogger("app.products.embeddings")


async def refresh_product_embedding(
    db: AsyncSession, product: Product, *, provider: EmbeddingProvider | None = None
) -> bool:
    """Re-embed one product if its text, model or width changed.

    Returns True if a new vector was stored. Skips silently when embeddings
    aren't configured, and swallows API failures — see the module docstring.
    """
    provider = provider or get_embedding_provider()
    if provider is None:
        return False

    text = build_embedding_text(product)
    if not text.strip():
        return False
    if not needs_reembedding(product, text, provider.model, provider.dimensions):
        # Editing a stock count shouldn't cost an API call.
        return False

    try:
        vectors = await provider.embed_documents([text])
    except EmbeddingError as exc:
        logger.warning("[embeddings] product %s not embedded: %s", product.id, exc)
        return False

    if len(vectors) != 1 or len(vectors[0]) != EMBEDDING_DIMENSIONS:
        logger.error(
            "[embeddings] product %s: provider returned a vector of the wrong shape (column is %d-d)",
            product.id, EMBEDDING_DIMENSIONS,
        )
        return False

    product.embedding = vectors[0]
    product.embedding_hash = embedding_hash(text, provider.model, provider.dimensions)
    product.embedding_model = provider.model
    await db.flush()
    return True


async def refresh_many(
    db: AsyncSession, products: list[Product], *, provider: EmbeddingProvider | None = None
) -> int:
    """Batch version for imports and backfills — one API round trip per batch
    instead of per product. Returns how many were re-embedded.

    A batch that fails is skipped whole rather than retried per product: the
    provider already retries internally, and a backfill that grinds through a
    dead API one product at a time helps nobody.
    """
    provider = provider or get_embedding_provider()
    if provider is None or not products:
        return 0

    pending: list[tuple[Product, str]] = []
    for product in products:
        text = build_embedding_text(product)
        if text.strip() and needs_reembedding(product, text, provider.model, provider.dimensions):
            pending.append((product, text))

    if not pending:
        return 0

    try:
        vectors = await provider.embed_documents([text for _, text in pending])
    except EmbeddingError as exc:
        logger.warning("[embeddings] batch of %d not embedded: %s", len(pending), exc)
        return 0

    if len(vectors) != len(pending) or any(len(v) != EMBEDDING_DIMENSIONS for v in vectors):
        logger.error(
            "[embeddings] provider returned %d vectors for %d inputs (column is %d-d) — batch skipped",
            len(vectors),
            len(pending),
            EMBEDDING_DIMENSIONS,
        )
        return 0

    for (product, text), vector in zip(pending, vectors):
        product.embedding = vector
        product.embedding_hash = embedding_hash(text, provider.model, provider.dimensions)
        product.embedding_model = provider.model

    await db.flush()
    return len(pending)
