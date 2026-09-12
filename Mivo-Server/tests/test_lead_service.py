"""Phase 8 acceptance tests: deterministic, backend-only lead persistence driven
by the orchestrator's structured output and independent message extraction."""
import uuid

import pytest
from sqlalchemy import select

from app.ai.orchestrator import ConversationTurnResult
from app.auth.models import User
from app.businesses.models import Business
from app.conversations.models import Conversation
from app.conversations.service import add_message, get_or_create_conversation
from app.customers.models import Customer
from app.customers.service import get_or_create_customer
from app.leads.models import Lead
from app.leads.service import apply_qualification, capture_phone_without_ai_turn, mark_hot_notified
from app.products.models import Product

pytestmark = pytest.mark.asyncio


async def _seed(db_session):
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Lead Test Biz")
    db_session.add(business)
    await db_session.flush()
    customer = await get_or_create_customer(db_session, business.id, ig_scoped_id=f"ig-{uuid.uuid4().hex[:8]}")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await db_session.commit()
    return business, customer, conversation


def _result(**overrides) -> ConversationTurnResult:
    defaults = dict(reply="ok", lead_status="cold", lead_score=10, qualification_reason="reason")
    defaults.update(overrides)
    return ConversationTurnResult(**defaults)


async def test_new_lead_created_from_score_band(db_session) -> None:
    business, customer, conversation = await _seed(db_session)
    lead, became_hot = await apply_qualification(
        db_session, business.id, customer.id, conversation.id, _result(lead_score=50)
    )
    assert lead.status == "warm"
    assert became_hot is False


async def test_phone_detection_forces_hot_regardless_of_score(db_session) -> None:
    business, customer, conversation = await _seed(db_session)
    lead, became_hot = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=15, phone_detected="+998 90 123 45 67", qualification_reason="gave phone"),
        raw_message_text="+998 90 123 45 67",
    )
    assert lead.status == "hot"
    assert lead.phone == "+998901234567"
    assert became_hot is True
    # Customer phone is also synced
    await db_session.refresh(customer)
    assert customer.phone == "+998901234567"


async def test_phone_captured_independently_from_raw_message_text(db_session) -> None:
    """Phone number capture must NOT depend solely on LLM populating phone_detected."""
    business, customer, conversation = await _seed(db_session)
    lead, became_hot = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=20, phone_detected=None, qualification_reason="user provided phone in message"),
        raw_message_text="Mening raqamim: 90 123 45 67, bog'laning",
    )
    assert lead.status == "hot"
    assert lead.phone == "+998901234567"
    assert became_hot is True
    await db_session.refresh(customer)
    assert customer.phone == "+998901234567"


async def test_phone_captured_from_recent_messages_if_llm_omits_it(db_session) -> None:
    """If customer sent phone in a recent turn, it is extracted from recent conversation messages."""
    business, customer, conversation = await _seed(db_session)
    await add_message(db_session, conversation, sender_type="customer", content="Nomerim: 998931112233")
    await db_session.commit()

    lead, became_hot = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=30, phone_detected=None),
        raw_message_text="Qachon yetkazasiz?",
    )
    assert lead.status == "hot"
    assert lead.phone == "+998931112233"
    assert became_hot is True


async def test_model_proposed_phone_the_customer_never_typed_is_ignored(db_session) -> None:
    """The analyst can mistake a number the shop quoted (delivery hotline in
    the business settings, echoed in a reply) for the customer's. Only a
    number the customer actually typed can make a lead hot."""
    business, customer, conversation = await _seed(db_session)
    await add_message(db_session, conversation, sender_type="ai", content="Savol bo'lsa +998 71 200 00 00 ga qo'ng'iroq qiling")
    await db_session.commit()
    lead, became_hot = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=20, phone_detected="+998712000000"),
        raw_message_text="rahmat",
    )
    assert lead.phone is None
    assert lead.status == "cold"
    assert became_hot is False


