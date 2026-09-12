"""LLMProvider abstraction (plan §8/§21).

Capabilities, all behind the same interface so the model/vendor can be
swapped without touching callers:
- generate_structured: one-shot call constrained to a JSON schema (Phase 5 ingestion).
- run_agentic_turn: a bounded tool-calling loop that ends in one structured
  final answer (Phase 7 conversation engine + Phase 8 fused qualification).
- run_sales_turn: the conversation engine's actual entry point — the same tool
  loop, but the customer-facing reply and the lead analysis come back as two
  separately-generated values. Has a default implementation on top of
  run_agentic_turn, so only the two above are abstract.
"""
import asyncio
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# (tool_name, arguments) -> JSON-serializable result handed back to the model.
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

# Called with the reply as soon as it's written, before the analysis pass.
# Returns the text to treat as final — the hook may rewrite it (safety guards
# run here) as well as deliver it.
ReplyHook = Callable[[str], Awaitable[str]]


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

    async def run_sales_turn(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        fused_schema: type[BaseModel],
        analysis_schema: type[T],
        analyst_system_prompt: str,
        on_reply: ReplyHook | None = None,
        max_tool_calls: int = 4,
        business_id: uuid.UUID | None = None,
    ) -> tuple[str, T]:
        """One conversation turn, returned as (customer-facing reply, analysis).

        The reply and the analysis are deliberately separate return values
        because they're best generated separately: constrained JSON decoding
        measurably flattens prose, and a schema that puts `reply` before the
        model has reasoned about the sale makes it commit to wording before it
        has decided what the sales move even is. Providers that can run two
        passes (write freely first, analyse the written reply second) should
        override this.

        `on_reply`, when given, is awaited with the reply the moment it exists
        and before the analysis runs, and whatever it returns becomes the final
        reply. That's the hook the conversation engine uses to deliver the
        message to the customer while the bookkeeping is still being worked out
        — the analysis is worth roughly a second of somebody's attention, and on
        Instagram that second is the whole difference between a shop that
        answers instantly and one that doesn't.

        The default here is the old single-pass behaviour — one fused
        `run_agentic_turn` call against `fused_schema`, split afterwards — so
        any provider implementing only the two abstract methods above still
        works unchanged. It gains nothing in latency (there's only one call to
        wait for), but `on_reply` still fires, so callers behave identically
        whichever provider they're given.
        """
        from app.core.config import get_settings

        try:
            async with asyncio.timeout(get_settings().llm_turn_deadline_seconds):
                fused = await self.run_agentic_turn(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                    response_schema=fused_schema,
                    tool_executor=tool_executor,
                    max_tool_calls=max_tool_calls,
                    business_id=business_id,
                )
        except TimeoutError as exc:
            raise LLMProviderError("Turn deadline exceeded") from exc
        reply = (getattr(fused, "reply", "") or "").strip()
        if not reply:
            raise LLMProviderError("Provider returned an empty reply")
        if on_reply is not None:
            reply = await on_reply(reply)
        analysis = analysis_schema.model_validate(fused.model_dump(exclude={"reply"}))
        return reply, analysis
