"""OpenAI embeddings.

OpenRouter, which serves every chat call in this system, has no embeddings
endpoint — so this talks to OpenAI directly, over httpx, in the same style as
the OpenRouter provider rather than pulling in another SDK.

Model choice matters more than usual here. The whole point of semantic search in
this product is that a customer can write "qishga issiq narsa kerak" in Uzbek,
in either alphabet, and be understood. `text-embedding-3-large` is the
multilingual tier; the smaller model is noticeably weaker on low-resource
languages, which is exactly the case that has to work.

Dimensions are shortened from the model's native 3072 to 1536 (the model is
trained so that truncating stays coherent). That is not a cost decision: pgvector
cannot build an HNSW index above 2000 dimensions, and an unindexed catalog scan
is worse than the small quality difference.
"""
import asyncio
import logging
import random

import httpx

from app.ai.embeddings.base import EmbeddingError, EmbeddingProvider
from app.ai.usage_context import billed_business
from app.core.config import get_settings

# OpenAI list prices, for the superadmin cost figures (the embeddings API
# returns token counts, not cost). Unknown models are logged at $0.
_USD_PER_MILLION_TOKENS = get_settings().embedding_usd_per_million_tokens

logger = logging.getLogger("app.ai.embeddings.openai")

_OPENAI_EMBEDDINGS_URL = get_settings().embedding_api_url
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# The API accepts far more, but a batch that fails costs a retry of everything
# in it, and catalogs here are small enough that this is never the bottleneck.
MAX_BATCH = get_settings().embedding_max_batch


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.embedding_api_key:
            raise EmbeddingError("EMBEDDING_API_KEY is not set.")
        self._api_key = settings.embedding_api_key
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions
        self._timeout = settings.embedding_request_timeout_seconds
        self._max_retries = 2

    async def _post(self, client: httpx.AsyncClient, payload: dict, max_retries: int | None = None) -> dict:
        last_error: Exception | None = None
        retries = self._max_retries if max_retries is None else max_retries
        for attempt in range(retries + 1):
            try:
                response = await client.post(
                    _OPENAI_EMBEDDINGS_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                if response.status_code in _RETRYABLE_STATUS_CODES and attempt < retries:
                    delay = 0.4 * (2**attempt) + random.uniform(0.05, 0.15)
                    logger.warning(
                        "[embeddings] HTTP %s, retrying in %.2fs", response.status_code, delay
                    )
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(0.4 * (2**attempt))
                    continue
                raise EmbeddingError(f"embedding request failed: {exc}") from exc
            except httpx.HTTPStatusError as exc:
                raise EmbeddingError(
                    f"embedding HTTP {exc.response.status_code}: {exc.response.text[:200]}"
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise EmbeddingError(f"embedding call failed: {exc}") from exc
        raise EmbeddingError(f"embedding retries exhausted: {last_error}")

    def _payload(self, inputs: list[str]) -> dict:
        return {"model": self.model, "input": inputs, "dimensions": self.dimensions}

    @staticmethod
    def _vectors(data: dict, expected: int) -> list[list[float]]:
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != expected:
            raise EmbeddingError(f"expected {expected} embeddings, got {len(items or [])}")
        if not all(isinstance(item, dict) and isinstance(item.get("embedding"), list) for item in items):
            raise EmbeddingError("embedding response is malformed")
        # The API documents index order but doesn't guarantee it in transit.
        ordered = sorted(items, key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in ordered]

    async def _log(self, data: dict) -> None:
        business_id = billed_business()
        tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
        if business_id is None or not tokens:
            return
        from app.ai.provider.openrouter import log_usage

        price = _USD_PER_MILLION_TOKENS.get(self.model, 0.0)
        await log_usage(
            business_id,
            "embedding",
            self.model,
            {"prompt_tokens": tokens, "total_tokens": tokens, "cost": tokens * price / 1_000_000},
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        cleaned = [t.strip() or " " for t in texts]
        if not cleaned:
            return []
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for start in range(0, len(cleaned), MAX_BATCH):
                batch = cleaned[start : start + MAX_BATCH]
                data = await self._post(client, self._payload(batch))
                await self._log(data)
                vectors.extend(self._vectors(data, len(batch)))
        return vectors

    async def embed_query(self, text: str, timeout: float | None = None) -> list[float]:
        cleaned = (text or "").strip()
        if not cleaned:
            raise EmbeddingError("cannot embed an empty query")
        async with httpx.AsyncClient(timeout=timeout or self._timeout) as client:
            data = await self._post(client, self._payload([cleaned]), max_retries=0 if timeout else None)
        await self._log(data)
        return self._vectors(data, 1)[0]
