"""HTTP-level tests for Instagram connect/callback/webhook endpoints."""
import datetime as dt
import uuid
from urllib.parse import parse_qs, urlsplit

from app.ai.orchestrator import ConversationTurnResult
from app.ai.provider.base import LLMProvider
from app.ai.provider.factory import get_llm_provider
from app.instagram.client import ConnectedAccount
from app.instagram.router import get_meta_client
from app.main import app
from tests.conftest import create_business_and_headers, sign_webhook


class FakeMetaClient:
    def __init__(self, ig_business_id: str = "ig-biz-42"):
        self.sent: list[str] = []
        self._ig = ig_business_id

    def build_oauth_url(self, state: str) -> str:
        return f"https://www.instagram.com/oauth/authorize?state={state}"

    async def exchange_code_for_account(self, code: str) -> ConnectedAccount:
        return ConnectedAccount(
            access_token="fake-token",
            ig_business_id=self._ig,
            ig_username="fake_shop",
            fb_page_id="page-42",
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=60),
        )

    async def send_message(self, *, ig_business_id, access_token, recipient_id, text):
        self.sent.append(text)
        return {"message_id": f"out-{uuid.uuid4()}"}

    async def get_user_profile(self, user_id, access_token):
        return {"username": "customer_one"}


def _connect(client, headers, meta) -> str:
    """Runs connect -> callback -> complete; returns the completion id used."""
    app.dependency_overrides[get_meta_client] = lambda: meta
    state = client.post("/integrations/instagram/connect", headers=headers).json()["oauth_url"].split("state=")[1]
    callback = client.get(
        "/integrations/instagram/callback", params={"code": "abc123", "state": state}, follow_redirects=False
    )
    assert callback.status_code in (302, 307)
    location = callback.headers["location"]
    assert location.startswith("https://app.mivo.test/integrations?")
    completion_id = parse_qs(urlsplit(location).query)["instagram_pending"][0]
    done = client.post("/integrations/instagram/complete", json={"completion_id": completion_id}, headers=headers)
    assert done.status_code == 200, done.text
    return completion_id


def test_connect_returns_oauth_url_with_state(client) -> None:
    headers = create_business_and_headers("igowner@test.com", "IG Biz")
    resp = client.post("/integrations/instagram/connect", headers=headers)
    assert resp.status_code == 200
    assert "state=" in resp.json()["oauth_url"]


def test_connect_requires_auth(client) -> None:
    assert client.post("/integrations/instagram/connect").status_code == 401


def test_connect_flow_completes_through_the_owners_session(client) -> None:
    headers = create_business_and_headers("igowner@test.com", "IG Biz")
    try:
        _connect(client, headers, FakeMetaClient())
        status_resp = client.get("/integrations/instagram/status", headers=headers)
        assert status_resp.json()["username"] == "fake_shop"
    finally:
        app.dependency_overrides.pop(get_meta_client, None)


def test_completion_id_is_single_use_and_bound_to_the_business(client) -> None:
    """The OAuth-CSRF fix: an attacker who gets someone else to authorize
    their connect link can't finish it into their own business, and a
    completion id can't be replayed."""
    owner = create_business_and_headers("owner@test.com", "Owner Biz")
    attacker = create_business_and_headers("attacker@test.com", "Attacker Biz")
    app.dependency_overrides[get_meta_client] = lambda: FakeMetaClient()
    try:
        state = client.post("/integrations/instagram/connect", headers=owner).json()["oauth_url"].split("state=")[1]
        location = client.get(
            "/integrations/instagram/callback", params={"code": "c", "state": state}, follow_redirects=False
        ).headers["location"]
        completion_id = parse_qs(urlsplit(location).query)["instagram_pending"][0]

        stolen = client.post("/integrations/instagram/complete", json={"completion_id": completion_id}, headers=attacker)
        assert stolen.status_code == 400

        ok = client.post("/integrations/instagram/complete", json={"completion_id": completion_id}, headers=owner)
        assert ok.status_code == 200
        replay = client.post("/integrations/instagram/complete", json={"completion_id": completion_id}, headers=owner)
        assert replay.status_code == 400

        # The state itself is single-use too.
        again = client.get(
            "/integrations/instagram/callback", params={"code": "c2", "state": state}, follow_redirects=False
        )
        assert "instagram_error=invalid_state" in again.headers["location"]
    finally:
        app.dependency_overrides.pop(get_meta_client, None)