async def test_interested_products_are_kept_across_turns_without_products(db_session) -> None:
    business, customer, conversation = await _seed(db_session)
    product = Product(business_id=business.id, name="Hoodie", currency="UZS", availability=True, attributes={}, source="manual")
    db_session.add(product)
    await db_session.commit()
    await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=50, interested_product_ids=[str(product.id)]),
    )
    lead, _ = await apply_qualification(
        db_session, business.id, customer.id, conversation.id, _result(lead_score=80), raw_message_text="90 123 45 67",
    )
    assert lead.interested_products == [{"id": str(product.id), "name": "Hoodie"}]


async def test_summaries_are_only_what_the_analyst_wrote(db_session) -> None:
    business, customer, conversation = await _seed(db_session)
    lead, _ = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=40, summary_uz="Qora hoodie so'radi", summary_ru=None, summary_en="Asked for a black hoodie"),
    )
    assert lead.summaries == {"uz": "Qora hoodie so'radi", "en": "Asked for a black hoodie"}


async def test_hallucinated_phone_text_is_ignored(db_session) -> None:
    business, customer, conversation = await _seed(db_session)
    lead, became_hot = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=90, phone_detected="not really a phone"),
    )
    # phone_detected didn't pass regex validation, so score band (90 -> hot) still applies,
    # but no phone is stored.
    assert lead.status == "hot"
    assert lead.phone == None
    assert became_hot is True


async def test_status_re_evaluates_fresh_on_later_turns_after_phone_captured(db_session) -> None:
    """The turn a phone is FIRST given is always hot, but status on every
    turn after that must track the model's current read of the conversation."""
    business, customer, conversation = await _seed(db_session)
    first_lead, became_hot = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=85, phone_detected="+998901234567"),
        raw_message_text="+998901234567",
    )
    assert became_hot is True
    await mark_hot_notified(db_session, first_lead)

    # A later turn with a low score (e.g. the customer cancelled)
    lead, became_hot_again = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=5, qualification_reason="customer cancelled the order"),
    )
    assert lead.status == "cold"
    assert lead.phone == "+998901234567"  # contact info is never erased
    assert became_hot_again is False  # already notified once; no double-fire

    # And if interest picks back up later, status rises to HOT again -> fresh HOT transition re-notifies
    lead, became_hot_third_time = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=80, qualification_reason="asked to reorder"),
    )
    assert lead.status == "hot"
    assert became_hot_third_time is True  # re-entering hot from cold triggers notification


async def test_hot_notified_at_gates_one_time_notification(db_session) -> None:
    business, customer, conversation = await _seed(db_session)
    lead, became_hot = await apply_qualification(
        db_session, business.id, customer.id, conversation.id, _result(lead_score=90)
    )
    assert became_hot is True
    assert lead.hot_notified_at is None

    await mark_hot_notified(db_session, lead)
    assert lead.hot_notified_at is not None

    # Subsequent HOT-qualifying turns must not report became_hot again.
    lead_again, became_hot_again = await apply_qualification(
        db_session, business.id, customer.id, conversation.id, _result(lead_score=95)
    )
    assert became_hot_again is False
    assert lead_again.hot_notified_at is not None


async def test_interested_product_ids_are_cross_checked_against_real_products(db_session) -> None:
    business, customer, conversation = await _seed(db_session)
    product = Product(
        business_id=business.id, name="Real Product", currency="UZS",
        availability=True, attributes={}, source="manual",
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.commit()

    fake_id = str(uuid.uuid4())
    lead, _ = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=50, interested_product_ids=[str(product.id), fake_id, "not-a-uuid"]),
    )
    assert len(lead.interested_products) == 1
    assert lead.interested_products[0]["id"] == str(product.id)
    assert lead.interested_products[0]["name"] == "Real Product"


async def test_one_lead_row_per_business_customer_pair(db_session) -> None:
    business, customer, conversation = await _seed(db_session)
    await apply_qualification(db_session, business.id, customer.id, conversation.id, _result(lead_score=20))
    lead2, _ = await apply_qualification(
        db_session, business.id, customer.id, conversation.id, _result(lead_score=40)
    )

    result = await db_session.execute(select(Lead).where(Lead.customer_id == customer.id))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "warm"


