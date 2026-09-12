"""Phase 11 — the mandatory end-to-end test (plan §19/§24):

business created -> Instagram connected -> Telegram connected -> products added
(manual + AI-import) -> AI configured -> customer sends Instagram DM -> Mivo
retrieves relevant product -> AI replies -> customer shows purchase intent ->
AI asks for phone -> customer provides phone -> HOT lead created -> Telegram
notification sent.

All external services (Meta, Telegram, OpenRouter) are mocked/stubbed — no real
credentials are available in dev, per the plan's guidance.
"""
import datetime as dt

from app.ai.orchestrator import ConversationTurnResult
from app.ai.provider.base import LLMProvider
from app.ai.provider.factory import get_llm_provider
from app.instagram.client import ConnectedAccount
from app.instagram.router import get_meta_client
from app.main import app
from app.products.ingestion.schemas import RawExtractedProduct, RawExtractedProductList


class FakeMetaClient:
    def __init__(self):
        self.sent_messages: list[str] = []

    def build_oauth_url(self, state: str) -> str:
        return f"https://www.instagram.com/oauth/authorize?state={state}"

    async def exchange_code_for_account(self, code: str) -> ConnectedAccount:
        return ConnectedAccount(
            access_token="fake-ig-token",
            ig_business_id="ig-biz-e2e",
            ig_username="e2e_shop",
            fb_page_id="page-e2e",
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=60),
        )

    async def send_message(self, *, ig_business_id, access_token, recipient_id, text):
        self.sent_messages.append(text)
        return {"message_id": f"out-{len(self.sent_messages)}"}

    async def get_user_profile(self, user_id, access_token):
        return {"username": "e2e_customer"}


class FakeTelegramClient:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str, reply_markup: dict | None = None, **kwargs) -> dict:
        self.sent.append((chat_id, text))
        return {"ok": True}


def _post_webhook(client, mid: str, text: str):
    from tests.conftest import sign_webhook

    body, headers = sign_webhook(
        {"entry": [{"messaging": [{
            "sender": {"id": "customer-e2e-1"},
            "recipient": {"id": "ig-biz-e2e"},
            "message": {"mid": mid, "text": text},
        }]}]}
    )
    return client.post("/webhooks/instagram", content=body, headers=headers)


class ScriptedProvider(LLMProvider):
    """One provider standing in for the whole LLM surface: structured extraction
    (product import) and the conversational tool loop (two turns: warm, then hot
    once a phone number appears in the latest customer message)."""

    async def generate_structured(self, *, system_prompt, user_content, response_schema, business_id=None):
        return RawExtractedProductList(
            products=[
                RawExtractedProduct(
                    name="Nike Hoodie", description="Issiq oversize hoodie", price=250000,
                    currency="so'm", colors=["Qora"], sizes=["M"], availability=True,
                )
            ]
        )

    async def run_agentic_turn(
        self, *, system_prompt, messages, tools, response_schema, tool_executor, max_tool_calls=4, business_id=None
    ):
        last_user_message = messages[-1]["content"]
        # Exercise a real retrieval tool call, like a real model would.
        await tool_executor("search_products", {"query": "hoodie"})

        if "+998" in last_user_message:
            return ConversationTurnResult(
                reply="Rahmat! Tez orada siz bilan bog'lanamiz.",
                lead_status="hot", lead_score=92,
                qualification_reason="Customer confirmed purchase and provided phone number.",
                phone_detected=last_user_message.strip(),
            )
        return ConversationTurnResult(
            reply="Hoodie 250 000 so'm, qora M razmer mavjud. Olishni xohlaysizmi?",
            lead_status="warm", lead_score=55,
            qualification_reason="Customer asked about a specific product and variant.",
        )


