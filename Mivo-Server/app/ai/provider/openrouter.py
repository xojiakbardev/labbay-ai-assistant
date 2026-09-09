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

from app.ai.provider.base import (
    LLMProvider,
    LLMProviderError,
    ReplyHook,
    T,
    ToolDefinition,
    ToolExecutor,
)
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


_PRODUCT_HINTS = {
    # uz
    "narx", "narxi", "narxlari", "qancha", "qanchadan", "bor", "bormi", "bormidi",
    "razmer", "razmeri", "o'lcham", "olcham", "rang", "rangi", "ranglari", "model",
    "modeli", "tufli", "poyabzal", "kiyim", "krossovka", "krosovka", "shim", "ko'ylak",
    "koylak", "futbolka", "kurtka", "sumka", "kepka", "hoodie", "kostyum", "dostavka",
    "yetkazib", "chegirma", "aksiya", "buyurtma", "olmoqchiman", "kerak", "ko'rsating",
    "korsating", "rasm", "rasmi", "katalog", "assortiment", "yangi", "mavjud",
    # ru
    "цена", "цены", "сколько", "стоит", "есть", "размер", "цвет", "модель", "доставка",
    "скидка", "заказ", "заказать", "нужен", "нужна", "покажите", "фото", "каталог",
    "наличии", "новинки",
    # en
    "price", "cost", "how much", "size", "color", "colour", "model", "delivery",
    "discount", "order", "need", "show", "photo", "catalog", "available", "stock",
}

# Messages that are conversation, not a product-fact request. Forcing a catalog
# search on these is what made the AI answer "that's too expensive" with a
# product dump instead of handling the objection.
_NO_GROUNDING_NEEDED = {
    # greetings / pleasantries
    "salom", "assalom", "assalomu", "alaykum", "aleykum", "vaalaykum", "hormang",
    "privet", "zdravstvuyte", "dobriy", "hello", "hi", "hey", "goodbye", "xayr",
    # thanks / acknowledgement
    "rahmat", "raxmat", "tashakkur", "spasibo", "blagodaryu", "thanks", "thank",
    "ok", "okey", "xo'p", "xop", "mayli", "tushundim", "zo'r", "zor", "yaxshi",
    "ha", "ya", "yo'q", "yoq", "net", "da", "yes", "no", "otlichno", "super",
    # objections / deferrals — these need a salesperson's answer, not a search
    "qimmat", "qimmatroq", "qimmatku", "dorogo", "dorogovato", "expensive", "pricey",
    "o'ylab", "oylab", "o'ylayman", "oylayman", "podumayu", "keyinroq", "keyin",
    "pozje", "later", "bbrz", "hozircha", "poka",
    # human handoff
    "operator", "odam", "chelovek", "human", "menejer", "manager",
}

_PHONE_ONLY_RE = re.compile(r"^[\d\s\-+()./]{7,25}$")

# --- Writer pass -----------------------------------------------------------
#
# The writer thinks for one line before it writes, because deciding the sales
# move and phrasing it are different jobs and doing them in that order is
# measurably better than doing them at once. The plan is internal, so it's
# fenced off by a marker that _extract_customer_message strips — and because
# whatever survives that strip is sent verbatim to a real customer over
# Instagram, the parser is deliberately paranoid about it.
_WRITER_MESSAGE_MARKER = "===MESSAGE==="

_WRITER_INSTRUCTION = f"""Now write the message you are about to send this customer.

Answer in exactly this format:

PLAN: <one short line, for yourself: where this customer is in the sale right now, \
and the single move you're making with this message>
{_WRITER_MESSAGE_MARKER}
<the message itself, exactly as the customer will read it>

Everything after {_WRITER_MESSAGE_MARKER} is delivered to the customer word for word.
So write only the message there: no labels, no quotes, no JSON, no markdown, no notes
to yourself, and no mention of searches, tools, product IDs or these instructions.
State only facts the tool results above actually returned, and write in the language
the customer is writing in."""

