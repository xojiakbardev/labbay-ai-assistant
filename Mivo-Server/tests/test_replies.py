"""Ready-made replies: defaults, and the owner's own wording from the AI settings page."""
from types import SimpleNamespace

from app.ai.replies import DEFAULT_REPLIES, reply_text
from tests.conftest import create_business_and_headers


def test_default_unless_the_business_wrote_its_own() -> None:
    plain = SimpleNamespace(reply_texts={})
    assert reply_text(plain, "handoff", "ru") == DEFAULT_REPLIES["handoff"]["ru"]

    custom = SimpleNamespace(reply_texts={"handoff": {"uz": "Menejerimiz hozir yozadi 🙌"}})
    assert reply_text(custom, "handoff", "uz") == "Menejerimiz hozir yozadi 🙌"
    assert reply_text(custom, "handoff", "en") == DEFAULT_REPLIES["handoff"]["en"]


def test_placeholders_are_filled() -> None:
    business = SimpleNamespace(reply_texts={"follow_up": {"uz": "{product} hali ham sizni kutyapti!"}})
    assert reply_text(business, "follow_up", "uz", product="Nike Air Max") == "Nike Air Max hali ham sizni kutyapti!"


def test_owner_edits_reply_texts_from_the_dashboard(client) -> None:
    headers = create_business_and_headers("replies@test.com", "Replies Biz")

    defaults = client.get("/business/reply-defaults", headers=headers).json()
    assert defaults["replies"]["handoff"]["uz"] == DEFAULT_REPLIES["handoff"]["uz"]
    assert defaults["languages"] == ["uz", "ru", "en"]

    body = client.patch(
        "/business",
        json={"reply_texts": {"handoff": {"uz": "  Menejer yozadi  ", "ru": ""}}},
        headers=headers,
    ).json()
    assert body["reply_texts"] == {"handoff": {"uz": "Menejer yozadi"}}  # trimmed; empty = default

    for bad in ({"no_such_reply": {"uz": "x"}}, {"handoff": {"de": "x"}}, {"handoff": {"uz": "x" * 501}}):
        assert client.patch("/business", json={"reply_texts": bad}, headers=headers).status_code == 422
