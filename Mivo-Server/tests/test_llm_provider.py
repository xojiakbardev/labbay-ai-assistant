"""Unit tests for the OpenRouter provider's request/response handling (plan §21
Phase 5 note — this is the slice of ai/provider built for ingestion)."""
import json

import httpx
import pytest
import respx
from pydantic import BaseModel

from app.ai.provider.base import LLMProviderError, ToolDefinition
from app.ai.provider.openrouter import OpenRouterProvider

pytestmark = pytest.mark.asyncio


class _Dummy(BaseModel):
    value: str


@respx.mock
async def test_generate_structured_sends_json_schema_and_parses_response() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"value": "hello"})}}
                ]
            },
        )
    )

    provider = OpenRouterProvider()
    result = await provider.generate_structured(
        system_prompt="sys", user_content="usr", response_schema=_Dummy
    )

    assert result == _Dummy(value="hello")
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["response_format"]["type"] == "json_schema"
    assert sent_body["response_format"]["json_schema"]["name"] == "_Dummy"
    assert sent_body["messages"][0] == {"role": "system", "content": "sys"}
    assert sent_body["messages"][1] == {"role": "user", "content": "usr"}


@respx.mock
async def test_generate_structured_raises_on_http_error() -> None:
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(500)
    )
    provider = OpenRouterProvider()
    with pytest.raises(LLMProviderError):
        await provider.generate_structured(
            system_prompt="sys", user_content="usr", response_schema=_Dummy
        )


@respx.mock
async def test_generate_structured_raises_on_invalid_json() -> None:
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "not json"}}]}
        )
    )
    provider = OpenRouterProvider()
    with pytest.raises(LLMProviderError):
        await provider.generate_structured(
            system_prompt="sys", user_content="usr", response_schema=_Dummy
        )


class _TurnResult(BaseModel):
    reply: str
    lead_status: str


_SEARCH_TOOL = ToolDefinition(
    name="search_products",
    description="search",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)


@respx.mock
async def test_run_agentic_turn_executes_tool_call_then_returns_final_answer() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    # Round 1: model requests a tool call.
    # Round 2: model stops calling tools (plain content, loop breaks).
    # Final call: dedicated json_schema-constrained call for the fused answer.
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "search_products",
                                        "arguments": json.dumps({"query": "hoodie"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        ),
        httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Sure, let me answer that."}}]},
        ),
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"reply": "Found it!", "lead_status": "warm"})
                        }
                    }
                ]
            },
        ),
    ]

    executed_calls = []

    async def fake_executor(name, arguments):
        executed_calls.append((name, arguments))
        return {"products": [{"name": "Nike Hoodie"}]}

    provider = OpenRouterProvider()
    result = await provider.run_agentic_turn(
        system_prompt="sys",
        messages=[{"role": "user", "content": "hoodie bormi?"}],
        tools=[_SEARCH_TOOL],
        response_schema=_TurnResult,
        tool_executor=fake_executor,
    )

    assert result == _TurnResult(reply="Found it!", lead_status="warm")
    assert executed_calls == [("search_products", {"query": "hoodie"})]
    assert route.call_count == 3

    # Final request must not include tools, and must be schema-constrained.
    final_body = json.loads(route.calls[-1].request.content)
    assert "tools" not in final_body
    assert final_body["response_format"]["json_schema"]["name"] == "_TurnResult"


@respx.mock
async def test_run_agentic_turn_stops_at_max_tool_calls() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    tool_call_response = httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_x",
                                "type": "function",
                                "function": {"name": "search_products", "arguments": "{\"query\": \"x\"}"},
                            }
                        ],
                    }
                }
            ]
        },
    )
    final_response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps({"reply": "done", "lead_status": "cold"})}}]},
    )
    # 2 tool-call rounds (max_tool_calls=2) + 1 final call = 3 requests.
    route.side_effect = [tool_call_response, tool_call_response, final_response]

    async def fake_executor(name, arguments):
        return {"ok": True}

    provider = OpenRouterProvider()
    result = await provider.run_agentic_turn(
        system_prompt="sys",
        messages=[],
        tools=[_SEARCH_TOOL],
        response_schema=_TurnResult,
        tool_executor=fake_executor,
        max_tool_calls=2,
    )

    assert result.reply == "done"
    assert route.call_count == 3


