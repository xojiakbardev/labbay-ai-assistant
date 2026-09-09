"""Phase 9 acceptance tests: Instagram webhook -> AI -> lead -> reply pipeline,
idempotency, and tenant mapping (plan §11)."""
import asyncio
import datetime as dt
import uuid

import pytest

from app.ai.orchestrator import ConversationTurnResult
from app.ai.provider.base import LLMProvider
from app.auth.models import User
from app.businesses.models import Business
from app.instagram.models import InstagramAccount
from app.instagram.service import process_incoming_message
from app.leads.models import Lead
from app.products.models import Product

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_debounce(monkeypatch):
    """Production waits a few seconds before replying so a burst of rapid
    customer messages gets one combined reply instead of one per message
    (see app/instagram/service.py:DEBOUNCE_SECONDS) — tests don't need to pay
    that wall-clock cost."""
    import app.instagram.service as service_module

    monkeypatch.setattr(service_module, "DEBOUNCE_SECONDS", 0)


class FakeProvider(LLMProvider):
    def __init__(self, result: ConversationTurnResult):
        self._result = result
        self.calls = 0

    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(self, **kwargs):
        self.calls += 1
        return self._result


class FakeMetaClient:
    def __init__(self):
        self.sent: list[dict] = []
        self.sent_images: list[str] = []

    async def send_message(self, *, ig_business_id, access_token, recipient_id, text):
        self.sent.append(
            {"ig_business_id": ig_business_id, "access_token": access_token, "recipient_id": recipient_id, "text": text}
        )

    async def send_image(self, *, ig_business_id, access_token, recipient_id, image_url):
        self.sent_images.append(image_url)


async def _seed_connected_business(db_session):
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="IG Shop")
    db_session.add(business)
    await db_session.flush()

    from app.core.security import encrypt_secret

    account = InstagramAccount(
        business_id=business.id,
        ig_business_id="ig-biz-1",
        ig_username="ig_shop",
        fb_page_id="page-1",
        access_token_encrypted=encrypt_secret("real-token"),
        status="connected",
        connected_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(account)
    await db_session.commit()
    return business, account


async def test_incoming_message_creates_customer_conversation_and_replies(db_session) -> None:
    business, account = await _seed_connected_business(db_session)
    provider = FakeProvider(
        ConversationTurnResult(
            reply="Salom! Yordam bera olamanmi?", lead_status="cold", lead_score=5,
            qualification_reason="greeting",
        )
    )
    meta_client = FakeMetaClient()

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="customer-igsid-1",
        message_text="Salom", external_message_id="mid-1",
    )

    assert provider.calls == 1
    assert len(meta_client.sent) == 1
    assert meta_client.sent[0]["text"] == "Salom! Yordam bera olamanmi?"
    assert meta_client.sent[0]["access_token"] == "real-token"
    assert meta_client.sent[0]["recipient_id"] == "customer-igsid-1"


async def test_expired_subscription_skips_ai_reply_but_keeps_message(db_session) -> None:
    business, account = await _seed_connected_business(db_session)
    business.subscription_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    await db_session.commit()

    provider = FakeProvider(
        ConversationTurnResult(reply="should not be used", lead_status="cold", lead_score=5, qualification_reason="x")
    )
    meta_client = FakeMetaClient()

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="customer-igsid-expired",
        message_text="Salom", external_message_id="mid-expired",
    )

    assert provider.calls == 0  # no LLM call — cost control, not just UX
    assert meta_client.sent == []  # no auto-reply sent

    from app.conversations.models import Conversation, Message
    from sqlalchemy import select

    conversation = await db_session.scalar(select(Conversation).where(Conversation.business_id == business.id))
    messages = (await db_session.execute(select(Message).where(Message.conversation_id == conversation.id))).scalars().all()
    assert len(messages) == 1  # customer's message is still recorded for the owner to see
    assert messages[0].content == "Salom"


async def test_ai_disabled_skips_auto_reply(db_session) -> None:
    business, account = await _seed_connected_business(db_session)
    business.ai_enabled = False
    await db_session.commit()

    provider = FakeProvider(
        ConversationTurnResult(reply="should not be used", lead_status="cold", lead_score=5, qualification_reason="x")
    )
    meta_client = FakeMetaClient()

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="customer-igsid-off",
        message_text="Salom", external_message_id="mid-off",
    )

    assert provider.calls == 0
    assert meta_client.sent == []


