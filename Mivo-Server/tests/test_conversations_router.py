"""HTTP-level tests for /conversations."""
import datetime as dt
import uuid

import pytest

from app.ai.orchestrator import ConversationTurnResult
from app.ai.provider.base import LLMProvider
from app.ai.provider.factory import get_llm_provider
from app.instagram.client import ConnectedAccount, MetaAPIError
from app.instagram.router import get_meta_client
from app.conversations.router import get_meta_client as conversations_meta_client
from app.main import app
from tests.conftest import create_business_and_headers, sign_webhook


class FakeProvider(LLMProvider):
    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(self, **kwargs):
        return ConversationTurnResult(reply="Salom!", lead_status="cold", lead_score=5, qualification_reason="greeting")


class FakeMetaClient:
    def __init__(self, fail: bool = False):
        self.sent: list[str] = []
        self.fail = fail

    def build_oauth_url(self, state):
        return f"https://x?state={state}"

    async def exchange_code_for_account(self, code):
        return ConnectedAccount(
            access_token="t", ig_business_id="ig-conv-1", ig_username="u",
            fb_page_id="p", expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=60),
        )

    async def send_message(self, *, ig_business_id, access_token, recipient_id, text):
        if self.fail:
            raise MetaAPIError("24 soatlik oyna yopilgan")
        self.sent.append(text)
        return {"message_id": f"out-{uuid.uuid4()}"}

    async def get_user_profile(self, user_id, access_token):
        return {"username": "cust"}


@pytest.fixture
def connected(client, monkeypatch):
    """A business with Instagram connected and one inbound message answered."""
    import app.conversations.delivery as delivery
    import app.instagram.pipeline as pipeline
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setattr(pipeline, "DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(delivery, "PART_DELAY_SECONDS", 0)
    meta = FakeMetaClient()
    app.dependency_overrides[get_llm_provider] = lambda: FakeProvider()
    app.dependency_overrides[get_meta_client] = lambda: meta
    app.dependency_overrides[conversations_meta_client] = lambda: meta
    headers = create_business_and_headers("convflow@test.com", "Conv Biz")
    state = client.post("/integrations/instagram/connect", headers=headers).json()["oauth_url"].split("state=")[1]
    location = client.get(
        "/integrations/instagram/callback", params={"code": "c", "state": state}, follow_redirects=False
    ).headers["location"]
    completion_id = parse_qs(urlsplit(location).query)["instagram_pending"][0]
    assert client.post("/integrations/instagram/complete", json={"completion_id": completion_id}, headers=headers).status_code == 200

    body, sig = sign_webhook(
        {"entry": [{"messaging": [{
            "sender": {"id": "cust-conv-1"}, "recipient": {"id": "ig-conv-1"},
            "message": {"mid": "mid-conv-1", "text": "Salom"},
        }]}]}
    )
    assert client.post("/webhooks/instagram", content=body, headers=sig).status_code == 200
    yield headers, meta
    for dep in (get_llm_provider, get_meta_client, conversations_meta_client):
        app.dependency_overrides.pop(dep, None)


def test_list_conversations_empty_initially(client) -> None:
    headers = create_business_and_headers("convowner@test.com", "Conv Biz")
    resp = client.get("/conversations", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_conversations_require_auth(client) -> None:
    assert client.get("/conversations").status_code == 401


def test_get_conversation_not_found(client) -> None:
    headers = create_business_and_headers("convowner@test.com", "Conv Biz")
    assert client.get(f"/conversations/{uuid.uuid4()}", headers=headers).status_code == 404


def test_conversation_appears_after_incoming_message(client, connected) -> None:
    headers, _meta = connected
    listed = client.get("/conversations", headers=headers).json()
    assert len(listed) == 1
    detail = client.get(f"/conversations/{listed[0]['id']}", headers=headers).json()
    assert [m["sender_type"] for m in detail["messages"]] == ["customer", "ai"]
    assert detail["messages"][1]["delivery_status"] == "sent"
    assert detail["has_more_messages"] is False


def test_conversation_list_is_paginated(client, connected) -> None:
    headers, _ = connected
    assert len(client.get("/conversations?limit=1&offset=0", headers=headers).json()) == 1
    assert client.get("/conversations?limit=1&offset=1", headers=headers).json() == []
    assert client.get("/conversations?limit=1000", headers=headers).status_code == 422


def test_messages_after_returns_only_newer_messages(client, connected) -> None:
    headers, _ = connected
    conv_id = client.get("/conversations", headers=headers).json()[0]["id"]
    first, second = client.get(f"/conversations/{conv_id}", headers=headers).json()["messages"]
    newer = client.get(f"/conversations/{conv_id}/messages?after={first['id']}", headers=headers).json()
    assert [m["id"] for m in newer] == [second["id"]]
    assert client.get(f"/conversations/{conv_id}/messages?after={second['id']}", headers=headers).json() == []


def test_status_only_accepts_known_values(client, connected) -> None:
    headers, _ = connected
    conv_id = client.get("/conversations", headers=headers).json()[0]["id"]
    assert client.patch(f"/conversations/{conv_id}/status?status_value=ai-active", headers=headers).status_code == 422
    ok = client.patch(f"/conversations/{conv_id}/status?status_value=human_active", headers=headers)
    assert ok.status_code == 200 and ok.json()["status"] == "human_active"


def test_operator_reply_is_recorded_sent_and_takes_over(client, connected) -> None:
    headers, meta = connected
    conv_id = client.get("/conversations", headers=headers).json()[0]["id"]
    resp = client.post(f"/conversations/{conv_id}/reply", json={"content": "Men operatorman"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["delivery_status"] == "sent"
    assert meta.sent[-1] == "Men operatorman"
    assert client.get(f"/conversations/{conv_id}", headers=headers).json()["status"] == "human_active"


def test_failed_operator_reply_stays_visible_as_failed(client, connected) -> None:
    headers, meta = connected
    meta.fail = True
    conv_id = client.get("/conversations", headers=headers).json()[0]["id"]
    resp = client.post(f"/conversations/{conv_id}/reply", json={"content": "Salom"}, headers=headers)
    assert resp.status_code == 400
    last = client.get(f"/conversations/{conv_id}", headers=headers).json()["messages"][-1]
    assert (last["sender_type"], last["delivery_status"]) == ("human", "failed")
    assert "24 soatlik" in last["delivery_error"]


def test_deleting_a_conversation_keeps_the_lead(client, connected) -> None:
    headers, _ = connected
    conv_id = client.get("/conversations", headers=headers).json()[0]["id"]
    leads_before = client.get("/leads", headers=headers).json()
    assert len(leads_before) == 1
    assert client.delete(f"/conversations/{conv_id}", headers=headers).status_code == 204
    leads_after = client.get("/leads", headers=headers).json()
    assert len(leads_after) == 1 and leads_after[0]["conversation_id"] is None
