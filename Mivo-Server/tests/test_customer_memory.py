"""Tests for customer memory and durable facts (known_facts).
Ensures preferences outlive the recent message history window and FIFO capping works.
"""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context.builder import build_customer_profile_block
from app.ai.orchestrator import ConversationTurnResult, handle_customer_message
from app.ai.provider.base import LLMProvider
from app.auth.models import User
from app.businesses.models import Business
from app.conversations.models import Message
from app.conversations.service import add_message, get_or_create_conversation
from app.customers.service import get_or_create_customer
from app.leads.models import Lead
from app.leads.service import apply_qualification, merge_known_facts


def test_merge_known_facts_unit():
    # 1. Basic addition
    facts = merge_known_facts([], ["faqat qora rangda kerak", "39-40 razmer kiyadi"])
    assert len(facts) == 2
    assert facts[0]["text"] == "faqat qora rangda kerak"
    assert "noted_at" in facts[0]
    assert facts[1]["text"] == "39-40 razmer kiyadi"

    # 2. Duplicate rejection (exact match)
    facts2 = merge_known_facts(facts, ["faqat qora rangda kerak"])
    assert len(facts2) == 2

    # 3. Duplicate rejection (substring match)
    # "qora rangda kerak" is a substring of "faqat qora rangda kerak"
    facts3 = merge_known_facts(facts, ["qora rangda kerak"])
    assert len(facts3) == 2

    # 4. New fact added
    facts4 = merge_known_facts(facts, ["byudjeti ~200000 so'm"])
    assert len(facts4) == 3
    assert facts4[2]["text"] == "byudjeti ~200000 so'm"

    # 5. FIFO cap at 15 items
    large_list = [f"fakt {i}" for i in range(25)]
    capped = merge_known_facts([], large_list, max_facts=15)
    assert len(capped) == 15
    # Should keep the latest 15: fakt 10 through fakt 24
    assert capped[0]["text"] == "fakt 10"
    assert capped[-1]["text"] == "fakt 24"


async def _seed(db_session: AsyncSession):
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Memory Test Shop")
    db_session.add(business)
    await db_session.flush()
    customer = await get_or_create_customer(db_session, business.id, ig_scoped_id=f"ig-{uuid.uuid4()}")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await db_session.commit()
    return business, customer, conversation


class MockMemoryProvider(LLMProvider):
    def __init__(self, reply: str, extracted_facts: list[str] | None = None):
        self._reply = reply
        self._extracted_facts = extracted_facts

    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(self, *, system_prompt, messages, tools, response_schema, tool_executor, **kwargs):
        return ConversationTurnResult(
            reply=self._reply,
            lead_status="warm",
            lead_score=60,
            qualification_reason="Customer expressed specific preference.",
            extracted_facts=self._extracted_facts,
        )


@pytest.mark.asyncio
async def test_known_facts_preserved_across_history_window(db_session: AsyncSession):
    business, customer, conversation = await _seed(db_session)

    # Turn 1: Customer states "faqat qora rangda kerak"
    provider1 = MockMemoryProvider(
        reply="Tushunarli, faqat qora rangdagilarni ko'rsataman!",
        extracted_facts=["faqat qora rangda kerak", "39-40 razmer kiyadi"],
    )
    res1 = await handle_customer_message(
        db_session,
        provider1,
        business,
        conversation,
        "Menga faqat qora rangda kerak, razmerim 39-40",
        apply_lead_qualification=True,
    )
    await db_session.commit()

    # Verify lead has the facts
    lead = await db_session.scalar(
        Lead.__table__.select().where(Lead.business_id == business.id, Lead.customer_id == customer.id)
    )
    assert lead is not None
    assert len(lead.known_facts) == 2

    # Now simulate 14 subsequent messages (exceeding the 10-message recent history window)
    for i in range(14):
        await add_message(
            db_session,
            conversation,
            sender_type="customer" if i % 2 == 0 else "ai",
            content=f"Oddiy suhbat xabari {i}",
        )
    await db_session.commit()

    # Render customer profile block for the next turn
    profile_block = await build_customer_profile_block(db_session, business.id, customer.id)

    # The facts MUST be in the profile block despite the original message being aged out of the 10-message window!
    assert "Known facts & preferences:" in profile_block
    assert "faqat qora rangda kerak" in profile_block
    assert "39-40 razmer kiyadi" in profile_block


@pytest.mark.asyncio
async def test_known_facts_fifo_capping_in_db(db_session: AsyncSession):
    business, customer, conversation = await _seed(db_session)

    # Simulate 4 turns gradually adding 5 facts each = 20 total facts
    for turn_idx in range(4):
        new_facts = [f"afzallik_turn_{turn_idx}_element_{i}" for i in range(5)]
        turn_result = ConversationTurnResult(
            reply=f"Rahmat {turn_idx}",
            lead_status="warm",
            lead_score=50,
            qualification_reason="Testing facts accumulation",
            extracted_facts=new_facts,
        )
        await apply_qualification(
            db_session, business.id, customer.id, conversation.id, turn_result
        )

    await db_session.commit()

    lead = await db_session.scalar(
        Lead.__table__.select().where(Lead.business_id == business.id, Lead.customer_id == customer.id)
    )
    assert lead is not None
    # Must be capped at exactly 15
    assert len(lead.known_facts) == 15

    # Oldest facts (from turn 0) must have been evicted (FIFO)
    all_texts = [f["text"] for f in lead.known_facts]
    assert "afzallik_turn_0_element_0" not in all_texts
    assert "afzallik_turn_3_element_4" in all_texts