async def test_phone_number_still_captured_when_ai_wont_reply(db_session) -> None:
    """Regression: a conversation already handed to a human (human_needed)
    got a real phone number typed into it right after — since no AI turn
    ever runs for a paused conversation, that number was silently never
    recorded as a lead. See app/leads/service.py:capture_phone_without_ai_turn."""
    business, account = await _seed_connected_business(db_session)

    from app.conversations.service import get_or_create_conversation, set_status
    from app.customers.service import get_or_create_customer

    customer = await get_or_create_customer(db_session, business.id, "phone-while-paused")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await set_status(db_session, conversation, "human_needed")
    await db_session.commit()

    provider = FakeProvider(
        ConversationTurnResult(reply="should not be used", lead_status="cold", lead_score=5, qualification_reason="x")
    )
    meta_client = FakeMetaClient()

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="phone-while-paused",
        message_text="+998901234567", external_message_id="mid-phone-paused",
    )

    assert provider.calls == 0  # still no AI reply — conversation stays paused
    assert meta_client.sent == []

    from sqlalchemy import select

    from app.leads.models import Lead

    lead = (
        await db_session.execute(select(Lead).where(Lead.customer_id == customer.id))
    ).scalar_one()
    assert lead.phone == "+998901234567"
    assert lead.status == "hot"


async def test_duplicate_webhook_delivery_is_not_reprocessed(db_session) -> None:
    business, account = await _seed_connected_business(db_session)
    provider = FakeProvider(
        ConversationTurnResult(reply="ok", lead_status="cold", lead_score=5, qualification_reason="x")
    )
    meta_client = FakeMetaClient()

    kwargs = dict(
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="customer-igsid-2",
        message_text="hi", external_message_id="mid-dup",
    )
    await process_incoming_message(db_session, provider, meta_client, **kwargs)
    await process_incoming_message(db_session, provider, meta_client, **kwargs)  # Meta redelivers

    assert provider.calls == 1  # not re-run
    assert len(meta_client.sent) == 1  # not re-sent


async def test_unknown_instagram_account_is_ignored(db_session) -> None:
    provider = FakeProvider(
        ConversationTurnResult(reply="ok", lead_status="cold", lead_score=5, qualification_reason="x")
    )
    meta_client = FakeMetaClient()

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="unknown-account", customer_ig_scoped_id="customer-x",
        message_text="hi", external_message_id="mid-unknown",
    )

    assert provider.calls == 0
    assert meta_client.sent == []


async def test_hot_lead_triggers_notification_hook(db_session, monkeypatch) -> None:
    business, account = await _seed_connected_business(db_session)
    product = Product(
        business_id=business.id, name="Nike Hoodie", currency="UZS",
        availability=True, attributes={}, source="manual",
    )
    db_session.add(product)
    await db_session.commit()

    provider = FakeProvider(
        ConversationTurnResult(
            reply="Telefon raqamingizni qoldiring", lead_status="hot", lead_score=90,
            qualification_reason="ready to buy", phone_detected="+998901234567",
            interested_product_ids=[str(product.id)],
        )
    )
    meta_client = FakeMetaClient()

    notified = []

    async def fake_notify(db, business_, lead):
        notified.append(lead.id)

    import app.instagram.service as service_module

    monkeypatch.setattr(service_module, "notify_hot_lead", fake_notify)

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="customer-hot",
        message_text="+998901234567", external_message_id="mid-hot",
    )

    assert len(notified) == 1
    from sqlalchemy import select

    lead = (await db_session.execute(select(Lead).where(Lead.id == notified[0]))).scalar_one()
    assert lead.status == "hot"
    assert lead.hot_notified_at is not None  # marked notified after the hook ran


