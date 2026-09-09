"""HTTP-level tests for /integrations/telegram/connect and /webhooks/telegram."""
from app.core.config import get_settings


def _auth_headers(client, email="tgowner@test.com") -> dict:
    from tests.conftest import create_business_and_headers

    return create_business_and_headers(email, "TG Biz")


def test_connect_returns_deep_link(client) -> None:
    headers = _auth_headers(client)
    resp = client.post("/integrations/telegram/connect", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["deep_link"].startswith("https://t.me/")
    assert "?start=" in resp.json()["deep_link"]
    assert "expires_at" in resp.json()


def test_disconnect_clears_connection(client) -> None:
    headers = _auth_headers(client, email="tgdisconnect@test.com")
    deep_link = client.post("/integrations/telegram/connect", headers=headers).json()["deep_link"]
    token = deep_link.split("?start=")[1]
    client.post(
        "/webhooks/telegram",
        json={"message": {"text": f"/start {token}", "chat": {"id": 777, "username": "owner_tg"}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": get_settings().telegram_webhook_secret},
    )
    assert client.get("/integrations/telegram/status", headers=headers).json()["connected"] is True

    resp = client.delete("/integrations/telegram/disconnect", headers=headers)
    assert resp.status_code == 204
    assert client.get("/integrations/telegram/status", headers=headers).json()["connected"] is False


def test_disconnect_requires_auth(client) -> None:
    resp = client.delete("/integrations/telegram/disconnect")
    assert resp.status_code == 401


def test_connect_requires_auth(client) -> None:
    resp = client.post("/integrations/telegram/connect")
    assert resp.status_code == 401


def test_webhook_rejects_wrong_secret(client) -> None:
    resp = client.post(
        "/webhooks/telegram",
        json={"message": {"text": "/start abc", "chat": {"id": 1}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )
    assert resp.status_code == 403


def test_webhook_start_command_connects_business(client) -> None:
    headers = _auth_headers(client, email="tgstart@test.com")
    deep_link = client.post("/integrations/telegram/connect", headers=headers).json()["deep_link"]
    token = deep_link.split("?start=")[1]

    resp = client.post(
        "/webhooks/telegram",
        json={"message": {"text": f"/start {token}", "chat": {"id": 555, "username": "owner_tg"}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": get_settings().telegram_webhook_secret},
    )
    assert resp.status_code == 200
