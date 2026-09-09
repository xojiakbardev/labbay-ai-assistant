"""Splitting a reply into messages, and delivering it before the analysis runs.

Two changes share this seam, and both are about the customer's experience of
the same reply: it arrives as the two or three short messages a person would
have sent, and it arrives without waiting for bookkeeping they never see.

The risk in both is the same — a bug here means a customer gets mangled text or
no reply at all — so the invariants are pinned hard: a split never loses a
character, delivery happens exactly once, and a failure after delivery never
produces a second message.
"""
import pytest

from app.ai.provider.base import LLMProvider
from app.ai.reply_parts import MAX_PARTS, MIN_TOTAL_CHARS, split_reply

LONG_REPLY = (
    "Assalomu alaykum! Nike Air Max bizda bor, narxi 780 000 so'm. "
    "Original charmdan tayyorlangan, shuning uchun uzoq muddat xizmat qiladi. "
    "Hozirda 42 va 44 razmerlari mavjud. Sizga qaysi razmer kerak?"
)


def _no_text_lost(parts: list[str], original: str) -> bool:
    import re

    return re.sub(r"\s+", "", " ".join(parts)) == re.sub(r"\s+", "", original.strip())


# --- splitting -------------------------------------------------------------


def test_a_short_reply_is_one_message() -> None:
    for reply in ("Ha, bor!", "Nike Air Max — 780 000 so'm. Qaysi razmer kerak?"):
        assert split_reply(reply) == [reply]


def test_a_long_reply_becomes_several_messages() -> None:
    parts = split_reply(LONG_REPLY)
    assert 2 <= len(parts) <= MAX_PARTS
    assert _no_text_lost(parts, LONG_REPLY)
    # The question lands last, where it belongs.
    assert parts[-1].endswith("?")


def test_line_breaks_are_preferred_to_guessing_at_sentences() -> None:
    reply = (
        "Tushunaman, narxi biroz balandroq.\n"
        "Lekin bu model original charm — 2-3 yil kiyiladi.\n"
        "Agar 500 000 atrofida qidirsangiz, Puma Rebound bor. Ko'rsataymi?"
    )
    parts = split_reply(reply)
    assert len(parts) == 3
    assert parts[0] == "Tushunaman, narxi biroz balandroq."


def test_no_split_ever_loses_or_reorders_text() -> None:
    replies = [
        LONG_REPLY,
        "Bor. " * 60,
        "Narxi 780 000 so'm. " * 10,
        "Salom!\n\n\nQanday yordam bera olaman? " + "Bizda ko'p model bor. " * 6,
        "Bitta juda uzun gap bo'lib, ichida hech qanday nuqta yo'q va shuning uchun uni "
        "bo'lishning iloji ham yo'q chunki gap tugamaydi va davom etaveradi shu tarzda",
    ]
    for reply in replies:
        parts = split_reply(reply)
        assert parts, f"no parts for {reply[:40]!r}"
        assert _no_text_lost(parts, reply), f"text lost splitting {reply[:40]!r}"


def test_an_unsplittable_long_reply_stays_whole() -> None:
    """No sentence boundary means no safe place to cut — send it as one."""
    reply = "a" * (MIN_TOTAL_CHARS + 100)
    assert split_reply(reply) == [reply]


def test_parts_are_capped_and_the_overflow_joins_the_last() -> None:
    reply = " ".join(f"Bu {i}-raqamli qisqa gap bo'lib turibdi." for i in range(10))
    parts = split_reply(reply)
    assert len(parts) <= MAX_PARTS
    assert _no_text_lost(parts, reply)


def test_empty_reply_produces_no_messages() -> None:
    assert split_reply("") == []
    assert split_reply("   ") == []


# --- delivery ordering -----------------------------------------------------


class _RecordingProvider(LLMProvider):
    """Single-pass provider — like every existing test fake. The base class's
    default run_sales_turn must still fire on_reply, so callers behave the same
    whichever provider they're handed."""

    def __init__(self, reply: str = "Ha, bor!"):
        self.reply = reply

    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(self, *, response_schema, tool_executor, **kwargs):
        return response_schema(
            reply=self.reply,
            qualification_reason="Asked about stock.",
            lead_status="warm",
            lead_score=45,
        )


async def test_on_reply_fires_and_can_rewrite_the_reply() -> None:
    """The hook is where the safety guards live, so whatever it returns — not
    what the writer produced — is what the turn treats as final."""
    from app.ai.orchestrator import ConversationTurnResult, TurnAnalysis

    seen: list[str] = []

    async def hook(reply: str) -> str:
        seen.append(reply)
        return "Guarded reply."

    reply, analysis = await _RecordingProvider("Original reply.").run_sales_turn(
        system_prompt="sys",
        messages=[{"role": "user", "content": "bormi?"}],
        tools=[],
        tool_executor=None,
        fused_schema=ConversationTurnResult,
        analysis_schema=TurnAnalysis,
        analyst_system_prompt="analyst",
        on_reply=hook,
    )

    assert seen == ["Original reply."]
    assert reply == "Guarded reply."
    assert analysis.lead_score == 45


