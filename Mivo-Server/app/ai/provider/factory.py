"""Provider factory — swap the LLM vendor by changing LLM_PROVIDER, no caller
changes needed (plan §21/critical decisions: LLM provider abstraction)."""
from functools import lru_cache

from app.ai.provider.base import LLMProvider, LLMProviderError
from app.ai.provider.openrouter import OpenRouterProvider
from app.core.config import get_settings


@lru_cache
def get_llm_provider() -> LLMProvider:
    """Raises LLMProviderError when the provider isn't configured. The
    Instagram pipeline resolves this only when a turn runs, and hands the
    conversation to a human on that error — the customer's message is recorded
    either way."""
    settings = get_settings()
    if settings.llm_provider == "openrouter":
        return OpenRouterProvider()
    raise LLMProviderError(f"Unsupported LLM_PROVIDER: {settings.llm_provider!r}")
