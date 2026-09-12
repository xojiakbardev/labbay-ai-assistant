"""Unit and integration tests for Web Push subscriptions and notifications."""
import uuid
import datetime as dt
import pywebpush
import pytest
from sqlalchemy import select

from app.auth.models import User
from app.businesses.models import Business
from app.push import service as push_service
from app.push.models import PushSubscription
from tests.conftest import create_business_and_headers

pytestmark = pytest.mark.asyncio


def test_vapid_public_key_requires_auth(client) -> None:
    resp = client.get("/push/vapid-public-key")
    assert resp.status_code == 401


def test_get_vapid_public_key(client) -> None:
    headers = create_business_and_headers("push_owner1@test.com", "Push Biz 1")
    resp = client.get("/push/vapid-public-key", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "public_key" in data
    assert len(data["public_key"]) > 20


def test_push_subscribe_status_and_unsubscribe(client) -> None:
    headers = create_business_and_headers("push_owner2@test.com", "Push Biz 2")

    # Initially not subscribed
    resp_init = client.get("/push/status", headers=headers)
    assert resp_init.status_code == 200
    assert resp_init.json() == {"subscribed": False, "devices_count": 0}

    # Subscribe device 1
    sub_payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/device-token-1",
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM=",
            "auth": "tBHItJI5svbpez7KI4CCXg==",
        },
        "user_agent": "Mozilla/5.0 Chrome",
    }
    resp_sub = client.post("/push/subscribe", json=sub_payload, headers=headers)
    assert resp_sub.status_code == 200
    assert resp_sub.json() == {"subscribed": True, "devices_count": 1}

    # Subscribe device 2 (e.g. mobile)
    sub_payload_2 = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/device-token-2",
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM=",
            "auth": "tBHItJI5svbpez7KI4CCXg==",
        },
        "user_agent": "Mozilla/5.0 Safari",
    }
    resp_sub2 = client.post("/push/subscribe", json=sub_payload_2, headers=headers)
    assert resp_sub2.status_code == 200
    assert resp_sub2.json() == {"subscribed": True, "devices_count": 2}

    # Status check
    resp_status = client.get("/push/status", headers=headers)
    assert resp_status.json() == {"subscribed": True, "devices_count": 2}

    # Unsubscribe device 1
    resp_unsub = client.post(
        "/push/unsubscribe",
        json={"endpoint": "https://fcm.googleapis.com/fcm/send/device-token-1"},
        headers=headers,
    )
    assert resp_unsub.status_code == 200
    assert resp_unsub.json() == {"subscribed": True, "devices_count": 1}


def test_tenant_isolation_push(client) -> None:
    headers1 = create_business_and_headers("push_tenant1@test.com", "Push Tenant 1")
    headers2 = create_business_and_headers("push_tenant2@test.com", "Push Tenant 2")

    # Tenant 1 subscribes
    client.post(
        "/push/subscribe",
        json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/t1-device",
            "keys": {"p256dh": "k1", "auth": "a1"},
        },
        headers=headers1,
    )

    # Tenant 2 status is empty
    resp2 = client.get("/push/status", headers=headers2)
    assert resp2.json() == {"subscribed": False, "devices_count": 0}

    # Tenant 2 tries to unsubscribe Tenant 1's endpoint -> does not affect Tenant 1
    client.post(
        "/push/unsubscribe",
        json={"endpoint": "https://fcm.googleapis.com/fcm/send/t1-device"},
        headers=headers2,
    )
    resp1 = client.get("/push/status", headers=headers1)
    assert resp1.json() == {"subscribed": True, "devices_count": 1}


