"""The writer/analyst split (LLMProvider.run_sales_turn).

A customer-facing reply and the lead bookkeeping that accompanies it are
generated separately: constrained JSON decoding flattens prose, and a schema
that emits `reply` before any of the analysis makes the model commit to wording
before it has worked out what the sales move is. These tests pin the split, the
sampling each pass gets, and — most importantly — that the writer's internal
plan line can never reach a customer.
"""
import json

import httpx
import pytest
import respx
from pydantic import BaseModel

from app.ai.provider.base import LLMProvider, LLMProviderError, ToolDefinition
from app.ai.provider.openrouter import (
    OpenRouterProvider,
    _extract_customer_message,
    _needs_product_grounding,
)


class _Analysis(BaseModel):
    qualification_reason: str
    lead_status: str
    lead_score: int = 0


class _Fused(_Analysis):
    reply: str


_SEARCH_TOOL = ToolDefinition(
    name="search_products",
    description="Search products",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)


def _tool_call_response(query: str) -> httpx.Response:
    return httpx.Response(
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
                                "function": {"name": "search_products", "arguments": json.dumps({"query": query})},
                            }
                        ],
                    }
                }
            ]
        },
    )


def _content_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def _noop_executor(name, arguments):
    return {"products": [{"name": "Nike Hoodie", "price": 450000}]}


@respx.mock
async def test_run_sales_turn_writes_prose_then_analyses_it() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [
        _tool_call_response("hoodie"),  # tool loop, round 0
        _content_response("thinking..."),  # tool loop, round 1 — no tool calls, loop breaks
        _content_response("PLAN: they asked a price, quote it and ask the size\n"
                          "===MESSAGE===\nNike Hoodie — 450 000 so'm. Qaysi razmer kerak?"),
        _content_response(json.dumps({
            "qualification_reason": "Asked the price of a specific hoodie.",
            "lead_status": "warm",
            "lead_score": 55,
        })),
    ]

    provider = OpenRouterProvider()
    reply, analysis = await provider.run_sales_turn(
        system_prompt="sales-prompt",
        messages=[{"role": "user", "content": "hoodie narxi qancha?"}],
        tools=[_SEARCH_TOOL],
        tool_executor=_noop_executor,
        fused_schema=_Fused,
        analysis_schema=_Analysis,
        analyst_system_prompt="analyst-prompt",
    )

    # The reply is the writer's prose, with its internal plan stripped.
    assert reply == "Nike Hoodie — 450 000 so'm. Qaysi razmer kerak?"
    assert analysis.lead_score == 55
    assert route.call_count == 4

    writer_body = json.loads(route.calls[2].request.content)
    analyst_body = json.loads(route.calls[3].request.content)

    # The writer is unconstrained and warm (it may not call tools — the
    # definitions ride along only because the history holds tool calls); the
    # analyst is constrained and cold.
    assert "response_format" not in writer_body
    assert writer_body["tool_choice"] == "none"
    assert writer_body["temperature"] > 0
    assert analyst_body["response_format"]["json_schema"]["name"] == "_Analysis"
    assert analyst_body["temperature"] == 0.0

    # The analyst judges the reply that was actually sent, under its own prompt.
    assert analyst_body["messages"][0] == {"role": "system", "content": "analyst-prompt"}
    assert analyst_body["messages"][-2]["content"] == reply
    # ...and it still sees the grounded product facts from the tool loop.
    assert any(m.get("role") == "tool" for m in analyst_body["messages"])


@respx.mock
async def test_run_sales_turn_rejects_a_reply_that_is_only_a_plan() -> None:
    """A writer that emits no customer-facing half must fail loudly rather than
    send its own internal note to the customer — the caller's fallback
    (apologise, hand off to a human) is a far better outcome."""
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [
        _content_response("no tools needed"),
        _content_response("PLAN: greet them warmly and ask what they need"),
        # Reminded of the format once, it still only plans.
        _content_response("PLAN: greet them"),
    ]

    provider = OpenRouterProvider()
    with pytest.raises(LLMProviderError, match="no customer-facing message"):
        await provider.run_sales_turn(
            system_prompt="sales-prompt",
            messages=[{"role": "user", "content": "salom"}],
            tools=[_SEARCH_TOOL],
            tool_executor=_noop_executor,
            fused_schema=_Fused,
            analysis_schema=_Analysis,
            analyst_system_prompt="analyst-prompt",
        )


