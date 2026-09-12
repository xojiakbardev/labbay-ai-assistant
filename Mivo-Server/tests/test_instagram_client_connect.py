"""The connect flow's own parsing of Meta's responses (every other test fakes
exchange_code_for_account whole — which is how routing by the wrong id went
unnoticed)."""
import httpx
import pytest
import respx

from app.instagram.client import MetaAPIError, MetaClient

pytestmark = pytest.mark.asyncio


def _mock_tokens() -> None:
    respx.post("https://api.instagram.com/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "short", "user_id": 27960007420294212})
    )
    respx.get("https://graph.instagram.com/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "long", "expires_in": 5184000})
    )


@respx.mock
async def test_account_is_routed_by_the_id_webhooks_carry() -> None:
    """/me `id` is app-scoped; webhooks name the account by `user_id`."""
    _mock_tokens()
    me = respx.get("https://graph.instagram.com/v21.0/me").mock(
        return_value=httpx.Response(
            200, json={"id": "27960007420294212", "user_id": "17841454852761342", "username": "shop"}
        )
    )

    account = await MetaClient().exchange_code_for_account("code")

    assert "user_id" in me.calls[0].request.url.params["fields"]
    assert account.ig_business_id == "17841454852761342"
    assert account.fb_page_id == "27960007420294212"
    assert account.access_token == "long" and account.ig_username == "shop"


@respx.mock
async def test_connect_fails_without_the_webhook_id() -> None:
    _mock_tokens()
    respx.get("https://graph.instagram.com/v21.0/me").mock(
        return_value=httpx.Response(200, json={"id": "27960007420294212", "username": "shop"})
    )
    with pytest.raises(MetaAPIError):
        await MetaClient().exchange_code_for_account("code")
