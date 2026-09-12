"""Embedding provider abstraction — same shape as LLMProvider, same reason.

Kept behind an interface so the vendor can be swapped without touching the
retrieval code, and so a business running without embeddings configured
degrades to lexical search instead of failing.
"""
from abc import ABC, abstractmethod


class EmbeddingError(Exception):
    """Any embedding failure. Callers must treat embeddings as an enhancement
    that can be absent — never let this break a search or a product write."""


class EmbeddingProvider(ABC):
    #: Vector width. Must match the `products.embedding` column, or nothing
    #: written with one model can be compared against the other.
    dimensions: int

    #: Identifies which model produced a stored vector, so a model change is
    #: detectable and the catalog can be re-embedded rather than silently
    #: compared across incompatible vector spaces.
    model: str

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed catalog text. Batched — this runs over whole catalogs."""
        raise NotImplementedError

    @abstractmethod
    async def embed_query(self, text: str, timeout: float | None = None) -> list[float]:
        """Embed one customer query. Latency matters here: it sits inside a
        tool call the customer is waiting on, so a `timeout` means one attempt
        within that budget and no retries."""
        raise NotImplementedError