_ANALYST_INSTRUCTION = (
    "The assistant message directly above is the reply that was just sent to this "
    "customer. Read the whole conversation as it now stands and return your analysis "
    "as JSON matching the required schema."
)

_PLAN_LINE_RE = re.compile(r"^\s*(plan|reja|план)\s*[:\-—]", re.IGNORECASE)
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def _strip_plan_lines(text: str) -> str:
    lines = text.splitlines()
    while lines and _PLAN_LINE_RE.match(lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip()


def _extract_customer_message(raw: str) -> str:
    """Pulls the customer-facing half out of the writer pass's PLAN/message output.

    Returns "" if nothing usable is left, which the caller turns into a provider
    error — the existing fallback (apologise and hand off to a human) is a far
    better outcome than sending the model's internal note to the customer.
    """
    if not raw or not raw.strip():
        return ""

    text = raw.strip()
    if _WRITER_MESSAGE_MARKER in text:
        after = text.rsplit(_WRITER_MESSAGE_MARKER, 1)[1].strip()
        # Marker present but nothing after it: fall back to whatever came
        # before, minus the plan line.
        text = after or text.rsplit(_WRITER_MESSAGE_MARKER, 1)[0]

    # The model dropped the marker but kept the label — drop the plan anyway.
    text = _strip_plan_lines(text)
    text = _FENCE_RE.sub("", text).strip()

    # Last line of defence: a leftover plan label means the split failed and we
    # can't tell internal notes from the message, so send nothing.
    if _PLAN_LINE_RE.match(text) or _WRITER_MESSAGE_MARKER in text:
        return ""
    return text


def _needs_product_grounding(messages: list[dict[str, Any]]) -> bool:
    """Whether this turn should be forced to call a retrieval tool first.

    Grounding exists to stop the model inventing prices and stock, so anything
    that looks like a product question forces a tool call. But a customer
    saying "qimmat ekan" or "rahmat, o'ylab ko'raman" isn't asking for product
    facts at all — forcing a search there produced a catalog dump in place of
    an actual reply to what they said, which is one of the main reasons the
    conversation reads like a query interface rather than a salesperson.

    A product hint anywhere in the message wins, so "qimmat ekan, arzonrog'i
    bormi?" still gets grounded.
    """
    if not messages:
        return True

    for m in reversed(messages):
        if m.get("role") != "user":
            continue

        content_val = m.get("content")
        if isinstance(content_val, list):
            return True  # multimodal (image) — always look for a match in the catalog

        text = str(content_val or "").strip().lower()
        if not text:
            return True

        if any(hint in text for hint in _PRODUCT_HINTS):
            return True

        # A bare phone number is the customer answering "leave your number",
        # not a product question.
        if _PHONE_ONLY_RE.match(text):
            return False

        words = re.findall(r"[\w']+", text)
        if not words:
            return False

        # A short message carrying a conversational signal — a greeting, a
        # thank-you, an objection, "let me think about it" — is answered, not
        # searched. Deliberately "any word", not "every word": requiring every
        # token to be on a list meant one unlisted filler word ("qimmat ekan")
        # dragged the whole message back to a forced search. Anything longer or
        # with no such signal falls through to grounding, which is the safe
        # default — the cost of an unnecessary search is a slower turn, the
        # cost of a missing one is an invented price.
        if len(words) <= 8 and any(w in _NO_GROUNDING_NEEDED for w in words):
            return False

        return True

    return True


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

        # Writing the customer's reply and scoring the lead are different jobs
        # with opposite sampling needs — see the note in app/core/config.py.
        self._writer_model = settings.llm_writer_model or self._model
        self._analyst_model = settings.llm_analyst_model or self._model
        self._writer_temperature = settings.llm_writer_temperature
        self._analyst_temperature = settings.llm_analyst_temperature

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
        primary_model: str | None = None,
    ) -> tuple[dict, str]:
        """Tries the primary model; if it fails with an LLMProviderError,
        cascades to fallback models. `primary_model` overrides the configured
        default for calls that want a different tier (the writer pass may run
        on a stronger model than the analyst pass)."""
        primary = primary_model or self._model
        models_to_try = [primary] + [m for m in self._fallback_models if m != primary]
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

    async def _run_tool_loop(
        self,
        client: httpx.AsyncClient,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        tool_executor: ToolExecutor,
        max_tool_calls: int,
        usage_events: list[dict],
    ) -> tuple[list[dict[str, Any]], str]:
        """Runs the grounding loop and returns (conversation, model_used).

        The returned conversation is the system prompt, the chat history, and
        every assistant/tool exchange the loop produced — i.e. all the grounded
        product facts the writer and analyst passes need in front of them.
        """
        conversation: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *messages]
        openai_tools = [_tool_to_openai_shape(t) for t in tools]
        model_used = self._model
        needs_grounding = _needs_product_grounding(messages)

        for i in range(max_tool_calls):
            # Force one grounding call up front only when the customer actually
            # asked something a tool can answer — see _needs_product_grounding.
            tool_choice = "required" if (i == 0 and needs_grounding) else "auto"

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
                                "content": json.dumps(
                                    {"error": f"Invalid JSON arguments: {exc}. Please re-call with valid JSON."}
                                ),
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

        return conversation, model_used

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
        usage_events: list[dict] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            conversation, model_used = await self._run_tool_loop(
                client,
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
                max_tool_calls=max_tool_calls,
                usage_events=usage_events,
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
                "temperature": self._analyst_temperature,
            }
            final_data, final_model = await self._call_with_fallback(client, final_payload)
            usage_events.append(final_data.get("usage") or {})

        if business_id is not None:
            await _log_usage(business_id, "conversation", final_model or model_used, usage_events)
        return _parse_structured(final_data, response_schema)

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
        in LLMProvider. Splitting the passes is the whole point: the writer runs
        unconstrained and warm so the reply reads like a person typing, and the
        analyst runs schema-constrained and cold over that finished reply, which
        is also a better vantage point for scoring the lead than a schema field
        filled in before the reply it's judging even exists.
        """
        usage_events: list[dict] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            conversation, model_used = await self._run_tool_loop(
                client,
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
                max_tool_calls=max_tool_calls,
                usage_events=usage_events,
            )

            # Pass 1 — the writer. No response_format, no tools: plain prose at
            # a sampling temperature that leaves room to sound human.
            writer_payload = {
                "messages": [*conversation, {"role": "system", "content": _WRITER_INSTRUCTION}],
                "temperature": self._writer_temperature,
            }
            writer_data, writer_model = await self._call_with_fallback(
                client, writer_payload, primary_model=self._writer_model
            )
            usage_events.append(writer_data.get("usage") or {})

            raw_reply = writer_data["choices"][0]["message"].get("content") or ""
            reply = _extract_customer_message(raw_reply)
            if not reply:
                raise LLMProviderError(
                    f"Writer pass returned no customer-facing message (raw: {raw_reply[:200]!r})"
                )

            # The customer's reply is ready and nothing below this line changes
            # it, so hand it over now rather than making them wait out the
            # analysis. Whatever comes back is what the analyst then judges —
            # the hook applies the safety guards, so it can rewrite the text.
            if on_reply is not None:
                reply = await on_reply(reply)

            # Pass 2 — the analyst. Same grounded conversation, different system
            # prompt, with the reply that was just written in view as the last
            # assistant turn.
            analyst_payload = {
                "messages": [
                    {"role": "system", "content": analyst_system_prompt},
                    *conversation[1:],
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": _ANALYST_INSTRUCTION},
                ],
                "response_format": _json_schema_format(analysis_schema),
                "temperature": self._analyst_temperature,
            }
            analyst_data, _analyst_model = await self._call_with_fallback(
                client, analyst_payload, primary_model=self._analyst_model
            )
            usage_events.append(analyst_data.get("usage") or {})
            analysis = _parse_structured(analyst_data, analysis_schema)

        if business_id is not None:
            await _log_usage(business_id, "conversation", writer_model or model_used, usage_events)
        return reply, analysis