def test_same_instagram_account_cannot_be_linked_to_two_businesses(client) -> None:
    first = create_business_and_headers("first@test.com", "First")
    second = create_business_and_headers("second@test.com", "Second")
    try:
        _connect(client, first, FakeMetaClient())
        app.dependency_overrides[get_meta_client] = lambda: FakeMetaClient()
        state = client.post("/integrations/instagram/connect", headers=second).json()["oauth_url"].split("state=")[1]
        location = client.get(
            "/integrations/instagram/callback", params={"code": "c", "state": state}, follow_redirects=False
        ).headers["location"]
        completion_id = parse_qs(urlsplit(location).query)["instagram_pending"][0]
        resp = client.post("/integrations/instagram/complete", json={"completion_id": completion_id}, headers=second)
        assert resp.status_code == 400
        assert "boshqa biznesga" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_meta_client, None)


def test_callback_rejects_invalid_state(client) -> None:
    resp = client.get(
        "/integrations/instagram/callback", params={"code": "abc", "state": "garbage"}, follow_redirects=False
    )
    assert resp.status_code in (302, 307)
    assert "instagram_error=invalid_state" in resp.headers["location"]


def test_webhook_verification_challenge(client) -> None:
    resp = client.get(
        "/webhooks/instagram",
        params={"hub.mode": "subscribe", "hub.challenge": "12345", "hub.verify_token": "test-verify-token"},
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
    import app.conversations.delivery as delivery
    import app.instagram.pipeline as pipeline

    monkeypatch.setattr(pipeline, "DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(delivery, "PART_DELAY_SECONDS", 0)

    class FakeProvider(LLMProvider):
        async def generate_structured(self, **kwargs):
            raise NotImplementedError

        async def run_agentic_turn(self, **kwargs):
            return ConversationTurnResult(reply="Salom!", lead_status="cold", lead_score=5, qualification_reason="greeting")

    fake_meta = FakeMetaClient()
    headers = create_business_and_headers("igowner@test.com", "IG Biz")
    try:
        _connect(client, headers, fake_meta)
        app.dependency_overrides[get_llm_provider] = lambda: FakeProvider()
        body, sig_headers = sign_webhook(
            {"entry": [{"messaging": [{
                "sender": {"id": "customer-1"},
                "recipient": {"id": "ig-biz-42"},
                "message": {"mid": "mid-webhook-1", "text": "Salom"},
            }]}]}
        )
        # The turn runs as a background task after the 200 — TestClient
        # waits for it, so the reply is visible once post() returns.
        resp = client.post("/webhooks/instagram", content=body, headers=sig_headers)
        assert resp.status_code == 200
        assert fake_meta.sent == ["Salom!"]
    finally:
        app.dependency_overrides.pop(get_meta_client, None)
        app.dependency_overrides.pop(get_llm_provider, None)


def test_webhook_signature_is_required(client) -> None:
    body, headers = sign_webhook({"entry": []})
    assert client.post("/webhooks/instagram", content=body, headers=headers).status_code == 200

    tampered = client.post(
        "/webhooks/instagram", content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert tampered.status_code == 403
    missing = client.post("/webhooks/instagram", content=body, headers={"Content-Type": "application/json"})
    assert missing.status_code == 403


def test_webhook_rejects_everything_when_no_app_secret_is_configured() -> None:
    """Fails closed — an unconfigured secret used to mean 'skip verification'."""
    from app.instagram.router import verify_signature

    assert verify_signature(b"{}", "sha256=anything", app_secret="") is False