@respx.mock
async def test_writer_that_forgets_the_format_is_asked_once_more() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [
        _content_response("no tools needed"),
        _content_response("Assalomu alaykum!"),  # no marker: can't tell plan from message
        _content_response("PLAN: greet\n===MESSAGE===\nAssalomu alaykum!"),
        _content_response(json.dumps({"qualification_reason": "greeting", "lead_status": "cold", "lead_score": 5})),
    ]
    reply, _ = await OpenRouterProvider().run_sales_turn(
        system_prompt="sales-prompt",
        messages=[{"role": "user", "content": "salom"}],
        tools=[_SEARCH_TOOL],
        tool_executor=_noop_executor,
        fused_schema=_Fused,
        analysis_schema=_Analysis,
        analyst_system_prompt="analyst-prompt",
    )
    assert reply == "Assalomu alaykum!"
    assert route.call_count == 4


@respx.mock
async def test_error_body_with_http_200_is_a_provider_failure() -> None:
    """A 200 carrying {"error": ...} and no choices used to crash with a
    KeyError outside the provider-error handling (no apology, no handoff)."""
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [httpx.Response(200, json={"error": {"message": "overloaded"}})] * 3
    with pytest.raises(LLMProviderError):
        await OpenRouterProvider().run_sales_turn(
            system_prompt="s", messages=[{"role": "user", "content": "salom"}], tools=[_SEARCH_TOOL],
            tool_executor=_noop_executor, fused_schema=_Fused, analysis_schema=_Analysis, analyst_system_prompt="a",
        )


@respx.mock
async def test_truncated_generation_is_not_accepted() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [
        httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "Nike Hoodie — 45"}}]})
    ] * 3
    with pytest.raises(LLMProviderError):
        await OpenRouterProvider().run_sales_turn(
            system_prompt="s", messages=[{"role": "user", "content": "salom"}], tools=[_SEARCH_TOOL],
            tool_executor=_noop_executor, fused_schema=_Fused, analysis_schema=_Analysis, analyst_system_prompt="a",
        )


@respx.mock
async def test_conversational_message_is_not_forced_into_a_product_search() -> None:
    """"Qimmat ekan" is an objection to answer, not a query to run. Forcing a
    tool call here is what produced a catalog dump in place of a reply."""
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [
        _content_response("no tool call"),
        _content_response("===MESSAGE===\nTushunaman, bu model original charm."),
        _content_response(json.dumps({
            "qualification_reason": "Pushed back on price.",
            "lead_status": "warm",
            "lead_score": 40,
        })),
    ]

    provider = OpenRouterProvider()
    await provider.run_sales_turn(
        system_prompt="sales-prompt",
        messages=[{"role": "user", "content": "qimmat ekan"}],
        tools=[_SEARCH_TOOL],
        tool_executor=_noop_executor,
        fused_schema=_Fused,
        analysis_schema=_Analysis,
        analyst_system_prompt="analyst-prompt",
    )

    assert json.loads(route.calls[0].request.content)["tool_choice"] == "auto"


@respx.mock
async def test_product_question_still_forces_grounding() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")
    route.side_effect = [
        _tool_call_response("hoodie"),
        _content_response("done"),
        _content_response("===MESSAGE===\n450 000 so'm."),
        _content_response(json.dumps({
            "qualification_reason": "Asked a price.", "lead_status": "warm", "lead_score": 50,
        })),
    ]

    provider = OpenRouterProvider()
    await provider.run_sales_turn(
        system_prompt="sales-prompt",
        messages=[{"role": "user", "content": "hoodie narxi qancha?"}],
        tools=[_SEARCH_TOOL],
        tool_executor=_noop_executor,
        fused_schema=_Fused,
        analysis_schema=_Analysis,
        analyst_system_prompt="analyst-prompt",
    )

    assert json.loads(route.calls[0].request.content)["tool_choice"] == "required"


