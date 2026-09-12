"""What the model is told about media it can't see, what never reaches a
customer, and when a conversation is over."""
from types import SimpleNamespace

import pytest

from app.ai.closing import may_be_closing, unanswered_burst, valid_reaction
from app.ai.context.builder import build_message_history, unseen_shared_media
from app.ai.orchestrator import _apply_reply_guards, claims_to_see_media, strip_internal_markers
from app.ai.provider.openrouter import _message_needs_grounding


def _msg(sender="customer", content="", attachment_type=None, attachment_url=None, message_type=None):
    return SimpleNamespace(
        sender_type=sender, content=content, attachment_type=attachment_type,
        attachment_url=attachment_url, message_type=message_type or attachment_type or "text", created_at=None,
    )


def _text(m) -> str:
    return build_message_history([m])[0]["content"]


# --- notes instead of labels ---------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        _msg(attachment_type="template"),
        _msg(content="[Template yuborildi]", attachment_type="template"),  # a pre-migration row
        _msg(attachment_type="ig_reel", attachment_url="https://cdn/r.mp4"),
        _msg(content="[Reels / Story ulashildi]", attachment_type="ig_reel"),
    ],
)
def test_media_the_model_cant_see_becomes_a_note_never_a_label(message) -> None:
    text = _text(message)
    assert text.startswith("(Note: ")
    assert "yuborildi" not in text and "ulashildi" not in text and "[" not in text


def test_a_reel_caption_reaches_the_model() -> None:
    text = _text(_msg(content="Nike Tech Fleece yangi kolleksiya", attachment_type="ig_reel"))
    assert 'caption says: "Nike Tech Fleece yangi kolleksiya"' in text
    assert "cannot see" in text


def test_a_voice_transcript_is_plain_text() -> None:
    assert _text(_msg(content="qora hoodie bormi", attachment_type="audio")) == "qora hoodie bormi"


def test_our_own_photos_are_notes_too() -> None:
    assert _text(_msg(sender="ai", content="📷 Nike Hoodie", attachment_type="image")) == (
        "(Note: a photo of Nike Hoodie was sent to the customer.)"
    )
    assert _text(_msg(sender="human", attachment_type="image")) == "(Note: a photo was sent to the customer.)"


def test_unseen_shared_media() -> None:
    assert unseen_shared_media(_msg(attachment_type="ig_reel"))
    assert unseen_shared_media(_msg(content="[Reels / Story ulashildi]", attachment_type="ig_reel"))
    assert not unseen_shared_media(_msg(content="Nike Tech Fleece", attachment_type="ig_reel"))
    assert not unseen_shared_media(_msg(attachment_type="image", attachment_url="https://cdn/p.jpg"))


def test_a_note_without_a_caption_forces_no_search() -> None:
    assert not _message_needs_grounding({"content": _text(_msg(attachment_type="ig_reel"))})
    assert _message_needs_grounding({"content": _text(_msg(content="Nike Tech Fleece narxi", attachment_type="ig_reel"))})


# --- what never reaches a customer ------------------------------------------------------------


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("Kechirasiz, «Template yuborildi» deganingizni tushunmadim. Qanday yordam bera olaman?",
         "Qanday yordam bera olaman?"),
        ("[Mijoz rasm yubordi] Bu juda chiroyli!", "Bu juda chiroyli!"),
        ("(Note: the customer shared a Reel.) Qaysi mahsulot qiziqtirdi?", "Qaysi mahsulot qiziqtirdi?"),
        ("Nike Tech Fleece — 450 000 so'm. M, L, XL bor.", "Nike Tech Fleece — 450 000 so'm. M, L, XL bor."),
    ],
)
def test_strip_internal_markers(reply, expected) -> None:
    assert strip_internal_markers(reply) == expected


@pytest.mark.parametrize(
    "reply,claims",
    [
        ("Rasmdagi qora hoodie'mizning narxi 319 000 so'm.", True),
        ("Ko'rinishidan sizga hoodie'larimiz qiziq bo'ldi shekilli.", True),
        ("Videodagi krossovka bizda bor.", True),
        ("На видео наша новая коллекция.", True),
        ("Qaysi mahsulot qiziqtirdi? Rasmini yuboring.", False),
    ],
)
def test_claims_to_see_media(reply, claims) -> None:
    assert claims_to_see_media(reply) is claims


def _guard(reply, **kwargs):
    business = SimpleNamespace(
        id="b", language="uz", delivery_info=None, payment_info=None, discount_policy=None,
        rules_text=None, description=None, handoff_instructions=None,
    )
    state = {"escalated": False, "reason": None, "executed_tools": [], "active_discounts": [], "catalog_prices": []}
    return _apply_reply_guards(reply, business, state, ["salom"], **kwargs)


def test_describing_an_unseen_reel_is_replaced_with_an_honest_question() -> None:
    reply, flagged = _guard("Ko'rinishidan sizga qora hoodie yoqdi.", unseen_media=True)
    assert flagged and "ocha olmayapman" in reply and "?" in reply


def test_the_same_words_are_fine_when_the_media_was_seen() -> None:
    reply, flagged = _guard("Rasmdagi hoodie'ga o'xshashi bor.", unseen_media=False)
    assert not flagged and reply == "Rasmdagi hoodie'ga o'xshashi bor."


def test_a_leaked_label_is_cut_and_flagged() -> None:
    reply, flagged = _guard("«Template yuborildi» deganingizni tushunmadim. Nima qidiryapsiz?")
    assert flagged and reply == "Nima qidiryapsiz?"


# --- when the conversation is over -----------------------------------------------------------


def test_only_short_plain_text_after_our_reply_is_a_closing_candidate() -> None:
    ours = _msg(sender="ai", content="Rahmat! Hamkasbim bog'lanadi.")
    assert may_be_closing([_msg(content="hop")], ours)
    assert may_be_closing([_msg(content="👍")], ours)
    assert may_be_closing([_msg(content="ok"), _msg(content="rahmat")], ours)
    assert not may_be_closing([_msg(content="hop")], None)  # nothing to close yet
    assert not may_be_closing([_msg(content="narxi qancha?")], ours)
    assert not may_be_closing([_msg(content="90 123 45 67")], ours)
    assert not may_be_closing([_msg(content="rahmat, lekin boshqa rangi ham bormi deb so'ramoqchi edim")], ours)
    assert not may_be_closing([_msg(attachment_type="image", attachment_url="https://cdn/p.jpg")], ours)


def test_unanswered_burst() -> None:
    ours = _msg(sender="ai", content="Marhamat")
    burst, last = unanswered_burst([_msg(content="salom"), ours, _msg(content="ok"), _msg(content="rahmat")])
    assert [m.content for m in burst] == ["ok", "rahmat"] and last is ours


@pytest.mark.parametrize(
    "value,expected",
    [("🔥", "🔥"), (" ❤️ ", "❤️"), ("👍🏻", "👍🏻"), ("love", None), ("ok 👍", None), ("", None), (None, None)],
)
def test_valid_reaction(value, expected) -> None:
    assert valid_reaction(value) == expected


def test_closing_transcript_skips_our_reactions() -> None:
    from app.ai.closing import _transcript

    text = _transcript([
        _msg(sender="ai", content="Rahmat! Hamkasbim bog'lanadi."),
        _msg(content="hop"),
        _msg(sender="ai", content="🔥", message_type="reaction"),
        _msg(content="ok"),
    ])
    assert text == "Shop: Rahmat! Hamkasbim bog'lanadi.\nCustomer: hop\nCustomer: ok"
