"""Embedding provider lookup.

Returns None rather than raising when embeddings aren't configured. Semantic
search is an enhancement on top of the lexical tiers: a business without an
embedding key still gets full-text and trigram search, and product writes still
succeed. Callers branch on None; nothing here is allowed to break a search.
"""
import logging
from functools import lru_cache

from app.ai.embeddings.base import EmbeddingError, EmbeddingProvider
from app.core.config import get_settings

logger = logging.getLogger("app.ai.embeddings")


@lru_cache
def get_embedding_provider() -> EmbeddingProvider | None:
    settings = get_settings()
    if not settings.embedding_enabled or not settings.embedding_api_key:
        return None

    if settings.embedding_provider == "openai":
        from app.ai.embeddings.openai import OpenAIEmbeddingProvider

        try:
            return OpenAIEmbeddingProvider()
        except EmbeddingError as exc:
            logger.warning("[embeddings] disabled: %s", exc)
            return None

    logger.warning("[embeddings] unknown EMBEDDING_PROVIDER %r — disabled", settings.embedding_provider)
    return None
