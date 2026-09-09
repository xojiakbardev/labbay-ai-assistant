"""Phase 7 acceptance test: a scripted conversation retrieves correct product
data (via a real tool-executor -> real Postgres round trip) and produces the
correct fused reply+qualification result — the 'internal harness' the plan
calls for, run as an integration test against the orchestrator directly."""
import uuid

import pytest

from app.ai.orchestrator import ConversationTurnResult, handle_customer_message
from app.ai.provider.base import LLMProvider
from app.auth.models import User
from app.businesses.models import Business
from app.conversations.models import Message
from app.conversations.service import get_or_create_conversation
from app.customers.service import get_or_create_customer
from app.products.models import Product, ProductVariant

pytestmark = pytest.mark.asyncio


class ScriptedProvider(LLMProvider):
    """Simulates a model that actually calls search_products before answering —
    exercises the real tool executor against real seeded Postgres data."""

    def __init__(self, final_result: ConversationTurnResult, expected_tool_query: str = "hoodie"):
        self._final_result = final_result
        self._expected_tool_query = expected_tool_query
        self.tool_call_result: dict | None = None

    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(
        self, *, system_prompt, messages, tools, response_schema, tool_executor, max_tool_calls=4, business_id=None
    ):
        assert any(t.name == "search_products" for t in tools)
        self.tool_call_result = await tool_executor("search_products", {"query": self._expected_tool_query})
        return self._final_result


async def _seed(db_session):
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Hoodie Shop", tone="Friendly")
    db_session.add(business)
    await db_session.flush()

    product = Product(
        business_id=business.id, name="Nike Hoodie", description="Issiq oversize hoodie",
        price=250000, currency="UZS", availability=True, attributes={}, source="manual",
    )
    product.variants = [ProductVariant(variant_type="size", value="M")]
    db_session.add(product)
    await db_session.flush()

    customer = await get_or_create_customer(db_session, business.id, ig_scoped_id="ig-123", username="buyer")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await db_session.commit()
    return business, conversation, product


async def test_handle_customer_message_retrieves_product_and_replies(db_session) -> None:
    business, conversation, product = await _seed(db_session)

    final_result = ConversationTurnResult(
        reply="Ha, qora M razmer mavjud 😊",
        lead_status="warm",
        lead_score=55,
        qualification_reason="Customer asked about a specific product and size.",
        interested_product_ids=[str(product.id)],
    )
    provider = ScriptedProvider(final_result)

    result = await handle_customer_message(
        db_session, provider, business, conversation, "Qora M hoodie bormi?"
    )

    assert result.reply == "Ha, qora M razmer mavjud 😊"
    assert result.lead_status == "warm"
    # The tool executor actually hit Postgres and found the seeded product.
    assert provider.tool_call_result is not None
    assert provider.tool_call_result["products"][0]["name"] == "Nike Hoodie"

    # Both the customer message and the AI reply were persisted.
    messages = (await db_session.execute(
        Message.__table__.select().where(Message.conversation_id == conversation.id)
    )).fetchall()
    contents = {row.content for row in messages}
    assert "Qora M hoodie bormi?" in contents
    assert "Ha, qora M razmer mavjud 😊" in contents


async def test_multi_turn_conversation_builds_on_history(db_session) -> None:
    business, conversation, product = await _seed(db_session)

    turn_1 = ConversationTurnResult(
        reply="Hoodie 250 000 so'm. Qora va oq bor.", lead_status="cold", lead_score=10,
        qualification_reason="Generic price question.",
    )
    await handle_customer_message(db_session, ScriptedProvider(turn_1), business, conversation, "Narxi qancha?")
    await db_session.commit()

    turn_2 = ConversationTurnResult(
        reply="Zo'r! Telefon raqamingizni qoldira olasizmi?", lead_status="hot", lead_score=85,
        qualification_reason="Customer confirmed purchase intent.",
        interested_product_ids=[str(product.id)],
    )
    result = await handle_customer_message(
        db_session, ScriptedProvider(turn_2), business, conversation, "Ha, olmoqchiman."
    )
    await db_session.commit()

    assert result.lead_status == "hot"
    history = (await db_session.execute(
        Message.__table__.select().where(Message.conversation_id == conversation.id).order_by(Message.created_at)
    )).fetchall()
    assert len(history) == 4  # 2 customer + 2 ai messages across both turns