async def test_failed_delivery_is_marked_failed_and_can_be_retried(db_session) -> None:
    """If processing fails after the dedup row is written (e.g. Instagram send
    fails), a Meta retry must not be silently swallowed as a duplicate —
    Phase 13 hardening for the bug where 'failed' used to look identical to
    'already handled'."""
    business, account = await _seed_connected_business(db_session)
    provider = FakeProvider(
        ConversationTurnResult(reply="ok", lead_status="cold", lead_score=5, qualification_reason="x")
    )

    class BrokenMetaClient:
        async def send_message(self, **kwargs):
            raise RuntimeError("simulated Instagram send failure")

    kwargs = dict(
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="customer-retry",
        message_text="hi", external_message_id="mid-retry-1",
    )

    with pytest.raises(RuntimeError):
        await process_incoming_message(db_session, provider, BrokenMetaClient(), **kwargs)

    from sqlalchemy import select

    from app.webhooks.models import WebhookEvent

    event = (
        await db_session.execute(select(WebhookEvent).where(WebhookEvent.external_event_id == "mid-retry-1"))
    ).scalar_one()
    assert event.status == "failed"

    # Meta redelivers the same webhook after the failure — it must actually retry,
    # not be dropped as a duplicate.
    working_meta_client = FakeMetaClient()
    await process_incoming_message(db_session, provider, working_meta_client, **kwargs)

    assert len(working_meta_client.sent) == 1
    event_after = (
        await db_session.execute(select(WebhookEvent).where(WebhookEvent.external_event_id == "mid-retry-1"))
    ).scalar_one()
    assert event_after.status == "processed"

    # The customer's inbound message was not duplicated across the failed + retried attempts.
    from app.conversations.models import Message

    customer_messages = (
        await db_session.execute(
            select(Message).where(
                Message.external_message_id == "mid-retry-1", Message.sender_type == "customer"
            )
        )
    ).scalars().all()
    assert len(customer_messages) == 1


async def test_processed_event_is_not_reprocessed_on_redelivery(db_session) -> None:
    business, account = await _seed_connected_business(db_session)
    provider = FakeProvider(
        ConversationTurnResult(reply="ok", lead_status="cold", lead_score=5, qualification_reason="x")
    )
    meta_client = FakeMetaClient()
    kwargs = dict(
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="customer-final",
        message_text="hi", external_message_id="mid-final-1",
    )
    await process_incoming_message(db_session, provider, meta_client, **kwargs)
    await process_incoming_message(db_session, provider, meta_client, **kwargs)

    assert provider.calls == 1
    assert len(meta_client.sent) == 1


async def test_burst_of_rapid_messages_gets_one_combined_reply(db_session, monkeypatch) -> None:
    """A real person often fires off several short DMs a second or two apart
    ("Salom", "poyabzal bormi", "42 razmer bormi") — replying to each one
    separately is what the "juda yomon" / robotic-feeling bug reports were
    about. The later message must be the one that answers, covering the
    whole burst in a single reply, not two overlapping ones."""
    import app.instagram.service as service_module

    monkeypatch.setattr(service_module, "DEBOUNCE_SECONDS", 0.15)

    business, account = await _seed_connected_business(db_session)
    provider = FakeProvider(
        ConversationTurnResult(reply="ok", lead_status="cold", lead_score=5, qualification_reason="x")
    )
    meta_client = FakeMetaClient()

    first = asyncio.create_task(
        process_incoming_message(
            db_session, provider, meta_client,
            ig_recipient_id="ig-biz-1", customer_ig_scoped_id="burst-cust",
            message_text="Salom", external_message_id="burst-1",
        )
    )
    await asyncio.sleep(0.05)  # let it persist message #1 and enter its debounce wait
    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="burst-cust",
        message_text="42 razmer bormi", external_message_id="burst-2",
    )
    await first

    assert provider.calls == 1  # the first message's wait saw a newer one and stood down
    assert len(meta_client.sent) == 1

    from app.conversations.models import Message
    from app.conversations.service import get_or_create_conversation
    from app.customers.service import get_or_create_customer
    from sqlalchemy import select

    customer = await get_or_create_customer(db_session, business.id, "burst-cust")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    customer_messages = (
        await db_session.execute(
            select(Message).where(
                Message.conversation_id == conversation.id, Message.sender_type == "customer"
            )
        )
    ).scalars().all()
    assert len(customer_messages) == 2  # both inbound messages were still recorded


async def test_ai_can_send_a_product_photo_alongside_the_text_reply(db_session) -> None:
    business, account = await _seed_connected_business(db_session)
    product = Product(
        business_id=business.id, name="Nike Hoodie", currency="UZS",
        availability=True, attributes={"image_url": "https://example.com/hoodie.jpg"}, source="manual",
    )
    db_session.add(product)
    await db_session.commit()

    provider = FakeProvider(
        ConversationTurnResult(
            reply="Mana, ko'ring:", lead_status="warm", lead_score=40,
            qualification_reason="asked for a photo", image_product_ids=[str(product.id)],
        )
    )
    meta_client = FakeMetaClient()

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="photo-cust",
        message_text="rasmini ko'rsating", external_message_id="mid-photo-1",
    )

    assert meta_client.sent[0]["text"] == "Mana, ko'ring:"
    assert meta_client.sent_images == ["https://example.com/hoodie.jpg"]


