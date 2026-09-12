"""OpenRouter implementation of LLMProvider: bounded retries, a multi-model
failover chain, a per-turn deadline, and per-call usage accounting.
"""
import asyncio
import copy
import json
import logging
import os
import random
import re
import time
import uuid
from typing import Any

import httpx

from app.ai.provider.base import (
    LLMProvider,
    LLMProviderError,
    ReplyHook,
    T,
    ToolDefinition,
    ToolExecutor,
)
from app.ai.context.builder import MEDIA_NOTE_PREFIX
from app.core.config import get_settings
from app import prompts

logger = logging.getLogger("app.ai.provider.openrouter")

_OPENROUTER_URL = get_settings().llm_api_url

# Fallback models in priority order if the primary model is unavailable or encounters errors
_DEFAULT_FALLBACK_MODELS = list(get_settings().llm_fallback_models)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
# Only failures where the request provably never reached the model are retried
# on the same model. A read timeout means the model may have run (and billed);
# retrying it doubles the cost and still may not answer in time.
_RETRYABLE_TRANSPORT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

# Tool calls executed per round. Beyond this the model is told to narrow down.
_MAX_TOOL_CALLS_PER_ROUND = get_settings().llm_max_tool_calls_per_round


async def log_usage(business_id: uuid.UUID, kind: str, model: str, usage: dict) -> None:
    """Persists one AiUsageLog row for one billed call — right after the call,
    so calls billed before a later failure in the same turn are still counted.

    Runs on its own short-lived session. A failure to write is logged at error
    level and swallowed: it's accounting, and by the time it runs the call has
    already happened — failing the customer's turn would not un-spend it.
    """
    if not usage:
        return
    try:
        from app.ai.models import AiUsageLog
        from app.core.db import async_session_factory

        async with async_session_factory() as db:
            db.add(
                AiUsageLog(
                    business_id=business_id,
                    kind=kind[:20],
                    model=model[:100],
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                    cost_usd=float(usage.get("cost") or 0),
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — see docstring
        logger.exception("[ai_usage_log] failed to persist usage for business %s", business_id)


def _tool_to_openai_shape(tool: ToolDefinition) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _clean_and_parse_json(content: str | None, response_schema: type[T]) -> T:
    """Extracts and validates structured output. Tolerates formatting noise
    around the JSON (code fences, a sentence before/after it, trailing commas)
    — the content itself is still validated strictly against the schema, so
    none of this can let a wrong value through."""
    if not content or not content.strip():
        raise ValueError(f"Empty content returned by model for schema {response_schema.__name__}")

    raw = content.strip()
    if "```" in raw:
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
        if fence_match:
            raw = fence_match.group(1).strip()
        else:
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw).strip()

    candidates = [raw]
    brace_start, brace_end = raw.find("{"), raw.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        inner = raw[brace_start : brace_end + 1]
        candidates += [inner, re.sub(r",\s*([\]\}])", r"\1", inner)]

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return response_schema.model_validate(json.loads(candidate))
        except (ValueError, TypeError) as exc:
            last_error = exc
    raise ValueError(f"Could not parse valid {response_schema.__name__} from output: {content[:200]} ({last_error})")


def _message_of(data: dict) -> dict:
    """The assistant message of a completion, or LLMProviderError — a 200 with
    an error body, no choices, or a truncated/filtered generation is a failed
    call, not an empty answer."""
    if not isinstance(data, dict):
        raise LLMProviderError("OpenRouter returned a non-object response")
    if data.get("error"):
        raise LLMProviderError(f"OpenRouter returned an error body: {str(data['error'])[:200]}")
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise LLMProviderError("OpenRouter returned no choices")
    choice = choices[0]
    finish_reason = choice.get("finish_reason") or choice.get("native_finish_reason")
    if finish_reason in ("length", "content_filter", "error"):
        raise LLMProviderError(f"Generation stopped early (finish_reason={finish_reason})")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise LLMProviderError("OpenRouter choice has no message")
    return message


def _parse_structured(data: dict, response_schema: type[T]) -> T:
    message = _message_of(data)
    try:
        return _clean_and_parse_json(message.get("content"), response_schema)
    except ValueError as exc:
        raise LLMProviderError(f"OpenRouter returned invalid structured output: {exc}") from exc


def _strict_schema(schema: dict) -> tuple[dict, bool]:
    """Makes a Pydantic JSON schema acceptable to strict structured-output
    implementations (OpenAI's in particular, used by the fallback chain):
    every object closed with additionalProperties=false and every property
    listed as required (optional ones are already nullable), no `default`.

    Returns (schema, strict_ok). A free-form object (a dict field) can't be
    expressed in strict mode at all, and a strict request carrying one is
    rejected outright — such a schema is sent non-strict instead, still
    validated against the model on our side."""
    schema = copy.deepcopy(schema)
    strict_ok = True

    def visit(node: Any) -> None:
        nonlocal strict_ok
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                if "properties" in node:
                    node["additionalProperties"] = False
                    node["required"] = list(node["properties"].keys())
                else:
                    strict_ok = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(schema)
    return schema, strict_ok


def _json_schema_format(response_schema: type[T]) -> dict:
    schema, strict_ok = _strict_schema(response_schema.model_json_schema())
    return {
        "type": "json_schema",
        "json_schema": {"name": response_schema.__name__, "strict": strict_ok, "schema": schema},
    }


_PRODUCT_HINTS = set(prompts.lexicon()["product_hints"])

# Messages that are conversation, not a product-fact request. Forcing a catalog
# search on these is what made the AI answer "that's too expensive" with a
# product dump instead of handling the objection.
_NO_GROUNDING_NEEDED = set(prompts.lexicon()["no_grounding_needed"])

_PHONE_ONLY_RE = re.compile(r"^[\d\s\-+()./]{7,25}$")
_NOTE_QUOTE_RE = re.compile(r'(?:says|wrote|with it): "(.+)"')

# --- Writer pass -----------------------------------------------------------
#
# The writer thinks for one line before it writes, because deciding the sales
# move and phrasing it are different jobs and doing them in that order is
# measurably better than doing them at once. The plan is internal, so it's
# fenced off by a marker — and because whatever comes after it is sent verbatim
# to a real customer over Instagram, the parser only accepts output that has
# the marker and a message after it. Anything else is not sent.
_WRITER_MESSAGE_MARKER = "===MESSAGE==="

_WRITER_INSTRUCTION = prompts.render("writer.md", marker=_WRITER_MESSAGE_MARKER)

_WRITER_FORMAT_REMINDER = (
    prompts.render("writer_reminder.md", marker=_WRITER_MESSAGE_MARKER)
)

_ANALYST_INSTRUCTION = (
    prompts.load("analyst_turn.md")
)

_PLAN_LINE_RE = re.compile(r"^\s*[*_#>\s]*(plan|reja|план|next step|keyingi qadam)\s*[*_]*\s*[:\-—]", re.IGNORECASE)
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def _extract_customer_message(raw: str) -> str:
    """The customer-facing half of the writer's PLAN/marker/message output.

    Returns "" unless the output has the marker with a message after it — a
    missing marker, an empty message, or anything that still looks like a
    plan line means we can't tell internal notes from the reply, and the
    caller then asks once more or hands off. Sending the model's note about
    the customer *to* the customer is the one outcome this must never allow.
    """
    if not raw or _WRITER_MESSAGE_MARKER not in raw:
        return ""
    text = raw.rsplit(_WRITER_MESSAGE_MARKER, 1)[1]
    text = _FENCE_RE.sub("", text.strip()).strip()
    # Only the first line: that's where a plan repeated after the marker
    # lands. A later line starting "Reja:" is the message's own content.
    if not text or _PLAN_LINE_RE.match(text.splitlines()[0]):
        return ""
    return text


def _needs_product_grounding(messages: list[dict[str, Any]]) -> bool:
    """Whether this turn should be forced to call a retrieval tool first.

    Grounding exists to stop the model inventing prices and stock, so anything
    that looks like a product question forces a tool call. But a customer
    saying "qimmat ekan" or "rahmat, o'ylab ko'raman" isn't asking for product
    facts at all — forcing a search there produced a catalog dump in place of
    an actual reply to what they said.

    Looks at every customer message since the last assistant reply, not only
    the newest: a debounced burst of "Air Max narxi qancha" + "rahmat" is still
    a price question.
    """
    burst: list[dict[str, Any]] = []
    for m in reversed(messages):
        if m.get("role") == "user":
            burst.append(m)
        elif burst:
            break
    if not burst:
        return True
    return any(_message_needs_grounding(m) for m in burst)


def _message_needs_grounding(m: dict[str, Any]) -> bool:
    content_val = m.get("content")
    if isinstance(content_val, list):
        return True  # multimodal (image) — always look for a match in the catalog

    text = str(content_val or "").strip().lower()
    if not text:
        return True
    if text.startswith(MEDIA_NOTE_PREFIX.lower()):
        # Something the model can't see (a shared Reel, a template...). Only
        # its caption, or what the customer wrote with it, can be searched
        # for; without one, a forced search is what made the model "find" a
        # product in a video it never saw.
        quoted = _NOTE_QUOTE_RE.search(text)
        return bool(quoted) and _message_needs_grounding({"content": quoted.group(1)})
    if any(hint in text for hint in _PRODUCT_HINTS):
        return True
    # A bare phone number is the customer answering "leave your number".
    if _PHONE_ONLY_RE.match(text):
        return False
    words = re.findall(r"[\w']+", text)
    if not words:
        return False
    # A short message carrying a conversational signal is answered, not
    # searched. Anything longer or with no such signal is grounded — the cost
    # of an unnecessary search is a slower turn, the cost of a missing one is
    # an invented price.
    if len(words) <= 8 and any(w in _NO_GROUNDING_NEEDED for w in words):
        return False
    return True


class _Deadline:
    """The time budget of one turn, shared by every call in it."""

    def __init__(self, seconds: float) -> None:
        self._at = time.monotonic() + seconds

    def remaining(self) -> float:
        return self._at - time.monotonic()

    def check(self) -> None:
        if self.remaining() <= 0:
            raise LLMProviderError("Turn deadline exceeded")


class OpenRouterProvider(LLMProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise LLMProviderError("OPENROUTER_API_KEY is not set.")
        self._api_key = settings.openrouter_api_key
        self._model = settings.openrouter_model
        self._fallback_models = [m for m in _DEFAULT_FALLBACK_MODELS if m != self._model]
        self._timeout = settings.llm_request_timeout_seconds
        self._turn_deadline = settings.llm_turn_deadline_seconds
        self._retry_after_cap = settings.llm_retry_after_cap_seconds

        # Writing the customer's reply and scoring the lead are different jobs
        # with opposite sampling needs — see the note in app/core/config.py.
        self._writer_model = settings.llm_writer_model or self._model
        self._analyst_model = settings.llm_analyst_model or self._model
        self._writer_temperature = settings.llm_writer_temperature
        self._analyst_temperature = settings.llm_analyst_temperature

        # In testing environments, use fast backoff to keep test runs instant
        self._is_testing = "PYTEST_CURRENT_TEST" in os.environ
        self._max_retries = 2

    def _backoff(self, attempt: int, retry_after: str | None = None) -> float:
        if self._is_testing:
            return 0.001
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), self._retry_after_cap)
        return min(0.4 * (2**attempt) + random.uniform(0.05, 0.15), self._retry_after_cap)

    async def _call_single_model(
        self,
        client: httpx.AsyncClient,
        payload: dict,
        model_name: str,
        deadline: _Deadline | None = None,
    ) -> dict:
        """Calls OpenRouter with one model, retrying only what's safe to retry
        (429/5xx, connection never established), within the turn deadline."""
        body = dict(payload, model=model_name, usage={"include": True})
        last_error = ""
        for attempt in range(self._max_retries + 1):
            if deadline is not None:
                deadline.check()
            timeout = self._timeout if deadline is None else max(1.0, min(self._timeout, deadline.remaining()))
            try:
                # httpx's timeout bounds each read, not the request: a response
                # trickling in slower than that but never stalling would run
                # past the turn deadline. This bounds the whole call.
                async with asyncio.timeout(timeout):
                    response = await client.post(
                        _OPENROUTER_URL,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "HTTP-Referer": "https://mivo.uz",
                            "X-Title": "Mivo AI",
                        },
                        json=body,
                        timeout=timeout,
                    )
            except TimeoutError as exc:
                raise LLMProviderError(f"OpenRouter call to {model_name} exceeded {timeout:.0f}s") from exc
            except _RETRYABLE_TRANSPORT_ERRORS as exc:
                last_error = f"{type(exc).__name__}"
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                raise LLMProviderError(f"OpenRouter unreachable for {model_name}: {last_error}") from exc
            except httpx.HTTPError as exc:
                raise LLMProviderError(f"OpenRouter request failed for {model_name}: {type(exc).__name__}") from exc

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                delay = self._backoff(attempt, response.headers.get("Retry-After"))
                logger.warning(
                    "[openrouter] %s returned HTTP %s, retrying in %.2fs (attempt %d/%d)",
                    model_name, response.status_code, delay, attempt + 1, self._max_retries,
                )
                await asyncio.sleep(delay)
                continue
            if response.status_code >= 400:
                raise LLMProviderError(
                    f"OpenRouter HTTP error {response.status_code} for {model_name}: {response.text[:200]}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise LLMProviderError(f"OpenRouter returned non-JSON for {model_name}") from exc
        raise LLMProviderError(f"OpenRouter retries exhausted for {model_name}: {last_error}")

    async def _call_with_fallback(
        self,
        client: httpx.AsyncClient,
        payload: dict,
        primary_model: str | None = None,
        *,
        deadline: _Deadline | None = None,
        business_id: uuid.UUID | None = None,
        kind: str = "conversation",
    ) -> tuple[dict, str]:
        """Primary model, then the fallback chain. A response whose message is
        unusable (error body, no choices, truncated) counts as that model
        failing. Each billed call is logged as it completes."""
        primary = primary_model or self._model
        models_to_try = [primary] + [m for m in self._fallback_models if m != primary]
        errors = []
        for model_name in models_to_try:
            try:
                data = await self._call_single_model(client, payload, model_name, deadline)
            except LLMProviderError as exc:
                logger.warning("[openrouter] %s failed: %s", model_name, exc)
                errors.append(f"{model_name}: {exc}")
                continue
            if business_id is not None:
                await log_usage(business_id, kind, model_name, data.get("usage") or {})
            try:
                _message_of(data)
            except LLMProviderError as exc:
                logger.warning("[openrouter] %s gave an unusable response: %s", model_name, exc)
                errors.append(f"{model_name}: {exc}")
                continue
            return data, model_name
        raise LLMProviderError(f"All LLM models failed: {'; '.join(errors)}")

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_schema: type[T],
        business_id: uuid.UUID | None = None,
    ) -> T:
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": _json_schema_format(response_schema),
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            data, _model_used = await self._call_with_fallback(
                client, payload, business_id=business_id, kind="extraction"
            )
        return _parse_structured(data, response_schema)

    async def _run_tool_loop(
        self,
        client: httpx.AsyncClient,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        max_tool_calls: int,
        deadline: _Deadline,
        business_id: uuid.UUID | None,
    ) -> list[dict[str, Any]]:
        """Runs the grounding loop and returns the conversation: the system
        prompt, the chat history, and every assistant/tool exchange the loop
        produced — the grounded facts the writer and analyst need in view."""
        conversation: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *messages]
        openai_tools = [_tool_to_openai_shape(t) for t in tools]
        needs_grounding = _needs_product_grounding(messages)

        for i in range(max_tool_calls):
            payload = {
                "messages": conversation,
                "tools": openai_tools,
                # Force one grounding call up front only when the customer
                # actually asked something a tool can answer.
                "tool_choice": "required" if (i == 0 and needs_grounding) else "auto",
            }
            data, _model = await self._call_with_fallback(
                client, payload, deadline=deadline, business_id=business_id
            )
            message = _message_of(data)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                break

            conversation.append({"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls})
            for index, call in enumerate(tool_calls):
                call_id = call.get("id") or f"call_{i}_{index}"
                function = call.get("function") or {}
                name = function.get("name") or ""
                if index >= _MAX_TOOL_CALLS_PER_ROUND:
                    result: dict = {
                        "error": f"Only {_MAX_TOOL_CALLS_PER_ROUND} tool calls are run per step; "
                        "this one was skipped. Narrow down and call again if still needed."
                    }
                else:
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                        if not isinstance(arguments, dict):
                            raise ValueError("arguments must be a JSON object")
                    except ValueError as exc:
                        # Told to the model so it re-calls correctly — never
                        # "repaired" by guessing what it meant.
                        logger.warning("[openrouter] malformed tool arguments for %s: %s", name, exc)
                        result = {"error": f"Invalid JSON arguments: {exc}. Re-call with a valid JSON object."}
                    else:
                        result = await tool_executor(name, arguments)
                conversation.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, default=str, ensure_ascii=False, separators=(",", ":"))})

        return conversation

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
        deadline = _Deadline(self._turn_deadline)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            conversation = await self._run_tool_loop(
                client,
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
                max_tool_calls=max_tool_calls,
                deadline=deadline,
                business_id=business_id,
            )
            final_payload = {
                "messages": [
                    *conversation,
                    {
                        "role": "user",
                        "content": "Using everything above, produce your final response now as JSON matching the required schema.",
                    },
                ],
                "tools": [_tool_to_openai_shape(t) for t in tools],
                "tool_choice": "none",
                "response_format": _json_schema_format(response_schema),
                "temperature": self._analyst_temperature,
            }
            final_data, _model = await self._call_with_fallback(
                client, final_payload, deadline=deadline, business_id=business_id
            )
        return _parse_structured(final_data, response_schema)

    async def _write_reply(
        self,
        client: httpx.AsyncClient,
        conversation: list[dict[str, Any]],
        tools: list[ToolDefinition],
        deadline: _Deadline,
        business_id: uuid.UUID | None,
    ) -> str:
        """The writer pass. Output without the marker gets one reminder and a
        second attempt; still unusable is a provider error (-> handoff)."""
        writer_messages = [*conversation, {"role": "system", "content": _WRITER_INSTRUCTION}]
        raw = ""
        for attempt in range(2):
            payload = {
                "messages": writer_messages,
                # The conversation carries tool-call history, which several
                # providers reject without the tool definitions present.
                "tools": [_tool_to_openai_shape(t) for t in tools],
                "tool_choice": "none",
                "temperature": self._writer_temperature,
            }
            data, _model = await self._call_with_fallback(
                client, payload, primary_model=self._writer_model, deadline=deadline, business_id=business_id
            )
            raw = _message_of(data).get("content") or ""
            reply = _extract_customer_message(raw)
            if reply:
                return reply
            logger.warning("[openrouter] writer output unusable (attempt %d): %r", attempt + 1, raw[:200])
            writer_messages = [
                *writer_messages,
                {"role": "assistant", "content": raw},
                {"role": "user", "content": _WRITER_FORMAT_REMINDER},
            ]
        raise LLMProviderError(f"Writer pass returned no customer-facing message (raw: {raw[:200]!r})")

    async def run_sales_turn(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        fused_schema: type[Any],
        analysis_schema: type[T],
        analyst_system_prompt: str,
        on_reply: ReplyHook | None = None,
        max_tool_calls: int = 4,
        business_id: uuid.UUID | None = None,
    ) -> tuple[str, T]:
        """Grounding loop, then two separate passes: write, then analyse.

        `fused_schema` goes unused here — it exists for the single-pass default
        in LLMProvider. The writer runs unconstrained and warm so the reply
        reads like a person typing; the analyst runs schema-constrained and
        cold over that finished reply.

        The turn deadline covers the grounding loop and the writer, which is
        what the customer waits on. Delivery (on_reply) is never cut off
        mid-send; the analyst gets whatever budget is left, with a floor, and
        its failure only degrades the bookkeeping.
        """
        deadline = _Deadline(self._turn_deadline)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            conversation = await self._run_tool_loop(
                client,
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
                max_tool_calls=max_tool_calls,
                deadline=deadline,
                business_id=business_id,
            )
            reply = await self._write_reply(client, conversation, tools, deadline, business_id)

            # The customer's reply is ready and nothing below this line changes
            # it, so hand it over now rather than making them wait out the
            # analysis. The hook applies the safety guards, so it can rewrite
            # the text — what it returns is what the analyst judges.
            if on_reply is not None:
                reply = await on_reply(reply)

            analyst_deadline = _Deadline(max(deadline.remaining(), 15.0))
            analyst_payload = {
                "messages": [
                    {"role": "system", "content": analyst_system_prompt},
                    *conversation[1:],
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": _ANALYST_INSTRUCTION},
                ],
                "tools": [_tool_to_openai_shape(t) for t in tools],
                "tool_choice": "none",
                "response_format": _json_schema_format(analysis_schema),
                "temperature": self._analyst_temperature,
            }
            analyst_data, _model = await self._call_with_fallback(
                client,
                analyst_payload,
                primary_model=self._analyst_model,
                deadline=analyst_deadline,
                business_id=business_id,
            )
            analysis = _parse_structured(analyst_data, analysis_schema)
        return reply, analysis