async def test_provider_failure_falls_back_and_escalates(db_session) -> None:
    from app.ai.provider.base import LLMProviderError

    class FailingProvider(LLMProvider):
        async def generate_structured(self, **kwargs):
            raise NotImplementedError

        async def run_agentic_turn(self, **kwargs):
            raise LLMProviderError("simulated timeout")

    business, conversation, _ = await _seed(db_session)
    result = await handle_customer_message(
        db_session, FailingProvider(), business, conversation, "Salom"
    )

    assert result.lead_status == "cold"
    assert "human" in result.qualification_reason.lower()
    await db_session.refresh(conversation)
    assert conversation.status == "human_needed"


class EscalatingProvider(LLMProvider):
    """Simulates a model that calls request_human, then produces the given
    (possibly broken) final reply — for exercising the guard against a reply
    that just echoes the tool call's own internal `reason` argument back at
    the customer."""

    def __init__(self, reason: str, final_reply: str):
        self._reason = reason
        self._final_reply = final_reply

    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(
        self, *, system_prompt, messages, tools, response_schema, tool_executor, max_tool_calls=4, business_id=None
    ):
        await tool_executor("request_human", {"reason": self._reason})
        return ConversationTurnResult(
            reply=self._final_reply, lead_status="cold", lead_score=10,
            qualification_reason="escalated",
        )


async def test_escalation_reply_that_echoes_the_internal_reason_gets_replaced(db_session) -> None:
    """Regression: the model has answered a "kimga murojaat qilishim kerak"
    escalation with "Mijoz kimga murojaat qilishi kerakligini so'radi." —
    literally reading its own internal note back to the customer instead of
    telling them a human is coming."""
    business, conversation, _ = await _seed(db_session)
    reason = "Mijoz kimga murojaat qilishi kerakligini so'radi."
    provider = EscalatingProvider(reason=reason, final_reply=reason)

    result = await handle_customer_message(db_session, provider, business, conversation, "Kimga murojat qilishim kerak")

    assert result.reply != reason
    assert len(result.reply) > 15
    await db_session.refresh(conversation)
    assert conversation.status == "human_needed"


async def test_escalation_reply_that_is_empty_gets_replaced(db_session) -> None:
    business, conversation, _ = await _seed(db_session)
    provider = EscalatingProvider(reason="Customer wants a human.", final_reply="")

    result = await handle_customer_message(db_session, provider, business, conversation, "Odam biriktiring")

    assert result.reply.strip()
    assert len(result.reply) > 15


async def test_escalation_reply_that_is_already_fine_is_kept(db_session) -> None:
    business, conversation, _ = await _seed(db_session)
    good_reply = "Tushunarli, telefon raqamingizni qoldirsangiz operatorimiz tez orada bog'lanadi."
    provider = EscalatingProvider(reason="Customer wants a human.", final_reply=good_reply)

    result = await handle_customer_message(db_session, provider, business, conversation, "Odam kerak")

    assert result.reply == good_reply  # the model's own good reply isn't touched


async def test_escalation_closing_message_matches_business_language(db_session) -> None:
    business, conversation, _ = await _seed(db_session)
    business.language = "Russian"
    await db_session.commit()
    reason = "Customer wants a human."
    provider = EscalatingProvider(reason=reason, final_reply=reason)

    result = await handle_customer_message(db_session, provider, business, conversation, "Мне нужен человек")

    assert "оператор" in result.reply.lower()