async def test_run_sales_turn_still_works_without_a_hook() -> None:
    from app.ai.orchestrator import ConversationTurnResult, TurnAnalysis

    reply, _ = await _RecordingProvider().run_sales_turn(
        system_prompt="sys",
        messages=[{"role": "user", "content": "bormi?"}],
        tools=[],
        tool_executor=None,
        fused_schema=ConversationTurnResult,
        analysis_schema=TurnAnalysis,
        analyst_system_prompt="analyst",
    )
    assert reply == "Ha, bor!"


# --- failure after delivery ------------------------------------------------


class _FailsAfterWriting(LLMProvider):
    """Writes a reply, hands it over, then the analysis pass dies."""

    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(self, **kwargs):
        raise NotImplementedError

    async def run_sales_turn(self, *, on_reply=None, **kwargs):
        from app.ai.provider.base import LLMProviderError

        if on_reply is not None:
            await on_reply("Ha, bor! Qaysi razmer kerak?")
        raise LLMProviderError("analyst pass exploded")


def _fake_turn_env(monkeypatch, provider_messages: list[str]):
    """Stubs run_turn's collaborators so the delivery path can be exercised
    without a database."""
    import uuid as _uuid
    from unittest.mock import AsyncMock, MagicMock

    from app.businesses.models import Business
    from app.conversations.models import Conversation

    persisted: list[str] = []

    class _FakeMessage:
        def __init__(self, content):
            self.content = content
            self.external_message_id = None

    async def fake_add_message(db, conversation, sender_type, content, **kwargs):
        persisted.append(content)
        return _FakeMessage(content)

    async def fake_recent(db, conv_id, limit=20):
        return []

    async def fake_prompt(db, biz, cust_id, working_state=None):
        return "system prompt"

    monkeypatch.setattr("app.ai.orchestrator.add_message", fake_add_message)
    monkeypatch.setattr("app.ai.orchestrator.get_recent_messages", fake_recent)
    monkeypatch.setattr("app.ai.orchestrator.build_system_prompt_with_learnings", fake_prompt)

    business = MagicMock(spec=Business)
    business.id = _uuid.uuid4()
    business.language = "uz"

    conversation = MagicMock(spec=Conversation)
    conversation.id = _uuid.uuid4()
    conversation.customer_id = _uuid.uuid4()
    conversation.working_state = {}

    return AsyncMock(), business, conversation, persisted


async def test_analysis_failure_after_delivery_never_sends_a_second_message(monkeypatch) -> None:
    """The customer already has a good answer. Apologising on top of it, or
    escalating to a human over a bookkeeping error, would both be worse than
    simply having no lead score for one turn."""
    from app.ai.orchestrator import run_turn

    db, business, conversation, persisted = _fake_turn_env(monkeypatch, [])
    delivered: list[list[str]] = []

    async def deliver(messages):
        delivered.append([m.content for m in messages])

    state: dict = {}
    result = await run_turn(
        db, _FailsAfterWriting(), business, conversation,
        escalation_state_out=state, deliver=deliver,
    )

    assert len(delivered) == 1, "delivered more than once"
    assert delivered[0] == ["Ha, bor! Qaysi razmer kerak?"]
    assert persisted == ["Ha, bor! Qaysi razmer kerak?"], "a second message was persisted"
    assert result.reply == "Ha, bor! Qaysi razmer kerak?"
    # The caller must know not to persist the placeholder scores.
    assert state["analysis_failed"] is True


async def test_a_failure_before_any_reply_still_delivers_the_fallback(monkeypatch) -> None:
    """The other side of the same coin: if nothing was written, the customer
    must still hear something — and it must go out through the same path."""
    from app.ai.orchestrator import run_turn
    from app.ai.provider.base import LLMProviderError

    class _FailsImmediately(LLMProvider):
        async def generate_structured(self, **kwargs):
            raise NotImplementedError

        async def run_agentic_turn(self, **kwargs):
            raise NotImplementedError

        async def run_sales_turn(self, **kwargs):
            raise LLMProviderError("writer never produced anything")

    db, business, conversation, persisted = _fake_turn_env(monkeypatch, [])
    delivered: list[list[str]] = []

    async def deliver(messages):
        delivered.append([m.content for m in messages])

    state: dict = {}
    result = await run_turn(
        db, _FailsImmediately(), business, conversation,
        escalation_state_out=state, deliver=deliver,
    )

    assert len(delivered) == 1
    assert persisted == delivered[0]
    assert result.reply.strip()
    assert state["escalated"] is True
    assert not state.get("analysis_failed")
    assert conversation.status == "human_needed"
