"""Phase 10 acceptance tests: Telegram connect flow (deep link + /start) and
production-grade hot-lead notifications with retries and resilience."""
import datetime as dt
import uuid
import respx
import httpx
import pytest

from app.auth.models import User
from app.businesses.models import Business
from app.core.config import get_settings
from app.leads.models import Lead
from app.leads.notifications import notify_hot_lead
from app.leads.service import apply_qualification, mark_hot_notified
from app.ai.orchestrator import ConversationTurnResult
from app.telegram import service
from app.telegram.client import TelegramClient, TelegramAPIError
from app.telegram.models import TelegramConnection

pytestmark = pytest.mark.asyncio


class FakeTelegramClient:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str, parse_mode: str = "HTML") -> dict:
        self.sent.append((chat_id, text))
        return {"ok": True}


async def _seed_business(db_session) -> Business:
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Telegram Test Biz")
    db_session.add(business)
    await db_session.commit()
    return business


# --- connect flow -----------------------------------------------------------

async def test_create_connect_token_and_deep_link(db_session) -> None:
    business = await _seed_business(db_session)
    token, _expires_at = await service.create_connect_token(db_session, business.id)
    assert len(token) <= 64  # Telegram deep-link payload limit

    link = service.build_deep_link(token)
    assert link == f"https://t.me/{get_settings().telegram_bot_username}?start={token}"


async def test_start_command_connects_business(db_session) -> None:
    business = await _seed_business(db_session)
    token, _expires_at = await service.create_connect_token(db_session, business.id)

    ok = await service.handle_start_command(db_session, token, chat_id="12345", username="owner_tg")
    assert ok is True

    from sqlalchemy import select

    connection = (
        await db_session.execute(select(TelegramConnection).where(TelegramConnection.business_id == business.id))
    ).scalar_one()
    assert connection.telegram_chat_id == "12345"
    assert connection.telegram_username == "owner_tg"
    assert connection.connected_at is not None


async def test_start_command_rejects_unknown_token(db_session) -> None:
    ok = await service.handle_start_command(db_session, "nonexistent-token", "1", "x")
    assert ok is False


async def test_start_command_rejects_expired_token(db_session) -> None:
    business = await _seed_business(db_session)
    token, _expires_at = await service.create_connect_token(db_session, business.id)

    from sqlalchemy import select

    connection = (
        await db_session.execute(select(TelegramConnection).where(TelegramConnection.business_id == business.id))
    ).scalar_one()
    connection.connect_token_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    await db_session.commit()

    ok = await service.handle_start_command(db_session, token, "1", "x")
    assert ok is False


# --- notification & formatting -----------------------------------------------

async def test_notify_hot_lead_sends_formatted_message(db_session) -> None:
    business = await _seed_business(db_session)
    token, _expires_at = await service.create_connect_token(db_session, business.id)
    await service.handle_start_command(db_session, token, chat_id="999", username="owner")

    from app.conversations.service import get_or_create_conversation
    from app.customers.service import get_or_create_customer

    customer = await get_or_create_customer(db_session, business.id, "ig-cust-1", username="buyer_ig")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await db_session.commit()
    lead = Lead(
        business_id=business.id, customer_id=customer.id, conversation_id=conversation.id,
        status="hot", score=90, phone="+998901234567",
        interested_products=[{"id": str(uuid.uuid4()), "name": "Nike Hoodie"}],
        summary="Customer confirmed purchase intent.",
    )
    db_session.add(lead)
    await db_session.commit()

    fake_client = FakeTelegramClient()
    ok = await notify_hot_lead(db_session, business, lead, client=fake_client)

    assert ok is True
    assert len(fake_client.sent) == 1
    chat_id, text = fake_client.sent[0]
    assert chat_id == "999"
    assert "@buyer_ig" in text
    assert "+998901234567" in text
    assert "Nike Hoodie" in text
    assert "Customer confirmed purchase intent." in text
    assert "90/100" in text
    assert "Mivo Dashboard" in text


async def test_notify_hot_lead_skips_when_telegram_not_connected(db_session) -> None:
    business = await _seed_business(db_session)  # never connected

    from app.conversations.service import get_or_create_conversation
    from app.customers.service import get_or_create_customer

    customer = await get_or_create_customer(db_session, business.id, "ig-cust-2")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await db_session.commit()
    lead = Lead(
        business_id=business.id, customer_id=customer.id, conversation_id=conversation.id,
        status="hot", score=90, interested_products=[],
    )
    db_session.add(lead)
    await db_session.commit()

    fake_client = FakeTelegramClient()
    ok = await notify_hot_lead(db_session, business, lead, client=fake_client)

    assert ok is False
    assert fake_client.sent == []