async def test_capture_phone_without_ai_turn_creates_a_hot_lead(db_session) -> None:
    business, customer, conversation = await _seed(db_session)

    lead, became_hot = await capture_phone_without_ai_turn(
        db_session, business.id, customer.id, conversation.id, "Mana raqamim: (90) 123-45-67"
    )

    assert became_hot is True
    assert lead.phone == "+998901234567"
    assert lead.status == "hot"
    await db_session.refresh(customer)
    assert customer.phone == "+998901234567"


async def test_capture_phone_without_ai_turn_ignores_non_phone_text(db_session) -> None:
    business, customer, conversation = await _seed(db_session)

    lead, became_hot = await capture_phone_without_ai_turn(
        db_session, business.id, customer.id, conversation.id, "Rahmat, o'ylab ko'raman"
    )

    assert lead is None
    assert became_hot is False


async def test_capture_phone_without_ai_turn_notifies_even_if_lead_was_already_hot(db_session) -> None:
    """Regression: a lead already hot-notified (no phone) whose customer then
    sent their number while a human was handling the chat never alerted the
    owner — the number is the most actionable thing a customer can send."""
    business, customer, conversation = await _seed(db_session)
    lead, _ = await apply_qualification(db_session, business.id, customer.id, conversation.id, _result(lead_score=90))
    await mark_hot_notified(db_session, lead)

    lead, notify = await capture_phone_without_ai_turn(
        db_session, business.id, customer.id, conversation.id, "90 123 45 67"
    )
    assert notify is True
    assert lead.phone == "+998901234567"


async def test_capture_phone_without_ai_turn_does_not_notify_twice(db_session) -> None:
    business, customer, conversation = await _seed(db_session)

    first_lead, became_hot_1 = await capture_phone_without_ai_turn(
        db_session, business.id, customer.id, conversation.id, "+998 90 123 45 67"
    )
    assert became_hot_1 is True
    assert first_lead.phone == "+998901234567"
    await mark_hot_notified(db_session, first_lead)

    # Same customer sends another phone-shaped message later — already on file
    second_lead, became_hot_2 = await capture_phone_without_ai_turn(
        db_session, business.id, customer.id, conversation.id, "+998907654321"
    )
    assert became_hot_2 is False
    assert second_lead.phone == "+998901234567"


async def test_capture_phone_without_ai_turn_does_not_overwrite_existing_qualification(db_session) -> None:
    business, customer, conversation = await _seed(db_session)
    await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        _result(lead_score=40, qualification_reason="Just browsing so far."),
    )

    lead, became_hot = await capture_phone_without_ai_turn(
        db_session, business.id, customer.id, conversation.id, "90.123.45.67"
    )

    assert became_hot is True
    assert lead.qualification_reason == "Just browsing so far."
    assert lead.phone == "+998901234567"


async def test_tenant_isolation_leads(db_session) -> None:
    """Ensure leads of Business A are isolated from Business B."""
    b1, c1, conv1 = await _seed(db_session)
    b2, c2, conv2 = await _seed(db_session)

    lead1, _ = await apply_qualification(
        db_session, b1.id, c1.id, conv1.id, _result(lead_score=75, phone_detected="901112233"),
        raw_message_text="901112233",
    )
    lead2, _ = await apply_qualification(db_session, b2.id, c2.id, conv2.id, _result(lead_score=30))

    assert lead1.business_id == b1.id
    assert lead2.business_id == b2.id
    assert lead1.phone == "+998901112233"
    assert lead2.phone is None

    # Querying leads for Business B returns only lead2
    b2_leads = (await db_session.execute(select(Lead).where(Lead.business_id == b2.id))).scalars().all()
    assert len(b2_leads) == 1
    assert b2_leads[0].id == lead2.id
