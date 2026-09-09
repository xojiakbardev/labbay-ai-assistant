"""Tests for AI Sandbox endpoints: GET /ai/sandbox, POST /ai/sandbox/message, POST /ai/sandbox/reset."""
import uuid
import pytest
from app.ai.orchestrator import ConversationTurnResult
from app.ai.provider.base import LLMProvider
from app.ai.provider.factory import get_llm_provider
from app.main import app
from tests.conftest import create_business_and_headers


class MockSandboxProvider(LLMProvider):
    def __init__(self, reply="Salom! Sizga qanday krossovka kerak?", lead_status="warm", score=50):
        self.reply = reply
        self.lead_status = lead_status
        self.score = score

    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(
        self, *, system_prompt, messages, tools, response_schema, tool_executor, max_tool_calls=4, business_id=None
    ):
        # Call search_products tool to simulate realistic execution
        await tool_executor("search_products", {"query": "krossovka"})
        return ConversationTurnResult(
            reply=self.reply,
            lead_status=self.lead_status,
            lead_score=self.score,
            qualification_reason="Customer asked about sneakers",
            phone_detected="+998901234567" if self.lead_status == "hot" else None,
            extracted_facts=["razmer 42", "oq rang"],
            interested_product_ids=[],
        )


def test_sandbox_initial_state(client):
    headers = create_business_and_headers(f"owner_{uuid.uuid4().hex[:6]}@test.com", "Sandbox Biz")
    resp = client.get("/ai/sandbox", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "conversation_id" in data
    assert "messages" in data
    assert isinstance(data["messages"], list)


def test_sandbox_message_turn_and_reset(client):
    headers = create_business_and_headers(f"owner_{uuid.uuid4().hex[:6]}@test.com", "Sandbox Biz 2")
    mock_provider = MockSandboxProvider(reply="Bizda Nike krossovkalar bor, narxi 350,000 so'm", lead_status="warm", score=55)
    app.dependency_overrides[get_llm_provider] = lambda: mock_provider

    try:
        # 1. Send simulated customer message
        resp = client.post(
            "/ai/sandbox/message",
            headers=headers,
            json={"content": "Krossovkalar bormi?", "simulate_telegram": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"] == "Bizda Nike krossovkalar bor, narxi 350,000 so'm"
        assert data["lead_status"] == "warm"
        assert data["lead_score"] == 55
        assert len(data["executed_tools"]) > 0
        assert data["executed_tools"][0]["name"] == "search_products"
        assert "razmer 42" in data["extracted_facts"]
        assert len(data["messages"]) >= 2  # Customer message + AI reply

        # 2. Check state reflects message
        state_resp = client.get("/ai/sandbox", headers=headers)
        assert state_resp.status_code == 200
        state_data = state_resp.json()
        assert len(state_data["messages"]) >= 2
        assert state_data["lead_status"] == "warm"
        assert state_data["lead_score"] == 55

        # 3. Reset sandbox
        reset_resp = client.post("/ai/sandbox/reset", headers=headers)
        assert reset_resp.status_code == 200
        assert reset_resp.json()["status"] == "ok"

        # 4. State should now be empty
        state_after_reset = client.get("/ai/sandbox", headers=headers).json()
        assert len(state_after_reset["messages"]) == 0
        assert state_after_reset["lead_status"] is None
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
