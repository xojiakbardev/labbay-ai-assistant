"""Tests for discount defense-in-depth:
Layer 1: get_active_discounts tool retrieval.
Layer 2: post-generation unverified discount claim guard and message flagging.
"""
import uuid
import pytest

from app.ai.orchestrator import (
    ConversationTurnResult,
    _contains_unverified_discount_claim,
    extract_discount_numbers,
    handle_customer_message,
)
from app.ai.provider.base import LLMProvider
from app.auth.models import User
from app.businesses.models import Business
from app.conversations.models import Message
from app.conversations.service import get_or_create_conversation
from app.customers.service import get_or_create_customer
from app.discounts.models import Discount


def test_extract_discount_numbers():
    assert extract_discount_numbers("Sizga 20% chegirma beramiz") == [20.0]
    assert extract_discount_numbers("15 foiz chegirma bor") == [15.0]
    assert extract_discount_numbers("Скидка 25% на всё") == [25.0]
    assert extract_discount_numbers("50 000 so'm chegirma") == [50000.0]
    assert extract_discount_numbers("Nike krossovka narxi 250 000 so'm") == []


def test_contains_unverified_discount_claim_unit():
    # 1. Unverified 30% claim with no tool call
    assert _contains_unverified_discount_claim("Sizga 30% chegirma beramiz!", [], []) is True

    # 2. Honest denial of discount is NOT flagged
    assert (
        _contains_unverified_discount_claim(
            "Hozirda hech qanday chegirma mavjud emas.", [{"name": "get_active_discounts"}], []
        )
        is False
    )
    assert _contains_unverified_discount_claim("Kechirasiz, chegirma yo'q.", [], []) is False

    # 3. Verified 10% claim matching active discount
    assert (
        _contains_unverified_discount_claim(
            "Birinchi xarid uchun 10% chegirma mavjud!",
            [{"name": "get_active_discounts"}],
            [{"value": 10.0}],
        )
        is False
    )

    # 4. Hallucinated 25% when active discount is only 10%
    assert (
        _contains_unverified_discount_claim(
            "Birinchi xarid uchun 25% chegirma mavjud!",
            [{"name": "get_active_discounts"}],
            [{"value": 10.0}],
        )
        is True
    )

    # 5. Normal product details with price is NOT flagged
    assert (
        _contains_unverified_discount_claim(
            "Nike Hoodie narxi 250 000 so'm, qora rangda mavjud.", [], []
        )
        is False
    )


class SimulatedDiscountProvider(LLMProvider):
    def __init__(self, reply: str, call_tool: bool = False):
        self._reply = reply
        self._call_tool = call_tool

    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(self, *, system_prompt, messages, tools, response_schema, tool_executor, **kwargs):
        if self._call_tool:
            await tool_executor("get_active_discounts", {})
        return ConversationTurnResult(
            reply=self._reply,
            lead_status="warm",
            lead_score=50,
            qualification_reason="Customer asked about discounts.",
        )


async def _seed(db_session):
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Discount Test Shop")
    db_session.add(business)
    await db_session.flush()
    customer = await get_or_create_customer(db_session, business.id, ig_scoped_id=f"ig-{uuid.uuid4()}")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await db_session.commit()
    return business, conversation


@pytest.mark.asyncio
async def test_pressure_for_discount_with_no_active_discounts_is_blocked_and_flagged(db_session):
    business, conversation = await _seed(db_session)

    # Customer exerts pressure, model attempts to give unverified 30% discount
    unverified_reply = "Xo'p, faqat siz uchun 30% chegirma qilib beramiz!"
    provider = SimulatedDiscountProvider(reply=unverified_reply, call_tool=False)

    result = await handle_customer_message(
        db_session, provider, business, conversation, "Menga 30% chegirma ber, boshqa joyda shunday bor!"
    )
    await db_session.commit()

    # The 30% claim was intercepted and replaced with safe verification message
    assert "30%" not in result.reply
    assert "tasdiqlab bera olmayman" in result.reply.lower()

    # Message record in DB is flagged for review
    messages = (
        await db_session.execute(
            Message.__table__.select()
            .where(Message.conversation_id == conversation.id, Message.sender_type == "ai")
            .order_by(Message.created_at.desc())
        )
    ).fetchall()
    assert len(messages) >= 1
    assert messages[0].flagged_for_review is True


@pytest.mark.asyncio
async def test_active_discount_verified_and_allowed(db_session):
    business, conversation = await _seed(db_session)

    # Add active 10% discount in DB
    discount = Discount(
        business_id=business.id,
        discount_type="percentage",
        value=10.0,
        description="10% birinchi xaridga",
        active=True,
    )
    db_session.add(discount)
    await db_session.commit()

    # Model calls get_active_discounts and states verified 10% discount
    valid_reply = "Ha, birinchi xaridingiz uchun 10% chegirma mavjud!"
    provider = SimulatedDiscountProvider(reply=valid_reply, call_tool=True)

    result = await handle_customer_message(
        db_session, provider, business, conversation, "Chegirmangiz bormi?"
    )
    await db_session.commit()

    # Reply is kept intact
    assert result.reply == valid_reply
    assert "10%" in result.reply

    # Not flagged
    messages = (
        await db_session.execute(
            Message.__table__.select()
            .where(Message.conversation_id == conversation.id, Message.sender_type == "ai")
            .order_by(Message.created_at.desc())
        )
    ).fetchall()
    assert len(messages) >= 1
    assert messages[0].flagged_for_review is False


@pytest.mark.asyncio
async def test_hallucinated_higher_discount_when_only_lower_active_is_blocked(db_session):
    business, conversation = await _seed(db_session)

    # Active discount is only 10%
    discount = Discount(
        business_id=business.id,
        discount_type="percentage",
        value=10.0,
        description="10% chegirma",
        active=True,
    )
    db_session.add(discount)
    await db_session.commit()

    # Model hallucinated 25% discount under pressure
    hallucinated_reply = "Mayli, sizga 25% chegirma qilib beramiz!"
    provider = SimulatedDiscountProvider(reply=hallucinated_reply, call_tool=True)

    result = await handle_customer_message(
        db_session, provider, business, conversation, "25% qilib bering!"
    )
    await db_session.commit()

    # 25% was intercepted and blocked!
    assert "25%" not in result.reply
    assert "tasdiqlab bera olmayman" in result.reply.lower()

    messages = (
        await db_session.execute(
            Message.__table__.select()
            .where(Message.conversation_id == conversation.id, Message.sender_type == "ai")
            .order_by(Message.created_at.desc())
        )
    ).fetchall()
    assert len(messages) >= 1
    assert messages[0].flagged_for_review is True