def test_subscription_endpoint_must_be_a_real_push_service(client) -> None:
    """The server POSTs to the endpoint: anything but a browser push service
    would turn /push/test into a way to reach internal addresses (SSRF)."""
    headers = create_business_and_headers("push_ssrf@test.com", "Push SSRF")
    for endpoint in (
        "http://169.254.169.254/latest/meta-data/",
        "http://db:5432/",
        "https://fcm.googleapis.com.evil.example/x",
        "http://fcm.googleapis.com/fcm/send/x",
        "https://fcm.googleapis.com:8443/x",
    ):
        resp = client.post(
            "/push/subscribe", json={"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}}, headers=headers
        )
        assert resp.status_code == 422, endpoint
    ok = client.post(
        "/push/subscribe",
        json={"endpoint": "https://web.push.apple.com/QGuQyavXutnMei", "keys": {"p256dh": "k", "auth": "a"}},
        headers=headers,
    )
    assert ok.status_code == 200


async def test_invalid_subscription_cleanup_on_410_gone(db_session, monkeypatch) -> None:
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Cleanup Biz")
    db_session.add(business)
    await db_session.flush()

    sub = PushSubscription(
        business_id=business.id,
        user_id=user.id,
        endpoint="https://expired.push.com/dead-token",
        p256dh="key",
        auth="auth",
    )
    db_session.add(sub)
    await db_session.commit()

    class Fake410Response:
        status_code = 410

    def fake_webpush(*args, **kwargs):
        exc = pywebpush.WebPushException("Subscription expired")
        exc.response = Fake410Response()
        raise exc

    monkeypatch.setattr(push_service, "_sync_send_webpush", fake_webpush)

    sent = await push_service.send_push_notification(
        db_session,
        business_id=business.id,
        title="Test",
        body="Test message",
    )
    assert sent == 0

    # Dead subscription was automatically purged from DB
    remaining = (
        await db_session.execute(
            select(PushSubscription).where(PushSubscription.business_id == business.id)
        )
    ).scalars().all()
    assert len(remaining) == 0


async def test_hot_lead_triggers_push_notification_via_webhook(db_session, monkeypatch) -> None:
    import app.conversations.delivery as delivery
    import app.instagram.pipeline as pipeline
    import app.notifications.owner_alerts as owner_alerts
    from app.ai.orchestrator import ConversationTurnResult
    from app.ai.provider.base import LLMProvider
    from app.core.security import encrypt_secret
    from app.instagram.models import InstagramAccount

    monkeypatch.setattr(pipeline, "DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(delivery, "PART_DELAY_SECONDS", 0)

    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Hot Push Webhook Biz")
    db_session.add(business)
    await db_session.flush()

    account = InstagramAccount(
        business_id=business.id,
        ig_business_id="ig-push-biz-1",
        ig_username="pushbiz",
        fb_page_id="fb-page-1",
        access_token_encrypted=encrypt_secret("tok"),
        status="connected",
        connected_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(account)
    await db_session.commit()

    push_calls = []

    async def fake_push(db, business_id, title, body, url=None, tag=None, data=None):
        push_calls.append({"title": title, "body": body, "url": url, "tag": tag})
        return 1

    monkeypatch.setattr(owner_alerts, "send_push_notification", fake_push)

    class FakeLLMProvider(LLMProvider):
        async def generate_structured(self, **kwargs):
            raise NotImplementedError

        async def run_agentic_turn(self, **kwargs):
            return ConversationTurnResult(
                reply="Rahmat!",
                lead_status="hot",
                lead_score=90,
                qualification_reason="gave phone",
                phone_detected="+998901234567",
            )

    class FakeMetaClient:
        async def send_message(self, **kwargs):
            return {"message_id": "out-push-1"}

        async def get_user_profile(self, user_id, access_token):
            return {"username": "push_customer"}

    event_id = await pipeline.ingest_event(
        db_session,
        {
            "kind": "message", "mid": "mid-push-hot-1", "business_ig_id": "ig-push-biz-1",
            "customer_igsid": "cust-push-hot-1", "text": "+998 90 123 45 67",
            "attachment_type": None, "attachment_url": None, "needs_transcription": False,
        },
    )
    await pipeline.process_event(event_id, provider=FakeLLMProvider(), meta_client=FakeMetaClient())

    assert len(push_calls) == 1
    assert "Yangi Issiq Lid" in push_calls[0]["title"]
    assert "+998901234567" in push_calls[0]["body"]
    assert push_calls[0]["url"].startswith("/leads?id=")
