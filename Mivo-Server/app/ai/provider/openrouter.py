"""OpenRouter implementation of LLMProvider — Production-hardened with retries,
multi-model failover, bulletproof JSON repair, and self-correcting agentic loops.
"""
import asyncio
import json
import logging
import os
import random
import re
import uuid
from typing import Any

import httpx

from app.ai.provider.base import LLMProvider, LLMProviderError, T, ToolDefinition, ToolExecutor
from app.core.config import get_settings

logger = logging.getLogger("app.ai.provider.openrouter")

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Fallback models in priority order if the primary model is unavailable or encounters errors
_DEFAULT_FALLBACK_MODELS = [
    "anthropic/claude-3.5-haiku",
    "openai/gpt-4o-mini",
]

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


async def _log_usage(business_id: uuid.UUID, kind: str, model: str, usage_events: list[dict]) -> None:
    """Persists aggregated AiUsageLog for the turn. Never raises — usage logging
    must never fail a customer-facing reply."""
    if not usage_events:
        return
    try:
        from app.ai.models import AiUsageLog
        from app.core.db import async_session_factory

        async with async_session_factory() as db:
            db.add(
                AiUsageLog(
                    business_id=business_id,
                    kind=kind,
                    model=model,
                    prompt_tokens=sum(u.get("prompt_tokens", 0) for u in usage_events),
                    completion_tokens=sum(u.get("completion_tokens", 0) for u in usage_events),
                    total_tokens=sum(u.get("total_tokens", 0) for u in usage_events),
                    cost_usd=sum(u.get("cost", 0) or 0 for u in usage_events),
                )
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[ai_usage_log] failed to persist usage: {exc}")


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
    """Extracts, cleans, and validates structured output from the model.
    Handles markdown code fences, leading/trailing conversational text, and
    escapes gracefully."""
    if not content or not content.strip():
        raise ValueError(f"Empty content returned by model for schema {response_schema.__name__}")

    raw = content.strip()

    # 1. Strip markdown code fences (```json ... ``` or ``` ... ```)
    if "```" in raw:
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
        if fence_match:
            raw = fence_match.group(1).strip()
        else:
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw).strip()

    # 2. Try direct Pydantic JSON validation
    try:
        return response_schema.model_validate_json(raw)
    except Exception:
        pass

    # 3. Extract the outermost JSON object { ... }
    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        candidate = raw[brace_start : brace_end + 1]
        try:
            return response_schema.model_validate_json(candidate)
        except Exception:
            pass

        # 4. Try json.loads to resolve minor encoding/dict issues
        try:
            parsed = json.loads(candidate)
            return response_schema.model_validate(parsed)
        except Exception:
            pass

        # 5. Clean trailing commas before closing braces/brackets
        try:
            cleaned_commas = re.sub(r",\s*([\]\}])", r"\1", candidate)
            parsed = json.loads(cleaned_commas)
            return response_schema.model_validate(parsed)
        except Exception:
            pass

    raise ValueError(f"Could not parse valid {response_schema.__name__} from output: {content[:200]}")


def _parse_structured(data: dict, response_schema: type[T]) -> T:
    try:
        content = data["choices"][0]["message"]["content"]
        return _clean_and_parse_json(content, response_schema)
    except Exception as exc:
        raise LLMProviderError(f"OpenRouter returned invalid structured output: {exc}") from exc


def _json_schema_format(response_schema: type[T]) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": response_schema.__name__,
            "strict": True,
            "schema": response_schema.model_json_schema(),
        },
    }


def _is_pure_greeting(messages: list[dict[str, Any]]) -> bool:
    """Detects if the latest customer message is a simple greeting or pleasantry
    without product intent, allowing tool_choice='auto' instead of forced tool calls."""
    if not messages:
        return False
    for m in reversed(messages):
        if m.get("role") == "user":
            content_val = m.get("content")
            if isinstance(content_val, list):
                # Has multimodal parts (image) — not a pure greeting
                return False
            text = str(content_val or "").strip().lower()
            greetings = {
                "salom", "assalomu alaykum", "assalomu aleykum", "salom alaykum",
                "privet", "zdravstvuyte", "hello", "hi", "hey",
                "rahmat", "raxmat", "spasibo", "thank you", "thanks", "ok"
            }
            words = re.findall(r"\w+", text)
            if len(words) <= 3 and any(w in greetings for w in words):
                product_hints = {
                    "narx", "narxi", "qancha", "bor", "bormi", "razmer", "rangi",
                    "model", "tufli", "kiyim", "krossovka", "hoodie", "shim", "futbolka",
                    "sumka", "dostavka", "skidka", "chegirma", "buyurtma", "olmoqchiman"
                }
                if not any(h in text for h in product_hints):
                    return True
            break
    return False


