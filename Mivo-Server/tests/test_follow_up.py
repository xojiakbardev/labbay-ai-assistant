"""Smart follow-up (app/ai/follow_up.py): who gets one, when, and only once."""
import datetime as dt
import uuid

import pytest
from sqlalchemy import select, update

from app.ai.follow_up import sweep_inactive_leads_follow_up
from app.auth.models import User
from app.businesses.models import Business
from app.conversations.models import Conversation, Message
from app.core.security import encrypt_secret
from app.customers.service import get_or_create_customer
from app.conversations.service import get_or_create_conversation
from app.instagram.client import MetaAPIError
from app.instagram.models import InstagramAccount
from app.leads.models import Lead

pytestmark = pytest.mark.asyncio


class FakeMeta:
    def __init__(self, fail: bool = False):
        self.sent: list[str] = []
        self.fail = fail

    async def send_message(self, *, ig_business_id, access_token, recipient_id, text):
        if self.fail:
            raise MetaAPIError("window closed")
        self.sent.append(text)
        return {"message_id": f"fu-{uuid.uuid4()}"}


async def _seed(db_session, *, customer_ago: dt.timedelta, ai_ago: dt.timedelta, lead_status="warm", language=None):
    now = dt.datetime.now(dt.timezone.utc)
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="FU Biz", language=language)
    db_session.add(business)
    await db_session.flush()
    db_session.add(
        InstagramAccount(
            business_id=business.id, ig_business_id=f"ig-{uuid.uuid4().hex[:8]}", fb_page_id="p",
            access_token_encrypted=encrypt_secret("tok"), status="connected", connected_at=now,
        )
    )
    customer = await get_or_create_customer(db_session, business.id, f"c-{uuid.uuid4().hex[:8]}")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    db_session.add_all([
        Message(conversation_id=conversation.id, sender_type="customer", content="Hoodie narxi?", created_at=now - customer_ago),
        Message(conversation_id=conversation.id, sender_type="ai", content="319 000 so'm.", created_at=now - ai_ago,
                delivery_status="sent"),
    ])
    conversation.last_message_at = now - ai_ago
    db_session.add(Lead(business_id=business.id, customer_id=customer.id, conversation_id=conversation.id,
                        status=lead_status, score=50, interested_products=[{"id": str(uuid.uuid4()), "name": "Hoodie"}]))
    await db_session.commit()
    return business, conversation


async def test_quiet_warm_lead_gets_one_follow_up_persisted_and_sent(db_session) -> None:
    _business, conversation = await _seed(db_session, customer_ago=dt.timedelta(hours=2), ai_ago=dt.timedelta(hours=1))
    meta = FakeMeta()

    assert await sweep_inactive_leads_follow_up(db_session, meta) == 1
    assert len(meta.sent) == 1 and "Hoodie" in meta.sent[0]
    last = (await db_session.execute(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at.desc())
        .execution_options(populate_existing=True)
    )).scalars().first()
    assert last.sender_type == "ai" and last.delivery_status == "sent" and last.external_message_id.startswith("fu-")

    # A second sweep (or a second process) sends nothing more.
    assert await sweep_inactive_leads_follow_up(db_session, meta) == 0
    assert len(meta.sent) == 1


async def test_no_follow_up_outside_the_24h_messaging_window(db_session) -> None:
    """Regression: the window was measured from the AI's last message, so a
    customer who wrote 30 hours ago could still be messaged — a Meta policy
    violation that gets the app restricted."""
    await _seed(db_session, customer_ago=dt.timedelta(hours=30), ai_ago=dt.timedelta(hours=2))
    meta = FakeMeta()
    assert await sweep_inactive_leads_follow_up(db_session, meta) == 0
    assert meta.sent == []


@pytest.mark.parametrize("switch", ["ai_enabled_off", "ai_suspended", "expired"])
async def test_no_follow_up_when_the_business_ai_may_not_reply(db_session, switch) -> None:
    business, _ = await _seed(db_session, customer_ago=dt.timedelta(hours=2), ai_ago=dt.timedelta(hours=1))
    values = {
        "ai_enabled_off": {"ai_enabled": False},
        "ai_suspended": {"ai_suspended": True},
        "expired": {"subscription_expires_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)},
    }[switch]
    await db_session.execute(update(Business).where(Business.id == business.id).values(**values))
    await db_session.commit()
    meta = FakeMeta()
    assert await sweep_inactive_leads_follow_up(db_session, meta) == 0
    assert meta.sent == []


async def test_no_follow_up_for_cold_leads_or_human_conversations(db_session) -> None:
    await _seed(db_session, customer_ago=dt.timedelta(hours=2), ai_ago=dt.timedelta(hours=1), lead_status="cold")
    _b, conversation = await _seed(db_session, customer_ago=dt.timedelta(hours=2), ai_ago=dt.timedelta(hours=1))
    await db_session.execute(update(Conversation).where(Conversation.id == conversation.id).values(status="human_active"))
    await db_session.commit()
    meta = FakeMeta()
    assert await sweep_inactive_leads_follow_up(db_session, meta) == 0


async def test_follow_up_speaks_the_customers_language(db_session) -> None:
    _b, conversation = await _seed(db_session, customer_ago=dt.timedelta(hours=2), ai_ago=dt.timedelta(hours=1))
    await db_session.execute(
        update(Message).where(Message.conversation_id == conversation.id, Message.sender_type == "customer")
        .values(content="Сколько стоит худи?")
    )
    await db_session.commit()
    meta = FakeMeta()
    await sweep_inactive_leads_follow_up(db_session, meta)
    assert meta.sent and meta.sent[0].startswith("Здравствуйте")


async def test_failed_follow_up_stays_recorded_as_failed_and_is_not_retried_by_the_sweep(db_session) -> None:
    _b, conversation = await _seed(db_session, customer_ago=dt.timedelta(hours=2), ai_ago=dt.timedelta(hours=1))
    assert await sweep_inactive_leads_follow_up(db_session, FakeMeta(fail=True)) == 0
    failed = (await db_session.execute(
        select(Message).where(Message.conversation_id == conversation.id, Message.delivery_status == "failed")
        .execution_options(populate_existing=True)
    )).scalars().all()
    assert len(failed) == 1
    retry_meta = FakeMeta()
    assert await sweep_inactive_leads_follow_up(db_session, retry_meta) == 0
    assert retry_meta.sent == []