async def test_single_pass_provider_still_works_through_the_default() -> None:
    """A provider implementing only the two abstract methods keeps working —
    run_sales_turn falls back to one fused call and splits the result."""

    class LegacyProvider(LLMProvider):
        async def generate_structured(self, **kwargs):
            raise NotImplementedError

        async def run_agentic_turn(
            self, *, system_prompt, messages, tools, response_schema, tool_executor,
            max_tool_calls=4, business_id=None,
        ):
            await tool_executor("search_products", {"query": "hoodie"})
            return response_schema(
                reply="Ha, bor!", qualification_reason="Asked about stock.",
                lead_status="warm", lead_score=45,
            )

    calls = []

    async def executor(name, arguments):
        calls.append(name)
        return {"products": []}

    reply, analysis = await LegacyProvider().run_sales_turn(
        system_prompt="sys",
        messages=[{"role": "user", "content": "hoodie bormi"}],
        tools=[],
        tool_executor=executor,
        fused_schema=_Fused,
        analysis_schema=_Analysis,
        analyst_system_prompt="analyst",
    )

    assert reply == "Ha, bor!"
    assert analysis.lead_score == 45
    assert calls == ["search_products"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("PLAN: quote the price\n===MESSAGE===\nNarxi 450 000 so'm.", "Narxi 450 000 so'm."),
        ("===MESSAGE===\nSalom!", "Salom!"),
        # Some models wrap the message in a fence.
        ("===MESSAGE===\n```\nSalom\n```", "Salom"),
        # Without the marker there's no telling plan from message — nothing
        # is sent (the writer is asked once more, then the turn hands off).
        ("PLAN: greet them\nAssalomu alaykum!", ""),
        ("Reja: salomlashish\nAssalomu alaykum!", ""),
        ("Assalomu alaykum! Nima qidiryapsiz?", ""),
        ("**PLAN:** greet them\nAssalomu alaykum!", ""),
        # Regression: marker with nothing after it used to fall back to the
        # text BEFORE the marker, leaking plan lines.
        ("PLAN: narx so'radi.\nKeyingi qadam: razmer so'rash.\n===MESSAGE===", ""),
        ("PLAN: x\n===MESSAGE===\nKeyingi qadam: razmer so'rash", ""),
        # A later line that happens to start like a plan is the message's own
        # content, not a leak.
        (
            "PLAN: x\n===MESSAGE===\nBuyurtma tartibi:\nReja: ertaga yetkazamiz",
            "Buyurtma tartibi:\nReja: ertaga yetkazamiz",
        ),
        # Nothing usable.
        ("PLAN: only a plan, no message at all", ""),
        ("", ""),
    ],
)
def test_extract_customer_message(raw: str, expected: str) -> None:
    assert _extract_customer_message(raw) == expected


@pytest.mark.parametrize(
    "text,needs_grounding",
    [
        ("salom", False),
        ("rahmat", False),
        ("qimmat ekan", False),
        ("rahmat, o'ylab ko'raman", False),
        ("+998 90 123 45 67", False),
        ("operator bilan gaplashsam bo'ladimi", False),
        ("krossovka bormi", True),
        ("narxi qancha", True),
        # A product hint anywhere wins over the conversational signal.
        ("qimmat ekan, arzonrog'i bormi", True),
        ("menga sovg'a uchun nimadir kerak", True),
    ],
)
def test_needs_product_grounding(text: str, needs_grounding: bool) -> None:
    assert _needs_product_grounding([{"role": "user", "content": text}]) is needs_grounding


def test_a_debounced_burst_is_judged_as_a_whole() -> None:
    """"Air Max narxi qancha" + "rahmat" is still a price question."""
    burst = [
        {"role": "assistant", "content": "Salom!"},
        {"role": "user", "content": "Air Max narxi qancha"},
        {"role": "user", "content": "rahmat"},
    ]
    assert _needs_product_grounding(burst) is True


def test_image_message_always_grounds() -> None:
    multimodal = [{"role": "user", "content": [{"type": "text", "text": "shunaqasi bormi"}]}]
    assert _needs_product_grounding(multimodal) is True


def test_turn_result_reasons_before_it_answers() -> None:
    """Field order is load-bearing: with structured output the fields are
    generated in order, so a conclusion placed before its reasoning is a
    conclusion reached without any."""
    from app.ai.orchestrator import ConversationTurnResult, TurnAnalysis

    fields = list(ConversationTurnResult.model_fields)
    assert fields[0] == "qualification_reason"
    assert fields[-1] == "reply"
    assert "reply" not in TurnAnalysis.model_fields
    assert set(TurnAnalysis.model_fields) | {"reply"} == set(fields)

    analysis = TurnAnalysis(
        qualification_reason="Asked a price.", lead_status="warm", lead_score=55
    )
    fused = ConversationTurnResult(reply="Narxi 450 000 so'm.", **analysis.model_dump())
    assert fused.lead_score == 55
    assert TurnAnalysis.model_validate(fused.model_dump(exclude={"reply"})).lead_score == 55
