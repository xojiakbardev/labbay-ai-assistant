"""HTTP-level tests for Instagram connect/callback/webhook endpoints."""
import datetime as dt

from app.instagram.client import ConnectedAccount
from app.instagram.router import get_meta_client
from app.main import app


def _auth_headers(client, email="igowner@test.com") -> dict:
    from tests.conftest import create_business_and_headers

    return create_business_and_headers(email, "IG Biz")


class FakeMetaClient:
    def build_oauth_url(self, state: str) -> str:
        return f"https://www.instagram.com/oauth/authorize?state={state}"

    async def exchange_code_for_account(self, code: str) -> ConnectedAccount:
        return ConnectedAccount(
            access_token="fake-token",
            ig_business_id="ig-biz-42",
            ig_username="fake_shop",
            fb_page_id="page-42",
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=60),
        )


def test_connect_returns_oauth_url_with_signed_state(client) -> None:
    headers = _auth_headers(client)
    resp = client.post("/integrations/instagram/connect", headers=headers)
    assert resp.status_code == 200
    assert "oauth_url" in resp.json()
    assert "state=" in resp.json()["oauth_url"]


def test_connect_requires_auth(client) -> None:
    resp = client.post("/integrations/instagram/connect")
    assert resp.status_code == 401


def test_callback_completes_connection(client) -> None:
    app.dependency_overrides[get_meta_client] = lambda: FakeMetaClient()
    try:
        headers = _auth_headers(client)
        connect_resp = client.post("/integrations/instagram/connect", headers=headers).json()
        state = connect_resp["oauth_url"].split("state=")[1]

        # The callback is a browser redirect back to the dashboard (never JSON
        # — see app/instagram/router.py), so don't follow it: assert on the
        # redirect itself, then confirm the connection via /status.
        callback_resp = client.get(
            "/integrations/instagram/callback",
            params={"code": "abc123", "state": state},
            follow_redirects=False,
        )
        assert callback_resp.status_code in (302, 307)
        assert "instagram_connected=1" in callback_resp.headers["location"]

        status_resp = client.get("/integrations/instagram/status", headers=headers)
        assert status_resp.json()["username"] == "fake_shop"
    finally:
        app.dependency_overrides.pop(get_meta_client, None)


def test_callback_rejects_invalid_state(client) -> None:
    resp = client.get(
        "/integrations/instagram/callback",
        params={"code": "abc", "state": "garbage"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "instagram_error=invalid_state" in resp.headers["location"]


def test_webhook_verification_challenge(client) -> None:
    from app.core.config import get_settings

    resp = client.get(
        "/webhooks/instagram",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "12345",
            "hub.verify_token": get_settings().meta_webhook_verify_token,
        },
    )
    assert resp.status_code == 200
    assert resp.text == "12345"


def test_webhook_verification_rejects_wrong_token(client) -> None:
    resp = client.get(
        "/webhooks/instagram",
        params={"hub.mode": "subscribe", "hub.challenge": "12345", "hub.verify_token": "wrong"},
    )
    assert resp.status_code == 403


def test_webhook_post_processes_message_event(client, monkeypatch) -> None:
    from app.ai.provider.base import LLMProvider
    from app.ai.provider.factory import get_llm_provider
    from app.ai.orchestrator import ConversationTurnResult
    import app.instagram.service as service_module

    # See app/instagram/service.py:DEBOUNCE_SECONDS — skip the real wait in tests.
    monkeypatch.setattr(service_module, "DEBOUNCE_SECONDS", 0)

    class FakeProvider(LLMProvider):
        async def generate_structured(self, **kwargs):
            raise NotImplementedError

        async def run_agentic_turn(self, **kwargs):
            return ConversationTurnResult(
                reply="Salom!", lead_status="cold", lead_score=5, qualification_reason="greeting"
            )

    class SendingFakeMetaClient(FakeMetaClient):
        def __init__(self):
            self.sent = []

        async def send_message(self, *, ig_business_id, access_token, recipient_id, text):
            self.sent.append(text)

    fake_meta = SendingFakeMetaClient()
    app.dependency_overrides[get_meta_client] = lambda: fake_meta
    app.dependency_overrides[get_llm_provider] = lambda: FakeProvider()
    try:
        headers = _auth_headers(client)
        connect_resp = client.post("/integrations/instagram/connect", headers=headers).json()
        state = connect_resp["oauth_url"].split("state=")[1]
        client.get(
            "/integrations/instagram/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

        webhook_body = {
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": "customer-1"},
                            "recipient": {"id": "ig-biz-42"},
                            "message": {"mid": "mid-webhook-1", "text": "Salom"},
                        }
                    ]
                }
            ]
        }
        resp = client.post("/webhooks/instagram", json=webhook_body)
        assert resp.status_code == 200
        assert fake_meta.sent == ["Salom!"]
    finally:
        app.dependency_overrides.pop(get_meta_client, None)
        app.dependency_overrides.pop(get_llm_provider, None)


def test_webhook_signature_verification_rejects_tampered_payload(client, monkeypatch) -> None:
    import hashlib
    import hmac

    from app.core.config import get_settings

    monkeypatch.setenv("META_APP_SECRET", "test-app-secret")
    get_settings.cache_clear()
    try:
        body = b'{"entry": []}'
        good_sig = "sha256=" + hmac.new(b"test-app-secret", body, hashlib.sha256).hexdigest()

        ok = client.post(
            "/webhooks/instagram", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": good_sig},
        )
        assert ok.status_code == 200

        bad = client.post(
            "/webhooks/instagram", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=deadbeef"},
        )
        assert bad.status_code == 403

        missing = client.post(
            "/webhooks/instagram", content=body, headers={"Content-Type": "application/json"}
        )
        assert missing.status_code == 403
    finally:
        monkeypatch.delenv("META_APP_SECRET", raising=False)
        get_settings.cache_clear()
