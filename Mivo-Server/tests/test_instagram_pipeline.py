"""Instagram webhook -> AI -> lead -> reply pipeline (app/instagram/pipeline.py):
routing, idempotency, the outbox, retries, echoes, handoffs and spend guards."""
import asyncio
import datetime as dt
import uuid

import pytest
from sqlalchemy import select, update

import app.instagram.pipeline as pipeline
from app.ai.closing import ClosingDecision
from app.ai.orchestrator import ConversationTurnResult
from app.ai.provider.base import LLMProvider, LLMProviderError
from app.auth.models import User
from app.businesses.models import Business
from app.conversations.models import Conversation, Message
from app.core.security import encrypt_secret
from app.customers.models import Customer
from app.instagram.client import MetaAPIError
from app.instagram.models import InstagramAccount
from app.leads.models import Lead
from app.products.models import Product
from app.webhooks.models import WebhookEvent

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """No debounce / part delays in tests; owner alerts recorded, not sent."""
    import app.conversations.delivery as delivery

    monkeypatch.setattr(pipeline, "DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(delivery, "PART_DELAY_SECONDS", 0)


@pytest.fixture
def alerts(monkeypatch):
    recorded: dict[str, list] = {"hot": [], "owner": []}

    async def fake_hot(db, business, customer, lead):
        recorded["hot"].append(lead.id)
        from app.leads.service import mark_hot_notified

        await mark_hot_notified(db, lead)

    async def fake_owner(db, business, **kwargs):
        recorded["owner"].append(kwargs)

    monkeypatch.setattr(pipeline, "alert_hot_lead", fake_hot)
    monkeypatch.setattr(pipeline, "alert_owner", fake_owner)
    return recorded


class FakeProvider(LLMProvider):
    def __init__(
        self,
        result: ConversationTurnResult | None = None,
        *,
        error: Exception | None = None,
        delay: float = 0,
        closing: ClosingDecision | Exception | None = None,
    ):
        self._result = result
        self._error = error
        self._delay = delay
        self._closing = closing or ClosingDecision(reasoning="still talking", conversation_finished=False)
        self.calls = 0
        self.closing_calls = 0
        self.seen_messages: list[list[dict]] = []

    async def generate_structured(self, *, response_schema, **kwargs):
        assert response_schema is ClosingDecision
        self.closing_calls += 1
        if isinstance(self._closing, Exception):
            raise self._closing
        return self._closing

    async def run_agentic_turn(self, *, messages, **kwargs):
        self.calls += 1
        self.seen_messages.append(messages)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return self._result


def _result(reply="Salom! Yordam bera olamanmi?", **kw) -> ConversationTurnResult:
    kw.setdefault("lead_status", "cold")
    kw.setdefault("lead_score", 5)
    kw.setdefault("qualification_reason", "greeting")
    return ConversationTurnResult(reply=reply, **kw)


class FakeMetaClient:
    def __init__(self, fail_times: int = 0, *, permanent: bool = False, actions_fail: bool = False):
        self.sent: list[dict] = []
        self.sent_images: list[str] = []
        self.reactions: list[tuple[str, str]] = []
        self.actions: list[str] = []
        self._fail_times = fail_times
        self._permanent = permanent
        self._actions_fail = actions_fail

    async def send_reaction(self, *, access_token, recipient_id, message_id, emoji):
        self.reactions.append((message_id, emoji))

    async def send_sender_action(self, *, access_token, recipient_id, action):
        if self._actions_fail:
            raise MetaAPIError("sender actions not allowed")
        self.actions.append(action)

    def _maybe_fail(self):
        if self._fail_times:
            self._fail_times -= 1
            raise MetaAPIError("simulated Instagram send failure", permanent=self._permanent)

    async def send_message(self, *, ig_business_id, access_token, recipient_id, text):
        self._maybe_fail()
        self.sent.append({"access_token": access_token, "recipient_id": recipient_id, "text": text})
        return {"message_id": f"sent-{uuid.uuid4()}"}

    async def send_image(self, *, ig_business_id, access_token, recipient_id, image_url):
        self._maybe_fail()
        self.sent_images.append(image_url)
        return {"message_id": f"img-{uuid.uuid4()}"}

    async def get_user_profile(self, user_id, access_token):
        return {"username": f"user_{user_id}", "name": "Test Customer"}


async def _seed(db_session, ig_business_id: str = "ig-biz-1") -> tuple[Business, InstagramAccount]:
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="IG Shop")
    db_session.add(business)
    await db_session.flush()
    account = InstagramAccount(
        business_id=business.id,
        ig_business_id=ig_business_id,
        ig_username="ig_shop",
        fb_page_id=f"page-{ig_business_id}",
        access_token_encrypted=encrypt_secret("real-token"),
        status="connected",
        connected_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(account)
    await db_session.commit()
    return business, account


def _event(mid: str, text: str, *, customer="customer-1", business_ig="ig-biz-1", kind="message", **extra) -> dict:
    return {
        "kind": kind,
        "mid": mid,
        "business_ig_id": business_ig,
        "customer_igsid": customer,
        "text": text,
        "attachment_type": extra.get("attachment_type"),
        "attachment_url": extra.get("attachment_url"),
        "needs_transcription": extra.get("needs_transcription", False),
    }


async def _deliver(db_session, provider, meta, *args, **kwargs) -> uuid.UUID | None:
    """What one webhook request + its background task do."""
    event_id = await pipeline.ingest_event(db_session, _event(*args, **kwargs))
    if event_id is not None:
        await pipeline.process_event(event_id, provider=provider, meta_client=meta)
    return event_id


async def _messages(db_session, business_id, customer_igsid) -> list[Message]:
    rows = await db_session.execute(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(Customer, Customer.id == Conversation.customer_id)
        .where(Conversation.business_id == business_id, Customer.ig_scoped_id == customer_igsid)
        .order_by(Message.created_at)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def _event_row(db_session, mid: str) -> WebhookEvent:
    return await db_session.scalar(
        select(WebhookEvent).where(WebhookEvent.external_event_id == mid).execution_options(populate_existing=True)
    )


# --- basic flow ---------------------------------------------------------------


async def test_incoming_message_creates_customer_conversation_and_replies(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    provider, meta = FakeProvider(_result()), FakeMetaClient()

    await _deliver(db_session, provider, meta, "mid-1", "Salom")

    assert provider.calls == 1
    assert meta.sent == [{"access_token": "real-token", "recipient_id": "customer-1", "text": "Salom! Yordam bera olamanmi?"}]
    msgs = await _messages(db_session, business.id, "customer-1")
    assert [m.sender_type for m in msgs] == ["customer", "ai"]
    assert msgs[1].delivery_status == "sent" and msgs[1].external_message_id.startswith("sent-")
    assert (await _event_row(db_session, "mid-1")).status == "processed"


async def test_customer_profile_is_fetched_once(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    await _deliver(db_session, FakeProvider(_result()), FakeMetaClient(), "mid-p1", "Salom", customer="prof-1")
    customer = await db_session.scalar(
        select(Customer).where(Customer.ig_scoped_id == "prof-1").execution_options(populate_existing=True)
    )
    assert customer.username == "user_prof-1"
    assert customer.profile_fetched_at is not None


async def test_expired_subscription_skips_ai_reply_but_keeps_message(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    business.subscription_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    await db_session.commit()
    provider, meta = FakeProvider(_result()), FakeMetaClient()

    await _deliver(db_session, provider, meta, "mid-expired", "Salom", customer="c-exp")

    assert provider.calls == 0
    assert meta.sent == []
    msgs = await _messages(db_session, business.id, "c-exp")
    assert [m.content for m in msgs] == ["Salom"]


@pytest.mark.parametrize("switch", ["ai_enabled_off", "ai_suspended", "deleted"])
async def test_ai_off_suspended_or_deleted_business_gets_no_reply(db_session, alerts, switch) -> None:
    business, _ = await _seed(db_session)
    if switch == "ai_enabled_off":
        business.ai_enabled = False
    elif switch == "ai_suspended":
        business.ai_suspended = True
    else:
        business.deleted_at = dt.datetime.now(dt.timezone.utc)
    await db_session.commit()
    provider, meta = FakeProvider(_result()), FakeMetaClient()

    await _deliver(db_session, provider, meta, f"mid-{switch}", "Salom", customer=f"c-{switch}")

    assert provider.calls == 0
    assert meta.sent == []


async def test_human_needed_conversation_gets_no_ai_reply(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    await _deliver(db_session, FakeProvider(_result()), FakeMetaClient(), "mid-h0", "Salom", customer="c-human")
    await db_session.execute(update(Conversation).where(Conversation.business_id == business.id).values(status="human_needed"))
    await db_session.commit()

    provider, meta = FakeProvider(_result()), FakeMetaClient()
    await _deliver(db_session, provider, meta, "mid-h1", "Hali javob kutyapman", customer="c-human")

    assert provider.calls == 0
    assert meta.sent == []
    assert (await _messages(db_session, business.id, "c-human"))[-1].content == "Hali javob kutyapman"


# --- routing --------------------------------------------------------------------


async def test_unknown_recipient_is_dropped_not_routed_to_another_business(db_session, alerts) -> None:
    """Regression: an unmatched recipient used to fall back to ANY connected
    account — delivering one business's customers into another's inbox."""
    other_business, _ = await _seed(db_session, ig_business_id="ig-other")
    provider, meta = FakeProvider(_result()), FakeMetaClient()

    event_id = await _deliver(db_session, provider, meta, "mid-stray", "hi", business_ig="ig-unknown")

    assert event_id is None
    assert provider.calls == 0 and meta.sent == []
    assert await db_session.scalar(select(Customer).where(Customer.business_id == other_business.id)) is None
    row = await _event_row(db_session, "mid-stray")
    assert row.status == "processed" and "no connected" in row.last_error


async def test_disconnected_account_does_not_receive(db_session, alerts) -> None:
    _business, account = await _seed(db_session)
    account.status = "disconnected"
    await db_session.commit()

    assert await _deliver(db_session, FakeProvider(_result()), FakeMetaClient(), "mid-dc", "hi") is None


# --- idempotency / retries --------------------------------------------------------


async def test_duplicate_webhook_delivery_is_not_reprocessed(db_session, alerts) -> None:
    await _seed(db_session)
    provider, meta = FakeProvider(_result("ok")), FakeMetaClient()

    await _deliver(db_session, provider, meta, "mid-dup", "hi", customer="c-dup")
    await _deliver(db_session, provider, meta, "mid-dup", "hi", customer="c-dup")

    assert provider.calls == 1
    assert len(meta.sent) == 1


async def test_failed_delivery_is_resent_on_retry_not_regenerated(db_session, alerts) -> None:
    """The reply is recorded before sending; when the send fails, the retry
    resends THAT reply — the customer never gets a second, different answer
    and the LLM isn't paid twice."""
    business, _ = await _seed(db_session)
    provider = FakeProvider(_result("Birinchi javob"))
    meta = FakeMetaClient(fail_times=1)

    await _deliver(db_session, provider, meta, "mid-retry", "hi", customer="c-retry")

    row = await _event_row(db_session, "mid-retry")
    assert row.status == "failed" and row.attempts == 1 and row.next_attempt_at is not None
    ai = [m for m in await _messages(db_session, business.id, "c-retry") if m.sender_type == "ai"]
    assert [(m.content, m.delivery_status) for m in ai] == [("Birinchi javob", "failed")]

    # The sweeper picks it up once its backoff has passed.
    row.next_attempt_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    await db_session.commit()
    await pipeline.process_event(row.id, provider=provider, meta_client=meta)

    assert provider.calls == 1  # not regenerated
    assert [s["text"] for s in meta.sent] == ["Birinchi javob"]
    ai = [m for m in await _messages(db_session, business.id, "c-retry") if m.sender_type == "ai"]
    assert [(m.content, m.delivery_status) for m in ai] == [("Birinchi javob", "sent")]
    assert (await _event_row(db_session, "mid-retry")).status == "processed"


async def test_failure_after_delivery_never_sends_a_second_reply(db_session, alerts, monkeypatch) -> None:
    """A crash after the reply went out (here: lead qualification) must not
    make the event fail and be retried into a second reply."""
    business, _ = await _seed(db_session)

    async def boom(*args, **kwargs):
        raise RuntimeError("lead write exploded")

    monkeypatch.setattr(pipeline, "apply_qualification", boom)
    provider, meta = FakeProvider(_result("Javob")), FakeMetaClient()

    event_id = await _deliver(db_session, provider, meta, "mid-post", "hi", customer="c-post")

    assert (await _event_row(db_session, "mid-post")).status == "processed"
    # Even a forced re-drive finds the message answered.
    await db_session.execute(update(WebhookEvent).where(WebhookEvent.id == event_id).values(status="failed", next_attempt_at=None))
    await db_session.commit()
    await pipeline.process_event(event_id, provider=provider, meta_client=meta)
    assert provider.calls == 1
    assert len(meta.sent) == 1


async def test_stale_processing_lease_is_taken_over(db_session, alerts) -> None:
    """A worker that died mid-turn leaves the event 'processing'; after the
    lease it is processed again instead of being stuck forever."""
    business, _ = await _seed(db_session)
    event_id = await pipeline.ingest_event(db_session, _event("mid-lease", "hi", customer="c-lease"))
    await db_session.execute(
        update(WebhookEvent)
        .where(WebhookEvent.id == event_id)
        .values(status="processing", claimed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10), attempts=1)
    )
    await db_session.commit()

    provider, meta = FakeProvider(_result()), FakeMetaClient()
    assert event_id in await pipeline.due_event_ids(db_session)
    await pipeline.sweep_events(provider, meta)

    assert provider.calls == 1 and len(meta.sent) == 1
    assert (await _event_row(db_session, "mid-lease")).status == "processed"


async def test_fresh_processing_claim_is_not_stolen(db_session, alerts) -> None:
    await _seed(db_session)
    event_id = await pipeline.ingest_event(db_session, _event("mid-busy", "hi", customer="c-busy"))
    await db_session.execute(
        update(WebhookEvent).where(WebhookEvent.id == event_id).values(status="processing", claimed_at=dt.datetime.now(dt.timezone.utc))
    )
    await db_session.commit()
    provider = FakeProvider(_result())
    await pipeline.process_event(event_id, provider=provider, meta_client=FakeMetaClient())
    assert provider.calls == 0


async def test_retries_are_abandoned_after_max_attempts_and_owner_alerted(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    provider, meta = FakeProvider(_result("x")), FakeMetaClient(fail_times=100)
    event_id = await _deliver(db_session, provider, meta, "mid-dead", "hi", customer="c-dead")
    for _ in range(pipeline.MAX_ATTEMPTS - 1):
        await db_session.execute(update(WebhookEvent).where(WebhookEvent.id == event_id).values(next_attempt_at=None))
        await db_session.commit()
        await pipeline.process_event(event_id, provider=provider, meta_client=meta)

    row = await _event_row(db_session, "mid-dead")
    assert row.status == "abandoned"
    assert [a["type"] for a in alerts["owner"]] == ["delivery_failed"]
    conversation = await db_session.scalar(
        select(Conversation).where(Conversation.business_id == business.id).execution_options(populate_existing=True)
    )
    assert conversation.status == "human_needed"


async def test_permanent_send_failure_is_abandoned_at_once(db_session, alerts) -> None:
    """Messaging window closed, recipient gone, token revoked: retrying can't
    help, so the event isn't retried five times — a person is told now."""
    business, _ = await _seed(db_session)
    provider, meta = FakeProvider(_result("x")), FakeMetaClient(fail_times=1, permanent=True)

    await _deliver(db_session, provider, meta, "mid-perm", "hi", customer="c-perm")

    row = await _event_row(db_session, "mid-perm")
    assert row.status == "abandoned" and row.attempts == 1
    assert [a["type"] for a in alerts["owner"]] == ["delivery_failed"]


async def test_event_out_of_attempts_is_never_claimed_again(db_session, alerts) -> None:
    await _seed(db_session)
    event_id = await pipeline.ingest_event(db_session, _event("mid-max", "hi", customer="c-max"))
    await db_session.execute(
        update(WebhookEvent).where(WebhookEvent.id == event_id).values(status="failed", attempts=pipeline.MAX_ATTEMPTS)
    )
    await db_session.commit()
    provider = FakeProvider(_result())

    assert event_id not in await pipeline.due_event_ids(db_session)
    await pipeline.process_event(event_id, provider=provider, meta_client=FakeMetaClient())
    assert provider.calls == 0


async def test_worker_dying_on_the_last_attempt_is_abandoned_with_an_alert(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    event_id = await pipeline.ingest_event(db_session, _event("mid-lastlease", "hi", customer="c-last"))
    await db_session.execute(
        update(WebhookEvent)
        .where(WebhookEvent.id == event_id)
        .values(
            status="processing",
            claimed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10),
            attempts=pipeline.MAX_ATTEMPTS,
        )
    )
    await db_session.commit()
    provider = FakeProvider(_result())

    await pipeline.sweep_events(provider, FakeMetaClient())

    assert provider.calls == 0
    assert (await _event_row(db_session, "mid-lastlease")).status == "abandoned"
    assert [a["type"] for a in alerts["owner"]] == ["delivery_failed"]


async def test_unconfigured_llm_provider_still_records_and_hands_off(db_session, alerts) -> None:
    """The provider is resolved only when a turn runs; a missing API key
    hands the conversation to a person instead of failing the webhook."""
    business, _ = await _seed(db_session)
    meta = FakeMetaClient()

    def broken_factory():
        raise LLMProviderError("OPENROUTER_API_KEY is not set.")

    await _deliver(db_session, broken_factory, meta, "mid-nokey", "salom", customer="c-nokey")

    assert [m.content for m in await _messages(db_session, business.id, "c-nokey")][0] == "salom"
    assert len(meta.sent) == 1  # the operator-will-reply line
    assert [a["type"] for a in alerts["owner"]] == ["ai_limit"]
    assert (await _event_row(db_session, "mid-nokey")).status == "processed"


# --- debounce / serialization ----------------------------------------------------------


async def test_burst_of_rapid_messages_gets_one_combined_reply(db_session, alerts, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "DEBOUNCE_SECONDS", 0.3)
    monkeypatch.setattr(pipeline, "FRAGMENT_DEBOUNCE_SECONDS", 0.3)
    business, _ = await _seed(db_session)
    provider, meta = FakeProvider(_result("ok")), FakeMetaClient()

    first_id = await pipeline.ingest_event(db_session, _event("burst-1", "Salom", customer="burst"))
    first = asyncio.create_task(pipeline.process_event(first_id, provider=provider, meta_client=meta))
    await asyncio.sleep(0.05)
    await _deliver(db_session, provider, meta, "burst-2", "42 razmer bormi", customer="burst")
    await first

    assert provider.calls == 1
    assert len(meta.sent) == 1
    assert [m.content for m in await _messages(db_session, business.id, "burst") if m.sender_type == "customer"] == [
        "Salom", "42 razmer bormi",
    ]


async def test_debounce_gives_the_processing_slot_back(db_session, alerts, monkeypatch) -> None:
    """With one slot, a second customer's turn runs while the first customer's
    event is still in its debounce wait."""
    monkeypatch.setattr(pipeline, "FRAGMENT_DEBOUNCE_SECONDS", 0.5)
    monkeypatch.setattr(pipeline, "_PROCESS_SLOTS", asyncio.Semaphore(1))
    await _seed(db_session)
    meta = FakeMetaClient()

    waiting_id = await pipeline.ingest_event(db_session, _event("slot-1", "Salom", customer="slot-a"))
    waiting = asyncio.create_task(pipeline.process_event(waiting_id, provider=FakeProvider(_result("A")), meta_client=meta))
    await asyncio.sleep(0.05)
    monkeypatch.setattr(pipeline, "FRAGMENT_DEBOUNCE_SECONDS", 0)
    other_id = await pipeline.ingest_event(db_session, _event("slot-2", "Salom", customer="slot-b"))
    await asyncio.wait_for(pipeline.process_event(other_id, provider=FakeProvider(_result("B")), meta_client=meta), 0.3)

    assert [s["text"] for s in meta.sent] == ["B"]
    await waiting
    assert [s["text"] for s in meta.sent] == ["B", "A"]
    assert pipeline._PROCESS_SLOTS._value == 1


async def test_shutdown_hands_interrupted_events_back(db_session, alerts, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "FRAGMENT_DEBOUNCE_SECONDS", 5)
    await _seed(db_session)
    event_id = await pipeline.ingest_event(db_session, _event("cut-1", "Salom", customer="cut"))
    task = asyncio.create_task(pipeline.process_event(event_id, provider=FakeProvider(_result()), meta_client=FakeMetaClient()))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await pipeline.release_interrupted()

    row = await _event_row(db_session, "cut-1")
    assert (row.status, row.attempts, row.last_error) == ("failed", 0, "interrupted by shutdown")
    assert row.next_attempt_at <= dt.datetime.now(dt.timezone.utc)
    assert pipeline._IN_FLIGHT == set()


async def _message_during_a_running_turn(db_session, customer: str):
    slow = FakeProvider(_result("Birinchi"), delay=0.4)
    meta = FakeMetaClient()
    first_id = await pipeline.ingest_event(db_session, _event(f"{customer}-1", "Salom", customer=customer))
    first = asyncio.create_task(pipeline.process_event(first_id, provider=slow, meta_client=meta))
    await asyncio.sleep(0.15)  # turn 1 is inside the LLM call
    second_provider = FakeProvider(_result("Ikkinchi"))
    await _deliver(db_session, second_provider, meta, f"{customer}-2", "narxi qancha?", customer=customer)
    await first
    return meta, second_provider


async def test_reply_written_while_the_customer_wrote_again_is_rewritten(db_session, alerts) -> None:
    """The draft for "Salom" is dropped once "narxi qancha?" arrives: the
    customer gets one reply to both, not one each."""
    await _seed(db_session)
    meta, second_provider = await _message_during_a_running_turn(db_session, "sup")

    assert [s["text"] for s in meta.sent] == ["Ikkinchi"]
    assert [m["content"] for m in second_provider.seen_messages[0]] == ["Salom", "narxi qancha?"]
    assert (await _event_row(db_session, "sup-1")).status == "processed"


async def test_an_old_burst_gets_its_reply_even_if_the_customer_wrote_again(db_session, alerts, monkeypatch) -> None:
    """Past the supersede age the customer has waited long enough: the first
    reply goes out, and the new message gets its own."""
    monkeypatch.setattr(pipeline, "SUPERSEDE_MAX_AGE_SECONDS", 0)
    await _seed(db_session)
    meta, second_provider = await _message_during_a_running_turn(db_session, "par")

    assert [s["text"] for s in meta.sent] == ["Birinchi", "Ikkinchi"]
    # Chronological, as the customer saw it: their second message landed
    # while the first reply was still being written.
    history = second_provider.seen_messages[0]
    assert [m["content"] for m in history] == ["Salom", "narxi qancha?", "Birinchi"]


@pytest.mark.parametrize(
    ("content", "attachment", "expected"),
    [
        ("Salom", None, 8.0),
        ("42 razmer", None, 8.0),
        ("Narxi qancha?", None, 3.0),
        ("Menga qora rangli krossovka kerak edi", None, 3.0),
        ("", "image", 8.0),
        ("Yangi kolleksiya", "ig_reel", 8.0),
        ("[Ovozli xabar]", "audio", 3.0),
    ],
)
def test_fragments_wait_longer_than_questions(monkeypatch, content, attachment, expected) -> None:
    monkeypatch.setattr(pipeline, "DEBOUNCE_SECONDS", 3.0)
    monkeypatch.setattr(pipeline, "FRAGMENT_DEBOUNCE_SECONDS", 8.0)
    now = dt.datetime.now(dt.timezone.utc)
    message = Message(content=content, attachment_type=attachment, sender_type="customer")
    assert pipeline._debounce_seconds(message, now, now) == expected


def test_the_wait_never_runs_past_the_cap_from_the_first_message(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "FRAGMENT_DEBOUNCE_SECONDS", 8.0)
    monkeypatch.setattr(pipeline, "DEBOUNCE_MAX_SECONDS", 15.0)
    now = dt.datetime.now(dt.timezone.utc)
    message = Message(content="aka", sender_type="customer")
    assert pipeline._debounce_seconds(message, now - dt.timedelta(seconds=12), now) == pytest.approx(3.0)
    assert pipeline._debounce_seconds(message, now - dt.timedelta(seconds=40), now) == 0.0


async def test_seen_then_typing_while_the_ai_answers(db_session, alerts, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "SENDER_ACTIONS", True)
    await _seed(db_session)
    meta = FakeMetaClient()
    await _deliver(db_session, FakeProvider(_result("Salom!")), meta, "sa-1", "Salom", customer="c-sa")
    await asyncio.gather(*pipeline._BACKGROUND)
    assert meta.actions == ["mark_seen", "typing_on"]

    # A refused sender action changes nothing about the reply.
    failing = FakeMetaClient(actions_fail=True)
    await _deliver(db_session, FakeProvider(_result("Bor")), failing, "sa-2", "42 bormi?", customer="c-sa")
    await asyncio.gather(*pipeline._BACKGROUND)
    assert [s["text"] for s in failing.sent] == ["Bor"]


async def test_no_seen_or_typing_while_a_person_handles_the_chat(db_session, alerts, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "SENDER_ACTIONS", True)
    business, _ = await _seed(db_session)
    await _deliver(db_session, FakeProvider(_result()), FakeMetaClient(), "hm-0", "Salom", customer="c-hm")
    await db_session.execute(
        update(Conversation).where(Conversation.business_id == business.id).values(status="human_active")
    )
    await db_session.commit()

    meta = FakeMetaClient()
    await _deliver(db_session, FakeProvider(_result("x")), meta, "hm-1", "narxi?", customer="c-hm")
    await asyncio.gather(*pipeline._BACKGROUND)
    assert meta.actions == [] and meta.sent == []


# --- echoes -------------------------------------------------------------------------------


async def test_echo_of_our_own_reply_is_not_recorded_twice(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    meta = FakeMetaClient()
    await _deliver(db_session, FakeProvider(_result("AI javobi")), meta, "mid-e1", "hi", customer="c-echo")
    ai = [m for m in await _messages(db_session, business.id, "c-echo") if m.sender_type == "ai"][0]

    await _deliver(
        db_session, FakeProvider(), meta, ai.external_message_id, "AI javobi",
        customer="c-echo", kind="echo",
    )

    msgs = await _messages(db_session, business.id, "c-echo")
    assert [m.sender_type for m in msgs] == ["customer", "ai"]
    conversation = await db_session.scalar(
        select(Conversation).where(Conversation.business_id == business.id).execution_options(populate_existing=True)
    )
    assert conversation.status == "ai_active"


async def test_echo_arriving_before_the_send_response_is_matched_not_taken_for_a_human(db_session, alerts) -> None:
    """Meta can deliver the echo before our send call returns (or the send
    times out after Meta delivered). The echo is matched to the outbound row
    still waiting for its id — not recorded as a person replying, which would
    have switched the AI off."""
    from app.conversations.service import add_message, get_or_create_conversation
    from app.customers.service import get_or_create_customer

    business, _ = await _seed(db_session)
    customer = await get_or_create_customer(db_session, business.id, "c-early")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    pending = await add_message(db_session, conversation, sender_type="ai", content="Mana javob", delivery_status="pending")
    await db_session.commit()

    await _deliver(db_session, FakeProvider(), FakeMetaClient(), "early-echo-mid", "Mana javob", customer="c-early", kind="echo")

    msgs = await _messages(db_session, business.id, "c-early")
    assert [m.sender_type for m in msgs] == ["ai"]
    assert msgs[0].id == pending.id and msgs[0].external_message_id == "early-echo-mid"
    assert msgs[0].delivery_status == "sent"
    conversation = await db_session.get(Conversation, conversation.id, populate_existing=True)
    assert conversation.status == "ai_active"
    assert alerts["owner"] == []


async def test_old_untranscribed_voice_note_does_not_block_new_turns(db_session, alerts) -> None:
    """A voice note from before transcription was configured (its media URL
    long expired) must not hand every later message to a human."""
    from app.conversations.service import add_message, get_or_create_conversation
    from app.customers.service import get_or_create_customer

    business, _ = await _seed(db_session)
    customer = await get_or_create_customer(db_session, business.id, "c-oldvoice")
    conversation = await get_or_create_conversation(db_session, business.id, customer.id)
    old = await add_message(
        db_session, conversation, sender_type="customer", content="[Ovozli xabar]", message_type="audio",
        attachment_type="audio", attachment_url="https://cdn.example/old.mp4",
    )
    old.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    await db_session.commit()
    provider = FakeProvider(_result("Salom!"))

    await _deliver(db_session, provider, FakeMetaClient(), "mid-after-old-voice", "salom", customer="c-oldvoice")

    assert provider.calls == 1
    assert alerts["owner"] == []


async def test_human_reply_from_instagram_app_is_recorded_and_ai_steps_back(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    meta = FakeMetaClient()
    await _deliver(db_session, FakeProvider(_result()), meta, "mid-x1", "hi", customer="c-op")

    await _deliver(db_session, FakeProvider(), meta, "op-mid-1", "Men operatorman", customer="c-op", kind="echo")

    msgs = await _messages(db_session, business.id, "c-op")
    assert msgs[-1].sender_type == "human" and msgs[-1].content == "Men operatorman"
    conversation = await db_session.scalar(
        select(Conversation).where(Conversation.business_id == business.id).execution_options(populate_existing=True)
    )
    assert conversation.status == "human_active"
    assert [a["type"] for a in alerts["owner"]] == ["handoff"]

    provider = FakeProvider(_result("should not be sent"))
    await _deliver(db_session, provider, meta, "mid-x2", "rahmat", customer="c-op")
    assert provider.calls == 0

    # A second message from the app while already human_active: recorded,
    # but the owner isn't told again that the AI paused.
    await _deliver(db_session, FakeProvider(), meta, "op-mid-2", "Yana bir narsa", customer="c-op", kind="echo")
    assert [a["type"] for a in alerts["owner"]] == ["handoff"]


async def test_product_photo_is_recorded_so_its_echo_is_recognised(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    product = Product(
        business_id=business.id, name="Nike Hoodie", currency="UZS", availability=True,
        attributes={"image_url": "https://example.com/hoodie.jpg"}, source="manual",
    )
    db_session.add(product)
    await db_session.commit()
    meta = FakeMetaClient()

    await _deliver(
        db_session,
        FakeProvider(_result("Mana, ko'ring:", image_product_ids=[str(product.id)])),
        meta, "mid-photo", "rasmini ko'rsating", customer="c-photo",
    )

    assert meta.sent[0]["text"] == "Mana, ko'ring:"
    assert meta.sent_images == ["https://example.com/hoodie.jpg"]
    photo = [m for m in await _messages(db_session, business.id, "c-photo") if m.attachment_type == "image"][0]
    assert photo.sender_type == "ai" and photo.delivery_status == "sent"

    await _deliver(db_session, FakeProvider(), meta, photo.external_message_id, "", customer="c-photo", kind="echo",
                   attachment_type="image", attachment_url="https://cdn/x.jpg")
    assert [m.sender_type for m in await _messages(db_session, business.id, "c-photo")].count("human") == 0


async def test_invented_or_foreign_product_id_never_sends_a_photo(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    other_business, _ = await _seed(db_session, ig_business_id="ig-biz-2")
    foreign = Product(
        business_id=other_business.id, name="Other", currency="UZS", availability=True,
        attributes={"image_url": "https://example.com/foreign.jpg"}, source="manual",
    )
    db_session.add(foreign)
    await db_session.commit()
    meta = FakeMetaClient()

    await _deliver(
        db_session, FakeProvider(_result("ok", image_product_ids=["not-a-uuid", str(foreign.id)])),
        meta, "mid-foreign", "hi", customer="c-foreign",
    )
    assert meta.sent_images == []


# --- leads / alerts ----------------------------------------------------------------------


async def test_hot_lead_alerts_the_owner_once(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    product = Product(business_id=business.id, name="Nike Hoodie", currency="UZS", availability=True, attributes={}, source="manual")
    db_session.add(product)
    await db_session.commit()
    provider = FakeProvider(
        _result("Rahmat!", lead_status="hot", lead_score=90, qualification_reason="gave phone",
                phone_detected="+998901234567", interested_product_ids=[str(product.id)])
    )
    meta = FakeMetaClient()

    await _deliver(db_session, provider, meta, "mid-hot", "+998901234567", customer="c-hot")
    await _deliver(db_session, provider, meta, "mid-hot", "+998901234567", customer="c-hot")  # redelivery

    assert len(alerts["hot"]) == 1
    lead = await db_session.scalar(select(Lead).where(Lead.id == alerts["hot"][0]).execution_options(populate_existing=True))
    assert lead.status == "hot" and lead.phone == "+998901234567" and lead.hot_notified_at is not None
    assert lead.interested_products == [{"id": str(product.id), "name": "Nike Hoodie"}]


async def test_lead_recalculated_fresh_and_products_not_wiped(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    product = Product(business_id=business.id, name="Hoodie", currency="UZS", availability=True, attributes={}, source="manual")
    db_session.add(product)
    await db_session.commit()
    meta = FakeMetaClient()

    await _deliver(db_session, FakeProvider(_result("A", lead_status="warm", lead_score=45, interested_product_ids=[str(product.id)])),
                   meta, "m1", "Hoodie bormi?", customer="c-multi")
    await _deliver(db_session, FakeProvider(_result("B", lead_status="hot", lead_score=85)), meta, "m2", "90 123 45 67", customer="c-multi")
    await _deliver(db_session, FakeProvider(_result("C", lead_status="cold", lead_score=10)), meta, "m3", "Bekor qilaman", customer="c-multi")

    lead = await db_session.scalar(select(Lead).where(Lead.business_id == business.id).execution_options(populate_existing=True))
    assert lead.status == "cold"
    assert lead.phone == "+998901234567"
    assert lead.interested_products == [{"id": str(product.id), "name": "Hoodie"}]
    assert len(alerts["hot"]) == 1


async def test_phone_captured_while_ai_disabled_creates_hot_lead_without_auto_reply(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    business.ai_enabled = False
    await db_session.commit()
    provider, meta = FakeProvider(_result()), FakeMetaClient()

    await _deliver(db_session, provider, meta, "mid-off-phone", "Mana raqamim: +998 90 123 45 67", customer="c-offp")

    assert provider.calls == 0
    assert meta.sent == []  # AI is off: nothing automated goes out
    assert len(alerts["hot"]) == 1
    customer = await db_session.scalar(select(Customer).where(Customer.ig_scoped_id == "c-offp").execution_options(populate_existing=True))
    assert customer.phone == "+998901234567"


async def test_phone_during_handoff_is_acknowledged_and_alerts_even_if_lead_was_hot(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    await _deliver(db_session, FakeProvider(_result("x", lead_status="hot", lead_score=80)), FakeMetaClient(), "hp0", "olaman", customer="c-hp")
    assert len(alerts["hot"]) == 1  # hot without a phone
    await db_session.execute(update(Conversation).where(Conversation.business_id == business.id).values(status="human_needed"))
    await db_session.commit()

    meta = FakeMetaClient()
    await _deliver(db_session, FakeProvider(), meta, "hp1", "Nomerim: 90 123 45 67", customer="c-hp")

    assert len(alerts["hot"]) == 2  # the phone itself is news
    assert len(meta.sent) == 1 and "Rahmat" in meta.sent[0]["text"]


async def test_escalation_alerts_the_owner(db_session, alerts) -> None:
    await _seed(db_session)

    class Escalating(FakeProvider):
        async def run_agentic_turn(self, *, tool_executor, **kwargs):
            self.calls += 1
            await tool_executor("request_human", {"reason": "Mijoz operator so'radi"})
            return _result("Operatorimiz tez orada yozadi, raqamingizni qoldiring.")

    await _deliver(db_session, Escalating(), FakeMetaClient(), "mid-esc", "operator kerak", customer="c-esc")

    assert [a["type"] for a in alerts["owner"]] == ["handoff"]


async def test_provider_failure_hands_off_without_touching_the_lead(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    await _deliver(db_session, FakeProvider(_result("ok", lead_status="warm", lead_score=60)), FakeMetaClient(), "pf0", "hoodie", customer="c-pf")

    meta = FakeMetaClient()
    await _deliver(db_session, FakeProvider(error=LLMProviderError("down")), meta, "pf1", "narxi?", customer="c-pf")

    assert len(meta.sent) == 1  # the handoff reply
    lead = await db_session.scalar(select(Lead).where(Lead.business_id == business.id).execution_options(populate_existing=True))
    assert (lead.status, lead.score) == ("warm", 60)  # not overwritten with placeholder cold/0
    assert [a["type"] for a in alerts["owner"]] == ["handoff"]


async def test_daily_budget_stops_the_ai_and_hands_off(db_session, alerts, monkeypatch) -> None:
    from app.ai.models import AiUsageLog
    from app.core.config import get_settings

    business, _ = await _seed(db_session)
    db_session.add(AiUsageLog(business_id=business.id, kind="conversation", model="m", cost_usd=get_settings().ai_daily_cost_limit_usd))
    await db_session.commit()
    provider, meta = FakeProvider(_result("should not run")), FakeMetaClient()

    await _deliver(db_session, provider, meta, "mid-budget", "hi", customer="c-budget")

    assert provider.calls == 0
    assert len(meta.sent) == 1  # the operator-will-reply line, no LLM involved
    assert [a["type"] for a in alerts["owner"]] == ["ai_limit"]


async def test_voice_note_that_cannot_be_transcribed_goes_to_a_human(db_session, alerts) -> None:
    business, _ = await _seed(db_session)
    provider, meta = FakeProvider(_result("should not run")), FakeMetaClient()

    await _deliver(
        db_session, provider, meta, "mid-voice", "[Ovozli xabar]", customer="c-voice",
        attachment_type="audio", attachment_url="https://cdn.example/voice.mp4", needs_transcription=True,
    )

    assert provider.calls == 0  # STT_PROVIDER is empty in tests
    assert [a["type"] for a in alerts["owner"]] == ["handoff"]
    conversation = await db_session.scalar(
        select(Conversation).where(Conversation.business_id == business.id).execution_options(populate_existing=True)
    )
    assert conversation.status == "human_needed"


async def test_voice_note_is_transcribed_before_the_turn(db_session, alerts, monkeypatch) -> None:
    business, _ = await _seed(db_session)

    async def fake_transcribe(url, business_id=None):
        return "qora hoodie bormi"

    monkeypatch.setattr(pipeline, "transcribe_audio_url", fake_transcribe)
    provider = FakeProvider(_result("Bor!"))

    await _deliver(
        db_session, provider, FakeMetaClient(), "mid-voice2", "[Ovozli xabar]", customer="c-voice2",
        attachment_type="audio", attachment_url="https://cdn.example/v.mp4", needs_transcription=True,
    )

    assert provider.calls == 1
    assert provider.seen_messages[0][-1]["content"] == "qora hoodie bormi"


# --- parsing --------------------------------------------------------------------------------


def test_parse_webhook_body_normalizes_messages_echoes_and_skips_noise() -> None:
    body = {
        "entry": [{"messaging": [
            {"sender": {"id": "cust"}, "recipient": {"id": "biz"}, "message": {"mid": "m1", "text": "Salom"}},
            {"sender": {"id": "biz"}, "recipient": {"id": "cust"}, "message": {"mid": "m2", "text": "Javob", "is_echo": True}},
            {"sender": {"id": "cust"}, "recipient": {"id": "biz"}, "message": {"mid": "m3", "is_deleted": True}},
            {"sender": {"id": "cust"}, "recipient": {"id": "biz"}, "read": {"mid": "m1"}},
            {"sender": {"id": "cust"}, "recipient": {"id": "biz"}, "message": {
                "mid": "m4", "attachments": [{"type": "audio", "payload": {"url": "https://a/v.mp4"}}]}},
        ]}]
    }
    events = pipeline.parse_webhook_body(body)
    assert [(e["kind"], e["mid"], e["business_ig_id"], e["customer_igsid"]) for e in events] == [
        ("message", "m1", "biz", "cust"),
        ("echo", "m2", "biz", "cust"),
        ("message", "m4", "biz", "cust"),
    ]
    assert events[2]["needs_transcription"] is True and events[2]["text"] == "[Ovozli xabar]"


def test_media_messages_are_stored_without_labels() -> None:
    """The dashboard shows the media itself; the model gets a note. A label as
    the text ("[Template yuborildi]") only ever got quoted back to customers."""
    body = {
        "entry": [{"messaging": [
            {"sender": {"id": "cust"}, "recipient": {"id": "biz"}, "message": {
                "mid": "p1", "attachments": [{"type": "image", "payload": {"url": "https://cdn/p.jpg"}}]}},
            {"sender": {"id": "cust"}, "recipient": {"id": "biz"}, "message": {
                "mid": "p2", "attachments": [{"type": "ig_reel", "payload": {
                    "url": "https://cdn/r.mp4", "title": "  Nike Tech Fleece\n yangi kolleksiya "}}]}},
            {"sender": {"id": "cust"}, "recipient": {"id": "biz"}, "message": {
                "mid": "p3", "attachments": [{"type": "template", "payload": {}}]}},
            {"sender": {"id": "biz"}, "recipient": {"id": "cust"}, "message": {
                "mid": "p4", "is_echo": True, "attachments": [{"type": "image", "payload": {"url": "https://cdn/e.jpg"}}]}},
        ]}]
    }
    events = {e["mid"]: e for e in pipeline.parse_webhook_body(body)}
    assert events["p1"]["text"] == "" and events["p1"]["attachment_type"] == "image"
    assert events["p2"]["text"] == "Nike Tech Fleece yangi kolleksiya"  # the caption, and only that
    assert events["p3"]["text"] == "" and events["p3"]["attachment_type"] == "template"
    assert events["p4"]["kind"] == "echo" and events["p4"]["text"] == ""


# --- closing the conversation ------------------------------------------------------------


async def test_closing_message_gets_a_reaction_not_a_reply(db_session, alerts) -> None:
    """"Hop" after the goodbye: the model decides the conversation is over and
    picks a reaction; no new message goes out."""
    business, _ = await _seed(db_session)
    meta = FakeMetaClient()
    await _deliver(db_session, FakeProvider(_result("Rahmat! Hamkasbim tez orada bog'lanadi.")), meta,
                   "cl-1", "Nike Tech Fleece olaman", customer="c-close")

    provider = FakeProvider(
        _result("should not be sent"),
        closing=ClosingDecision(reasoning="said ok after the goodbye", conversation_finished=True, reaction="🔥"),
    )
    await _deliver(db_session, provider, meta, "cl-2", "hop", customer="c-close")

    assert provider.closing_calls == 1 and provider.calls == 0
    assert meta.reactions == [("cl-2", "🔥")]
    assert [s["text"] for s in meta.sent] == ["Rahmat! Hamkasbim tez orada bog'lanadi."]
    # The dashboard shows the conversation was answered — with the reaction.
    reaction = (await _messages(db_session, business.id, "c-close"))[-1]
    assert (reaction.sender_type, reaction.message_type, reaction.content, reaction.delivery_status) == (
        "ai", "reaction", "🔥", "sent"
    )
    assert (await _event_row(db_session, "cl-2")).status == "processed"
    conversation = await db_session.scalar(
        select(Conversation).where(Conversation.business_id == business.id).execution_options(populate_existing=True)
    )
    last_customer = [m for m in await _messages(db_session, business.id, "c-close") if m.sender_type == "customer"][-1]
    assert conversation.last_answered_customer_message_at == last_customer.created_at


async def test_short_answer_the_model_says_needs_a_reply_is_answered(db_session, alerts) -> None:
    await _seed(db_session)
    meta = FakeMetaClient()
    await _deliver(db_session, FakeProvider(_result("Qora rangdami yoki kulrangdami?")), meta,
                   "q-1", "Tech Fleece bormi", customer="c-q")

    provider = FakeProvider(_result("Zo'r, qora bor."))  # the default decision: not finished
    await _deliver(db_session, provider, meta, "q-2", "qora", customer="c-q")

    assert provider.closing_calls == 1 and provider.calls == 1
    assert meta.reactions == [] and meta.sent[-1]["text"] == "Zo'r, qora bor."


async def test_closing_decision_failure_means_a_normal_reply(db_session, alerts) -> None:
    await _seed(db_session)
    meta = FakeMetaClient()
    await _deliver(db_session, FakeProvider(_result("Marhamat!")), meta, "f-1", "rahmat katta", customer="c-f")

    provider = FakeProvider(_result("Arzimaydi!"), closing=LLMProviderError("down"))
    await _deliver(db_session, provider, meta, "f-2", "ok", customer="c-f")

    assert provider.calls == 1 and meta.sent[-1]["text"] == "Arzimaydi!"


async def test_long_or_question_messages_never_ask_for_a_closing_decision(db_session, alerts) -> None:
    await _seed(db_session)
    meta = FakeMetaClient()
    await _deliver(db_session, FakeProvider(_result("Marhamat!")), meta, "n-1", "salom", customer="c-n")

    provider = FakeProvider(_result("450 000 so'm."))
    await _deliver(db_session, provider, meta, "n-2", "narxi qancha?", customer="c-n")

    assert provider.closing_calls == 0 and provider.calls == 1