async def test_invented_or_foreign_product_id_never_sends_a_photo(db_session) -> None:
    """The model only ever names an id — this is the backend boundary that
    must not trust it blindly (plan §9/§10's "never invent" applies to media
    the same way it applies to prices and stock)."""
    business, account = await _seed_connected_business(db_session)
    other_business, _ = await _seed_connected_business(db_session)
    foreign_product = Product(
        business_id=other_business.id, name="Someone Else's Product", currency="UZS",
        availability=True, attributes={"image_url": "https://example.com/foreign.jpg"}, source="manual",
    )
    db_session.add(foreign_product)
    await db_session.commit()

    provider = FakeProvider(
        ConversationTurnResult(
            reply="ok", lead_status="cold", lead_score=5, qualification_reason="x",
            image_product_ids=["not-a-uuid", str(foreign_product.id)],
        )
    )
    meta_client = FakeMetaClient()

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="photo-cust-2",
        message_text="hi", external_message_id="mid-photo-2",
    )

    assert meta_client.sent_images == []


async def test_human_needed_conversation_gets_no_ai_reply(db_session) -> None:
    """Once a conversation is handed to a human (or an AI turn escalated it
    itself), the AI must stay silent on new messages until it's handed back —
    it shouldn't keep auto-replying underneath the operator."""
    business, account = await _seed_connected_business(db_session)

    from app.conversations.service import get_or_create_conversation, set_status
    from app.customers.service import get_or_create_customer

    customer = await get_or_create_customer(db_session, business.id, "human-mode-cust")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await set_status(db_session, conversation, "human_needed")
    await db_session.commit()

    provider = FakeProvider(
        ConversationTurnResult(reply="should not be used", lead_status="cold", lead_score=5, qualification_reason="x")
    )
    meta_client = FakeMetaClient()

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="human-mode-cust",
        message_text="Hali javob kutyapman", external_message_id="mid-human-mode",
    )

    assert provider.calls == 0
    assert meta_client.sent == []

    from app.conversations.models import Message
    from sqlalchemy import select

    messages = (
        await db_session.execute(select(Message).where(Message.conversation_id == conversation.id))
    ).scalars().all()
    assert len(messages) == 1  # still recorded for the owner to see
    assert messages[0].content == "Hali javob kutyapman"


async def test_phone_captured_while_ai_disabled_creates_hot_lead(db_session, monkeypatch) -> None:
    business, account = await _seed_connected_business(db_session)
    business.ai_enabled = False
    await db_session.commit()

    notified = []

    async def fake_notify(db, business_, lead):
        notified.append(lead.id)

    import app.instagram.service as service_module
    monkeypatch.setattr(service_module, "notify_hot_lead", fake_notify)

    provider = FakeProvider(
        ConversationTurnResult(reply="should not be used", lead_status="cold", lead_score=5, qualification_reason="x")
    )
    meta_client = FakeMetaClient()

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="disabled-ai-cust",
        message_text="Mana raqamim: +998 90 123 45 67", external_message_id="mid-ai-disabled-phone",
    )

    assert provider.calls == 0
    assert meta_client.sent == []
    assert len(notified) == 1

    from sqlalchemy import select
    from app.customers.models import Customer

    lead = (await db_session.execute(select(Lead).where(Lead.id == notified[0]))).scalar_one()
    assert lead.status == "hot"
    assert lead.phone == "+998901234567"
    assert lead.hot_notified_at is not None

    customer = (await db_session.execute(select(Customer).where(Customer.ig_scoped_id == "disabled-ai-cust"))).scalar_one()
    assert customer.phone == "+998901234567"