def test_full_mvp_flow_end_to_end(client, monkeypatch) -> None:
    from urllib.parse import parse_qs, urlsplit

    import app.conversations.delivery as delivery
    import app.instagram.pipeline as pipeline

    # Skip the real debounce / typing pauses in tests.
    monkeypatch.setattr(pipeline, "DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(delivery, "PART_DELAY_SECONDS", 0)

    fake_meta = FakeMetaClient()
    fake_telegram = FakeTelegramClient()
    app.dependency_overrides[get_meta_client] = lambda: fake_meta
    app.dependency_overrides[get_llm_provider] = lambda: ScriptedProvider()

    import app.leads.notifications as notifications_module

    original_get_client = notifications_module.get_telegram_client
    notifications_module.get_telegram_client = lambda: fake_telegram

    try:
        # 1. Superadmin onboards the business owner (no self-serve signup).
        from tests.conftest import create_business_and_headers

        headers = create_business_and_headers("e2e@test.com", "E2E Shop")

        # 2. Connect Instagram.
        connect_ig = client.post("/integrations/instagram/connect", headers=headers).json()
        ig_state = connect_ig["oauth_url"].split("state=")[1]
        callback = client.get(
            "/integrations/instagram/callback",
            params={"code": "abc", "state": ig_state},
            follow_redirects=False,
        )
        assert callback.status_code in (302, 307)
        completion_id = parse_qs(urlsplit(callback.headers["location"]).query)["instagram_pending"][0]
        completed = client.post(
            "/integrations/instagram/complete", json={"completion_id": completion_id}, headers=headers
        )
        assert completed.status_code == 200 and completed.json()["connected"] is True

        # 3. Connect Telegram.
        connect_tg = client.post("/integrations/telegram/connect", headers=headers).json()
        tg_token = connect_tg["deep_link"].split("?start=")[1]
        from app.core.config import get_settings

        start_resp = client.post(
            "/webhooks/telegram",
            json={"message": {"text": f"/start {tg_token}", "chat": {"id": 777, "username": "owner_tg"}}},
            headers={"X-Telegram-Bot-Api-Secret-Token": get_settings().telegram_webhook_secret},
        )
        assert start_resp.status_code == 200

        # 4a. Add a product manually.
        manual_product = client.post(
            "/products", json={"name": "Adidas Cap", "price": 80000}, headers=headers
        )
        assert manual_product.status_code == 201

        # 4b. Add a product via AI-assisted import (extract -> preview -> confirm).
        preview = client.post(
            "/products/import/preview", json={"text": "Nike Hoodie 250 000 so'm"}, headers=headers
        )
        assert preview.status_code == 200
        confirm = client.post(
            "/products/import/confirm", json={"products": preview.json()["products"]}, headers=headers
        )
        assert confirm.status_code == 201
        assert confirm.json()[0]["source"] == "ai_import"

        products = client.get("/products", headers=headers).json()
        assert len(products) == 2

        # 5. Configure AI selling behavior.
        settings_resp = client.patch(
            "/business",
            json={
                "tone": "Friendly and concise",
                "selling_approach": "Ask relevant questions, move toward a purchase without being pushy.",
                "rules_text": "Never invent prices. Ask for phone only when purchase intent is strong.",
            },
            headers=headers,
        )
        assert settings_resp.status_code == 200

        # 6. Customer sends a first Instagram DM — generic product interest.
        resp1 = _post_webhook(client, "mid-e2e-1", "Salom, hoodie bormi?")
        assert resp1.status_code == 200
        assert fake_meta.sent_messages == ["Hoodie 250 000 so'm, qora M razmer mavjud. Olishni xohlaysizmi?"]

        # Not yet a hot lead — no Telegram notification.
        assert fake_telegram.sent == []
        leads_after_first = client.get("/leads", headers=headers).json()
        assert len(leads_after_first) == 1
        assert leads_after_first[0]["status"] == "warm"

        # 7. Customer shows purchase intent and provides a phone number.
        resp2 = _post_webhook(client, "mid-e2e-2", "+998901234567")
        assert resp2.status_code == 200

        # 8. HOT lead created and persisted.
        leads_after_second = client.get("/leads", headers=headers).json()
        assert len(leads_after_second) == 1  # same customer -> same lead, updated in place
        hot_lead = leads_after_second[0]
        assert hot_lead["status"] == "hot"
        assert hot_lead["phone"] == "+998901234567"

        # 9. Telegram notification reached the owner, exactly once.
        assert len(fake_telegram.sent) == 1
        chat_id, notification_text = fake_telegram.sent[0]
        assert chat_id == "777"
        assert "+998901234567" in notification_text
        assert "HOT LEAD" in notification_text

        # 10. A third message from the same now-hot customer must not re-notify.
        _post_webhook(client, "mid-e2e-3", "Salom yana")
        assert len(fake_telegram.sent) == 1  # still just one notification
    finally:
        app.dependency_overrides.pop(get_meta_client, None)
        app.dependency_overrides.pop(get_llm_provider, None)
        notifications_module.get_telegram_client = original_get_client