@respx.mock
async def test_generate_structured_raises_llm_provider_error_on_timeout() -> None:
    """Real network timeouts (not just HTTP error statuses) must also surface as
    LLMProviderError so callers (orchestrator) can fall back safely — plan §16."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        side_effect=httpx.TimeoutException("simulated timeout")
    )
    provider = OpenRouterProvider()
    with pytest.raises(LLMProviderError):
        await provider.generate_structured(
            system_prompt="sys", user_content="usr", response_schema=_Dummy
        )


@respx.mock
async def test_run_agentic_turn_raises_llm_provider_error_on_timeout() -> None:
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        side_effect=httpx.TimeoutException("simulated timeout")
    )
    provider = OpenRouterProvider()

    async def fake_executor(name, arguments):
        return {}

    with pytest.raises(LLMProviderError):
        await provider.run_agentic_turn(
            system_prompt="sys", messages=[], tools=[_SEARCH_TOOL],
            response_schema=_TurnResult, tool_executor=fake_executor,
        )


@respx.mock
async def test_generate_structured_recovers_from_markdown_fences() -> None:
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "```json\n{\"value\": \"from_markdown\"}\n```"}}
                ]
            },
        )
    )
    provider = OpenRouterProvider()
    result = await provider.generate_structured(
        system_prompt="sys", user_content="usr", response_schema=_Dummy
    )
    assert result == _Dummy(value="from_markdown")


@respx.mock
async def test_generate_structured_recovers_from_outer_chatter_and_trailing_commas() -> None:
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "Here is the result:\n{\"value\": \"with_comma\",}\nHope this helps!"}}
                ]
            },
        )
    )
    provider = OpenRouterProvider()
    result = await provider.generate_structured(
        system_prompt="sys", user_content="usr", response_schema=_Dummy
    )
    assert result == _Dummy(value="with_comma")


@respx.mock
async def test_generate_structured_retries_on_502_and_succeeds() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [
        httpx.Response(502),
        httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"value": "recovered"})}}
                ]
            },
        ),
    ]

    provider = OpenRouterProvider()
    result = await provider.generate_structured(
        system_prompt="sys", user_content="usr", response_schema=_Dummy
    )

    assert result == _Dummy(value="recovered")
    assert route.call_count == 2


@respx.mock
async def test_generate_structured_falls_back_to_secondary_model_on_primary_failure() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    # Primary model fails with 500 across 3 retries (1 initial + 2 retries)
    # Secondary model succeeds on 1st try
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(500),
        httpx.Response(500),
        httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"value": "fallback_model_success"})}}
                ]
            },
        ),
    ]

    provider = OpenRouterProvider()
    result = await provider.generate_structured(
        system_prompt="sys", user_content="usr", response_schema=_Dummy
    )

    assert result == _Dummy(value="fallback_model_success")
    assert route.call_count == 4
    # Check that the 4th call was indeed sent to the fallback model
    fourth_call_body = json.loads(route.calls[3].request.content)
    assert fourth_call_body["model"] != "google/gemini-2.5-flash"


@respx.mock
async def test_run_agentic_turn_recovers_from_malformed_tool_arguments() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    # Round 1: Model issues tool call with invalid JSON arguments
    # Round 2: After receiving error in tool result, model issues valid tool call
    # Round 3: Model finishes tool loop
    # Round 4: Final structured output
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_bad",
                                    "type": "function",
                                    "function": {
                                        "name": "search_products",
                                        "arguments": "{bad_json: 123",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        ),
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_good",
                                    "type": "function",
                                    "function": {
                                        "name": "search_products",
                                        "arguments": json.dumps({"query": "hoodie"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        ),
        httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Found the items."}}]},
        ),
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"reply": "Here is your hoodie!", "lead_status": "warm"})
                        }
                    }
                ]
            },
        ),
    ]

    async def fake_executor(name, arguments):
        return {"products": [{"name": "Nike Hoodie"}]}

    provider = OpenRouterProvider()
    result = await provider.run_agentic_turn(
        system_prompt="sys",
        messages=[{"role": "user", "content": "hoodie bormi?"}],
        tools=[_SEARCH_TOOL],
        response_schema=_TurnResult,
        tool_executor=fake_executor,
    )

    assert result == _TurnResult(reply="Here is your hoodie!", lead_status="warm")
    assert route.call_count == 4

