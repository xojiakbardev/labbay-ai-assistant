"""Unit and integration tests for persistent, real-time dashboard notifications."""
import asyncio
import json
import uuid
import datetime as dt
import psycopg
import pytest
from sqlalchemy import select

from app.auth.models import User
from app.businesses.models import Business
from app.conversations.service import get_or_create_conversation
from app.customers.service import get_or_create_customer
from app.leads.models import Lead
from app.notifications import service as notif_service
from app.notifications.broadcaster import broadcaster
from app.notifications.models import Notification
from tests.conftest import _sync_dsn, create_business_and_headers


def test_list_notifications_empty_initially(client) -> None:
    headers = create_business_and_headers("notif_owner1@test.com", "Notif Biz 1")
    resp = client.get("/notifications", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_notifications_require_auth(client) -> None:
    resp = client.get("/notifications")
    assert resp.status_code == 401
    resp_count = client.get("/notifications/unread-count")
    assert resp_count.status_code == 401


def test_create_and_query_notifications(client) -> None:
    headers = create_business_and_headers("notif_owner2@test.com", "Notif Biz 2")

    with psycopg.connect(_sync_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM businesses WHERE name = 'Notif Biz 2'")
            b_id = cur.fetchone()[0]
            notif_id = uuid.uuid4()
            now = dt.datetime.now(dt.timezone.utc)
            cur.execute(
                "INSERT INTO notifications (id, business_id, type, title, message, is_read, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (notif_id, b_id, "lead_hot", "Yangi Issiq Lid 🔥", "@buyer: +998901234567", False, now, now),
            )
            conn.commit()

    # Query list
    resp = client.get("/notifications", headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == str(notif_id)
    assert items[0]["type"] == "lead_hot"
    assert items[0]["title"] == "Yangi Issiq Lid 🔥"
    assert items[0]["is_read"] is False

    # Query unread count
    resp_count = client.get("/notifications/unread-count", headers=headers)
    assert resp_count.status_code == 200
    assert resp_count.json() == {"unread_count": 1}

    # Mark single as read
    resp_read = client.patch(f"/notifications/{notif_id}/read", headers=headers)
    assert resp_read.status_code == 200
    assert resp_read.json()["is_read"] is True
    assert resp_read.json()["read_at"] is not None

    # Unread count now 0
    resp_count2 = client.get("/notifications/unread-count", headers=headers)
    assert resp_count2.json() == {"unread_count": 0}


def test_tenant_isolation_notifications(client) -> None:
    headers1 = create_business_and_headers("tenant1_owner@test.com", "Tenant 1")
    headers2 = create_business_and_headers("tenant2_owner@test.com", "Tenant 2")

    notif1_id = uuid.uuid4()
    now = dt.datetime.now(dt.timezone.utc)

    with psycopg.connect(_sync_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM businesses WHERE name = 'Tenant 1'")
            b1_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO notifications (id, business_id, type, title, message, is_read, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (notif1_id, b1_id, "lead_hot", "Tenant 1 Lead", "Message 1", False, now, now),
            )
            conn.commit()

    # Tenant 1 sees notification
    resp1 = client.get("/notifications", headers=headers1)
    assert len(resp1.json()) == 1

    # Tenant 2 sees nothing
    resp2 = client.get("/notifications", headers=headers2)
    assert resp2.json() == []

    # Tenant 2 cannot mark Tenant 1's notification as read
    resp_patch = client.patch(f"/notifications/{notif1_id}/read", headers=headers2)
    assert resp_patch.status_code == 404


def test_mark_all_read(client) -> None:
    headers = create_business_and_headers("markall_owner@test.com", "Mark All Biz")

    with psycopg.connect(_sync_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM businesses WHERE name = 'Mark All Biz'")
            b_id = cur.fetchone()[0]
            now = dt.datetime.now(dt.timezone.utc)
            for i in range(3):
                cur.execute(
                    "INSERT INTO notifications (id, business_id, type, title, message, is_read, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (uuid.uuid4(), b_id, "lead_warm", f"Lead {i}", f"Msg {i}", False, now, now),
                )
            conn.commit()

    # Unread count initially 3
    resp_count = client.get("/notifications/unread-count", headers=headers)
    assert resp_count.json() == {"unread_count": 3}

    # Mark all read
    resp_all = client.post("/notifications/read-all", headers=headers)
    assert resp_all.status_code == 200
    assert resp_all.json() == {"marked_read": 3}

    # Unread count now 0
    resp_count_after = client.get("/notifications/unread-count", headers=headers)
    assert resp_count_after.json() == {"unread_count": 0}


@pytest.mark.asyncio
async def test_hot_lead_creates_persistent_notification_via_webhook(db_session, monkeypatch) -> None:
    import app.conversations.delivery as delivery
    import app.instagram.pipeline as pipeline
    from app.ai.orchestrator import ConversationTurnResult
    from app.ai.provider.base import LLMProvider

    monkeypatch.setattr(pipeline, "DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(delivery, "PART_DELAY_SECONDS", 0)

    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Hot Notif Webhook Biz")
    db_session.add(business)
    await db_session.flush()

    from app.core.security import encrypt_secret
    from app.instagram.models import InstagramAccount

    now = dt.datetime.now(dt.timezone.utc)
    account = InstagramAccount(
        business_id=business.id,
        ig_business_id="ig-hot-biz-1",
        ig_username="hotbiz",
        fb_page_id="fb-page-1",
        access_token_encrypted=encrypt_secret("tok"),
        status="connected",
        connected_at=now,
    )
    db_session.add(account)
    await db_session.commit()

    class FakeLLMProvider(LLMProvider):
        async def generate_structured(self, **kwargs):
            raise NotImplementedError

        async def run_agentic_turn(self, **kwargs):
            return ConversationTurnResult(
                reply="Rahmat, bog'lanamiz!",
                lead_status="hot",
                lead_score=90,
                qualification_reason="gave phone",
                phone_detected="+998901234567",
            )

    class FakeMetaClient:
        async def send_message(self, **kwargs):
            return {"message_id": "out-1"}

        async def get_user_profile(self, user_id, access_token):
            return {"username": "hot_customer"}

    event_id = await pipeline.ingest_event(
        db_session,
        {
            "kind": "message", "mid": "mid-hot-notif-1", "business_ig_id": "ig-hot-biz-1",
            "customer_igsid": "cust-hot-notif-1", "text": "90 123 45 67",
            "attachment_type": None, "attachment_url": None, "needs_transcription": False,
        },
    )
    await pipeline.process_event(event_id, provider=FakeLLMProvider(), meta_client=FakeMetaClient())

    # Verify notification exists in database
    notifs = (
        await db_session.execute(
            select(Notification).where(Notification.business_id == business.id)
        )
    ).scalars().all()

    assert len(notifs) == 1
    assert notifs[0].type == "lead_hot"
    assert "Yangi Issiq Lid" in notifs[0].title
    assert "+998901234567" in notifs[0].message
    assert notifs[0].is_read is False


@pytest.mark.asyncio
async def test_broadcaster_publishes_to_connected_client() -> None:
    test_biz_id = uuid.uuid4()
    queue, unsub = broadcaster.register(test_biz_id)
    try:
        payload = {"id": "n1", "title": "Test notification", "type": "system"}
        count = await broadcaster.broadcast(test_biz_id, payload)
        assert count == 1
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received == payload
    finally:
        unsub()


@pytest.mark.asyncio
async def test_sse_stream_survives_heartbeats_and_unsubscribes_on_close(db_session, monkeypatch) -> None:
    """Regression: the heartbeat timeout cancelled the subscription itself, so
    every idle stream ended right after its first ping."""
    import app.notifications.router as notif_router
    from app.core.security import create_sse_ticket

    monkeypatch.setattr(notif_router, "_HEARTBEAT_SECONDS", 0.05)
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="SSE Biz")
    db_session.add(business)
    await db_session.commit()

    response = await notif_router.stream_notifications(ticket=create_sse_ticket(str(user.id)))
    stream = response.body_iterator

    async def next_event() -> str:
        return await asyncio.wait_for(stream.__anext__(), timeout=2.0)

    assert (await next_event()).startswith("event: connected")
    assert (await next_event()).startswith("event: ping")
    assert (await next_event()).startswith("event: ping")  # still alive after a heartbeat
    await broadcaster.broadcast(business.id, {"id": "n2", "type": "lead_hot"})
    assert '"id": "n2"' in await next_event()
    assert broadcaster.subscriber_count(business.id) == 1
    await stream.aclose()
    assert broadcaster.subscriber_count(business.id) == 0


def test_sse_stream_rejects_an_access_token_as_ticket(client) -> None:
    """The stream only accepts its own short-lived ticket — never the access
    token, which used to be put in the URL (and so in access logs)."""
    headers = create_business_and_headers("sse@test.com", "SSE Biz")
    access_token = headers["Authorization"].removeprefix("Bearer ")
    assert client.get(f"/notifications/stream?ticket={access_token}").status_code == 401
    ticket = client.post("/notifications/stream-ticket", headers=headers)
    assert ticket.status_code == 200 and ticket.json()["expires_in"] == 60