async def test_phone_captured_while_conversation_human_needed(db_session, monkeypatch) -> None:
    business, account = await _seed_connected_business(db_session)
    from app.conversations.service import get_or_create_conversation, set_status
    from app.customers.service import get_or_create_customer

    customer = await get_or_create_customer(db_session, business.id, "human-needed-phone-cust")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await set_status(db_session, conversation, "human_needed")
    await db_session.commit()

    notified = []

    async def fake_notify(db, business_, lead):
        notified.append(lead.id)

    import app.instagram.service as service_module
    monkeypatch.setattr(service_module, "notify_hot_lead", fake_notify)

    provider = FakeProvider(
        ConversationTurnResult(reply="should not be used", lead_status="cold", lead_score=5, qualification_reason="x")
    )
    meta_client = FakeMetaClient()

    await process_incoming_message(
        db_session, provider, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="human-needed-phone-cust",
        message_text="Nomerim: 90 123 45 67", external_message_id="mid-human-phone",
    )

    assert provider.calls == 0
    assert len(notified) == 1

    from sqlalchemy import select

    lead = (await db_session.execute(select(Lead).where(Lead.id == notified[0]))).scalar_one()
    assert lead.status == "hot"
    assert lead.phone == "+998901234567"


async def test_same_customer_multiple_messages_lead_recalculated_fresh(db_session, monkeypatch) -> None:
    business, account = await _seed_connected_business(db_session)
    notified = []

    async def fake_notify(db, business_, lead):
        notified.append(lead.id)

    import app.instagram.service as service_module
    monkeypatch.setattr(service_module, "notify_hot_lead", fake_notify)

    meta_client = FakeMetaClient()

    # Message 1: browsing (warm)
    p1 = FakeProvider(ConversationTurnResult(reply="Assalomu alaykum!", lead_status="warm", lead_score=45, qualification_reason="interested in catalog"))
    await process_incoming_message(
        db_session, p1, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="multi-msg-cust",
        message_text="Katalog bormi?", external_message_id="mid-m1",
    )
    from sqlalchemy import select
    from app.customers.models import Customer
    customer = (await db_session.execute(select(Customer).where(Customer.ig_scoped_id == "multi-msg-cust"))).scalar_one()
    lead = (await db_session.execute(select(Lead).where(Lead.customer_id == customer.id))).scalar_one()
    assert lead.status == "warm"
    assert lead.phone is None
    assert len(notified) == 0

    # Message 2: gives phone -> becomes HOT, notifies
    p2 = FakeProvider(ConversationTurnResult(reply="Rahmat!", lead_status="hot", lead_score=85, qualification_reason="gave phone"))
    await process_incoming_message(
        db_session, p2, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="multi-msg-cust",
        message_text="90 123 45 67", external_message_id="mid-m2",
    )
    await db_session.refresh(lead)
    assert lead.status == "hot"
    assert lead.phone == "+998901234567"
    assert len(notified) == 1

    # Message 3: cools off -> status becomes cold, phone is preserved, no double notification
    p3 = FakeProvider(ConversationTurnResult(reply="Tushunarli", lead_status="cold", lead_score=10, qualification_reason="cancelled order"))
    await process_incoming_message(
        db_session, p3, meta_client,
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="multi-msg-cust",
        message_text="Bekor qilmoqchiman", external_message_id="mid-m3",
    )
    await db_session.refresh(lead)
    assert lead.status == "cold"
    assert lead.phone == "+998901234567"
    assert len(notified) == 1  # still only 1 notification


async def test_duplicate_webhook_does_not_create_duplicate_leads_or_double_notify(db_session, monkeypatch) -> None:
    business, account = await _seed_connected_business(db_session)
    notified = []

    async def fake_notify(db, business_, lead):
        notified.append(lead.id)

    import app.instagram.service as service_module
    monkeypatch.setattr(service_module, "notify_hot_lead", fake_notify)

    provider = FakeProvider(
        ConversationTurnResult(reply="Rahmat!", lead_status="hot", lead_score=90, qualification_reason="hot lead", phone_detected="+998901234567")
    )
    meta_client = FakeMetaClient()

    kwargs = dict(
        ig_recipient_id="ig-biz-1", customer_ig_scoped_id="dup-lead-cust",
        message_text="+998901234567", external_message_id="mid-dup-lead",
    )
    await process_incoming_message(db_session, provider, meta_client, **kwargs)
    await process_incoming_message(db_session, provider, meta_client, **kwargs)

    assert len(notified) == 1

    from sqlalchemy import select
    from app.customers.models import Customer
    customer = (await db_session.execute(select(Customer).where(Customer.ig_scoped_id == "dup-lead-cust"))).scalar_one()
    leads = (await db_session.execute(select(Lead).where(Lead.customer_id == customer.id))).scalars().all()
    assert len(leads) == 1

