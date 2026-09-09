"""HTTP-level tests for GET /conversations and GET /conversations/{id}."""
import uuid


def _auth_headers(client, email="convowner@test.com") -> dict:
    from tests.conftest import create_business_and_headers

    return create_business_and_headers(email, "Conv Biz")


def test_list_conversations_empty_initially(client) -> None:
    headers = _auth_headers(client)
    resp = client.get("/conversations", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_conversations_require_auth(client) -> None:
    resp = client.get("/conversations")
    assert resp.status_code == 401


def test_get_conversation_not_found(client) -> None:
    headers = _auth_headers(client)
    resp = client.get(f"/conversations/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


def test_conversation_appears_after_incoming_message(client) -> None:
    from app.ai.orchestrator import ConversationTurnResult
    from app.ai.provider.base import LLMProvider
    from app.ai.provider.factory import get_llm_provider
    from app.instagram.client import ConnectedAccount
    from app.instagram.router import get_meta_client
    from app.main import app
    import datetime as dt

    class FakeProvider(LLMProvider):
        async def generate_structured(self, **kwargs):
            raise NotImplementedError

        async def run_agentic_turn(self, **kwargs):
            return ConversationTurnResult(
                reply="Salom!", lead_status="cold", lead_score=5, qualification_reason="greeting"
            )

    class FakeMetaClient:
        def build_oauth_url(self, state):
            return f"https://x?state={state}"

        async def exchange_code_for_account(self, code):
            return ConnectedAccount(
                access_token="t", ig_business_id="ig-conv-1", ig_username="u",
                fb_page_id="p", expires_at=dt.datetime.now(dt.timezone.utc),
            )

        async def send_message(self, **kwargs):
            pass

    app.dependency_overrides[get_llm_provider] = lambda: FakeProvider()
    app.dependency_overrides[get_meta_client] = lambda: FakeMetaClient()
    try:
        headers = _auth_headers(client, email="convflow@test.com")
        connect = client.post("/integrations/instagram/connect", headers=headers).json()
        state = connect["oauth_url"].split("state=")[1]
        client.get(
            "/integrations/instagram/callback",
            params={"code": "c", "state": state},
            follow_redirects=False,
        )

        client.post(
            "/webhooks/instagram",
            json={
                "entry": [{"messaging": [{
                    "sender": {"id": "cust-conv-1"}, "recipient": {"id": "ig-conv-1"},
                    "message": {"mid": "mid-conv-1", "text": "Salom"},
                }]}]
            },
        )

        listed = client.get("/conversations", headers=headers).json()
        assert len(listed) == 1
        detail = client.get(f"/conversations/{listed[0]['id']}", headers=headers).json()
        assert len(detail["messages"]) == 2  # customer message + AI reply
        assert detail["messages"][0]["sender_type"] == "customer"
        assert detail["messages"][1]["sender_type"] == "ai"
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
        app.dependency_overrides.pop(get_meta_client, None)
