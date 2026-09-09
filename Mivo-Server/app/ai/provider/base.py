"""LLMProvider abstraction (plan §8/§21).

Two capabilities, both behind the same interface so the model/vendor can be
swapped without touching callers:
- generate_structured: one-shot call constrained to a JSON schema (Phase 5 ingestion).
- run_agentic_turn: a bounded tool-calling loop that ends in one structured
  final answer (Phase 7 conversation engine + Phase 8 fused qualification).
"""
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# (tool_name, arguments) -> JSON-serializable result handed back to the model.
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class LLMProviderError(Exception):
    """Raised for any provider failure (timeout, HTTP error, invalid structured
    output) — callers map this to a safe fallback, never let it crash a request."""


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the tool's arguments


class LLMProvider(ABC):
    @abstractmethod
    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_schema: type[T],
        business_id: uuid.UUID | None = None,
    ) -> T:
        """One-shot call constrained to return JSON matching response_schema.
        `business_id`, if given, tags the call for the superadmin usage/cost
        dashboard (app/superadmin) — omit it for calls that aren't
        business-scoped."""
        raise NotImplementedError

    @abstractmethod
    async def run_agentic_turn(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[ToolDefinition],
        response_schema: type[T],
        tool_executor: ToolExecutor,
        max_tool_calls: int = 4,
        business_id: uuid.UUID | None = None,
    ) -> T:
        """Runs a bounded tool-calling loop (the model may call `tools`, each
        executed via `tool_executor`), then makes one final call constrained to
        `response_schema` for the fused reply+qualification output. Retrieval
        tools only — the model never gets a persistence tool (plan §9/§10)."""
        raise NotImplementedError