async def test_notify_hot_lead_records_error_when_telegram_fails(db_session) -> None:
    business = await _seed_business(db_session)
    token, _ = await service.create_connect_token(db_session, business.id)
    await service.handle_start_command(db_session, token, chat_id="999", username="owner")

    from app.conversations.service import get_or_create_conversation
    from app.customers.service import get_or_create_customer

    customer = await get_or_create_customer(db_session, business.id, "ig-cust-err")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    lead = Lead(
        business_id=business.id, customer_id=customer.id, conversation_id=conversation.id,
        status="hot", score=85,
    )
    db_session.add(lead)
    await db_session.commit()

    class FailingTelegramClient:
        async def send_message(self, *args, **kwargs):
            raise TelegramAPIError("Forbidden: bot was blocked by the user")

    ok = await notify_hot_lead(db_session, business, lead, client=FailingTelegramClient())
    assert ok is False
    assert lead.last_notification_error is not None
    assert "Forbidden" in lead.last_notification_error


async def test_notification_sanitizes_internal_ai_reasoning(db_session) -> None:
    business = await _seed_business(db_session)
    token, _ = await service.create_connect_token(db_session, business.id)
    await service.handle_start_command(db_session, token, chat_id="999", username="owner")

    from app.conversations.service import get_or_create_conversation
    from app.customers.service import get_or_create_customer

    customer = await get_or_create_customer(db_session, business.id, "ig-cust-leak")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    lead = Lead(
        business_id=business.id, customer_id=customer.id, conversation_id=conversation.id,
        status="hot", score=85,
        summary="<think>User is asking about price, I should recommend L size</think> Mijoz kiyim sotib olmoqchi.",
    )
    db_session.add(lead)
    await db_session.commit()

    fake_client = FakeTelegramClient()
    await notify_hot_lead(db_session, business, lead, client=fake_client)

    _, text = fake_client.sent[0]
    assert "<think>" not in text
    assert "User is asking about price" not in text
    assert "Mijoz kiyim sotib olmoqchi." in text


# --- retry & state machine tests --------------------------------------------

@respx.mock
async def test_telegram_client_retries_transient_error_and_succeeds() -> None:
    client = TelegramClient(token="test-token")
    route = respx.post("https://api.telegram.org/bottest-token/sendMessage").mock(
        side_effect=[
            httpx.Response(500, text="Internal Server Error"),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 101}}),
        ]
    )
    res = await client.send_message(chat_id="123", text="Hello", backoff_factor=0.01)
    assert res["ok"] is True
    assert route.call_count == 2


@respx.mock
async def test_telegram_client_fails_fast_on_4xx_without_endless_retry() -> None:
    client = TelegramClient(token="test-token")
    route = respx.post("https://api.telegram.org/bottest-token/sendMessage").mock(
        return_value=httpx.Response(400, text='{"ok":false,"description":"Bad Request: chat not found"}')
    )
    with pytest.raises(TelegramAPIError) as exc_info:
        await client.send_message(chat_id="123", text="Hello")
    assert "400" in str(exc_info.value)
    assert route.call_count == 1  # only called once, no wasteful retries


async def test_lead_remains_hot_does_not_re_notify(db_session) -> None:
    business = await _seed_business(db_session)
    from app.conversations.service import get_or_create_conversation
    from app.customers.service import get_or_create_customer

    customer = await get_or_create_customer(db_session, business.id, "cust-stay-hot")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await db_session.commit()

    # Turn 1: becomes HOT
    lead1, became_hot1 = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        ConversationTurnResult(reply="ok", lead_status="hot", lead_score=85, qualification_reason="hot"),
    )
    assert became_hot1 is True
    await mark_hot_notified(db_session, lead1)

    # Turn 2: still HOT (remains hot) -> must NOT notify again
    lead2, became_hot2 = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        ConversationTurnResult(reply="ok", lead_status="hot", lead_score=90, qualification_reason="still hot"),
    )
    assert became_hot2 is False


async def test_hot_to_warm_to_hot_re_notifies_deterministically(db_session) -> None:
    business = await _seed_business(db_session)
    from app.conversations.service import get_or_create_conversation
    from app.customers.service import get_or_create_customer

    customer = await get_or_create_customer(db_session, business.id, "cust-cooling-warming")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    await db_session.commit()

    # 1. Becomes HOT
    lead, became_hot_1 = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        ConversationTurnResult(reply="ok", lead_status="hot", lead_score=80, qualification_reason="interested"),
    )
    assert became_hot_1 is True
    await mark_hot_notified(db_session, lead)

    # 2. Cools down to warm
    lead, became_hot_2 = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        ConversationTurnResult(reply="ok", lead_status="warm", lead_score=45, qualification_reason="undecided"),
    )
    assert lead.status == "warm"
    assert became_hot_2 is False

    # 3. Warms up to HOT again -> fresh HOT transition triggers re-notification!
    lead, became_hot_3 = await apply_qualification(
        db_session, business.id, customer.id, conversation.id,
        ConversationTurnResult(reply="ok", lead_status="hot", lead_score=85, qualification_reason="ready to buy"),
    )
    assert lead.status == "hot"
    assert became_hot_3 is True
