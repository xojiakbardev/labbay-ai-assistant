"""Provider factory — swap the LLM vendor by changing LLM_PROVIDER, no caller
changes needed (plan §21/critical decisions: LLM provider abstraction)."""
from functools import lru_cache

from app.ai.provider.base import LLMProvider
from app.ai.provider.openrouter import OpenRouterProvider
from app.core.config import get_settings


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "openrouter":
        return OpenRouterProvider()
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider!r}")