class OpenRouterProvider(LLMProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise LLMProviderError("OPENROUTER_API_KEY is not set.")
        self._api_key = settings.openrouter_api_key

        model = settings.openrouter_model or "google/gemini-2.5-flash"
        if "gemini-2.0-flash-001" in model or not model:
            model = "google/gemini-2.5-flash"
        self._model = model

        # Fallback model list
        self._fallback_models = [
            m for m in _DEFAULT_FALLBACK_MODELS if m != self._model
        ]
        self._timeout = settings.llm_request_timeout_seconds or 30.0

        # In testing environments, use fast backoff to keep test runs instant
        self._is_testing = "PYTEST_CURRENT_TEST" in os.environ
        self._max_retries = 2

    async def _call_single_model(
        self,
        client: httpx.AsyncClient,
        payload: dict,
        model_name: str,
    ) -> dict:
        """Calls OpenRouter with a specific model, retrying on transient errors."""
        payload_copy = dict(payload)
        payload_copy["model"] = model_name

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await client.post(
                    _OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "HTTP-Referer": "https://mivo.uz",
                        "X-Title": "Mivo AI",
                    },
                    json=payload_copy,
                )
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    if attempt < self._max_retries:
                        retry_after = response.headers.get("Retry-After")
                        if self._is_testing:
                            delay = 0.001
                        elif retry_after and retry_after.isdigit():
                            delay = float(retry_after)
                        else:
                            delay = 0.4 * (2 ** attempt) + random.uniform(0.05, 0.15)

                        logger.warning(
                            f"[openrouter] Model {model_name} returned HTTP {response.status_code}. "
                            f"Retrying in {delay:.2f}s (attempt {attempt + 1}/{self._max_retries})..."
                        )
                        await asyncio.sleep(delay)
                        continue
                    response.raise_for_status()

                response.raise_for_status()
                return response.json()

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    delay = 0.001 if self._is_testing else (0.4 * (2 ** attempt) + random.uniform(0.05, 0.15))
                    logger.warning(
                        f"[openrouter] Network error on {model_name}: {exc}. "
                        f"Retrying in {delay:.2f}s (attempt {attempt + 1}/{self._max_retries})..."
                    )
                    await asyncio.sleep(delay)
                    continue
                raise LLMProviderError(f"OpenRouter request timed out or network failed for {model_name}: {exc}") from exc

            except httpx.HTTPStatusError as exc:
                last_error = exc
                raise LLMProviderError(f"OpenRouter HTTP error {exc.response.status_code}: {exc.response.text[:200]}") from exc

            except Exception as exc:
                last_error = exc
                raise LLMProviderError(f"OpenRouter call failed for {model_name}: {exc}") from exc

        if last_error:
            raise LLMProviderError(f"OpenRouter retries exhausted for {model_name}: {last_error}")
        raise LLMProviderError(f"OpenRouter call failed for {model_name}")

    async def _call_with_fallback(
        self,
        client: httpx.AsyncClient,
        payload: dict,
    ) -> tuple[dict, str]:
        """Tries the primary model; if it fails with an LLMProviderError,
        cascades to fallback models."""
        models_to_try = [self._model] + self._fallback_models
        errors = []

        for model_name in models_to_try:
            try:
                data = await self._call_single_model(client, payload, model_name=model_name)
                return data, model_name
            except LLMProviderError as exc:
                logger.warning(f"[openrouter] Model {model_name} failed: {exc}. Attempting next model...")
                errors.append(f"{model_name}: {exc}")

        raise LLMProviderError(f"All LLM models failed: {'; '.join(errors)}")

    async def _call(self, client: httpx.AsyncClient, payload: dict) -> dict:
        """Standard call with failover, for backward compatibility."""
        data, _ = await self._call_with_fallback(client, payload)
        return data

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
            data, model_used = await self._call_with_fallback(client, payload)

        if business_id is not None:
            await _log_usage(business_id, "extraction", model_used, [data.get("usage") or {}])
        return _parse_structured(data, response_schema)

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
        conversation = [{"role": "system", "content": system_prompt}, *messages]
        openai_tools = [_tool_to_openai_shape(t) for t in tools]
        usage_events: list[dict] = []
        model_used = self._model

        # Check if customer simply greeted — avoid forcing dummy product searches
        is_greeting = _is_pure_greeting(messages)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for i in range(max_tool_calls):
                # Force at least one grounding tool call on round 0 unless it's a pure greeting
                tool_choice = "auto" if is_greeting else ("required" if i == 0 else "auto")

                payload = {
                    "messages": conversation,
                    "tools": openai_tools,
                    "tool_choice": tool_choice,
                }
                data, model_used = await self._call_with_fallback(client, payload)
                usage_events.append(data.get("usage") or {})

                message = data["choices"][0]["message"]
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    break

                conversation.append(
                    {
                        "role": "assistant",
                        "content": message.get("content"),
                        "tool_calls": tool_calls,
                    }
                )

                for call in tool_calls:
                    name = call["function"]["name"]
                    raw_args = call["function"]["arguments"] or "{}"

                    try:
                        arguments = json.loads(raw_args)
                    except json.JSONDecodeError as exc:
                        # Attempt to fix single quotes or minor syntax errors
                        try:
                            arguments = json.loads(raw_args.replace("'", '"'))
                        except Exception:
                            logger.warning(f"[openrouter] Model sent malformed tool arguments: {raw_args} ({exc})")
                            # Provide feedback to the model in the tool role so it self-corrects
                            conversation.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call["id"],
                                    "content": json.dumps({"error": f"Invalid JSON arguments: {exc}. Please re-call with valid JSON."}),
                                }
                            )
                            continue

                    try:
                        result = await tool_executor(name, arguments)
                    except Exception as exc:
                        logger.warning(f"[openrouter] Tool execution error for '{name}': {exc}")
                        result = {"error": f"Tool execution failed: {str(exc)}"}

                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(result),
                        }
                    )

            # Final call: no tools, constrained to the fused response schema.
            final_payload = {
                "messages": [
                    *conversation,
                    {
                        "role": "user",
                        "content": (
                            "Using everything above, produce your final response now "
                            "as JSON matching the required schema."
                        ),
                    },
                ],
                "response_format": _json_schema_format(response_schema),
            }
            final_data, final_model = await self._call_with_fallback(client, final_payload)
            usage_events.append(final_data.get("usage") or {})

        if business_id is not None:
            await _log_usage(business_id, "conversation", final_model or model_used, usage_events)
        return _parse_structured(final_data, response_schema)
