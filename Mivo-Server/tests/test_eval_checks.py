"""The eval harness's own tests.

An eval that quietly passes everything is worse than no eval, so each check is
pinned from both sides: it fires on the failure it exists for, and it stays
quiet on a reply that is merely good.
"""
import pytest

from evals.checks import (
    CheckContext,
    Finding,
    check_asks_for_phone,
    check_does_not_deflect,
    check_does_not_reask_known_slots,
    check_language_matches,
    check_no_ai_boilerplate,
    check_no_internal_leakage,
    check_no_unverified_discount,
    check_prices_are_grounded,
    check_shape,
    run_checks,
    summarise,
)
from evals.scenarios import SCENARIOS, by_key

CATALOG = {780000.0, 520000.0, 450000.0}


def _ctx(**over) -> CheckContext:
    base = dict(customer_message="narxi qancha?", business_language="uz", catalog_prices=set(CATALOG))
    base.update(over)
    return CheckContext(**base)


# --- price grounding: the check that protects real money -------------------


def test_invented_price_is_caught() -> None:
    findings = check_prices_are_grounded("Nike Air Max — 690 000 so'm.", _ctx())
    assert [f.check for f in findings] == ["invented_price"]
    assert "690000" in findings[0].detail


def test_real_price_passes_however_it_is_written() -> None:
    for reply in ("Narxi 780000 so'm", "Narxi 780 000 so'm", "780 000 so'mdan", "2 tasi 1 560 000 so'm"):
        assert check_prices_are_grounded(reply, _ctx()) == []


def test_sizes_are_not_mistaken_for_prices() -> None:
    assert check_prices_are_grounded("42 va 44 razmerlari bor.", _ctx()) == []


def test_price_check_stays_quiet_without_a_known_catalog() -> None:
    """No catalog means no basis to call a number invented."""
    assert check_prices_are_grounded("Narxi 999 999 so'm", _ctx(catalog_prices=set())) == []


# --- internal leakage: what must never reach a customer --------------------


@pytest.mark.parametrize(
    "reply",
    [
        "PLAN: quote the price\n===MESSAGE===\nNarxi 780 000",
        "PLAN: greet them warmly",
        "Mahsulot id: 11111111-1111-1111-1111-111111111111",
        "search_products chaqirdim, natija yo'q",
        'Javob: {"reply": "salom"}',
    ],
)
def test_internal_leakage_is_caught(reply: str) -> None:
    assert check_no_internal_leakage(reply, _ctx())


def test_a_clean_reply_leaks_nothing() -> None:
    assert check_no_internal_leakage("Nike Air Max — 780 000 so'm. Qaysi razmer kerak?", _ctx()) == []


def test_ai_boilerplate_is_caught() -> None:
    assert check_no_ai_boilerplate("As an AI, I cannot confirm that.", _ctx())
    assert check_no_ai_boilerplate("Men sun'iy intellektman, yordam beraman.", _ctx())


def test_owning_up_to_being_an_ai_when_asked_is_not_boilerplate() -> None:
    """The prompt requires an honest answer here — flagging it would train the
    assistant to dodge the question."""
    ctx = _ctx(customer_message="siz odammisiz yoki bot?")
    assert check_no_ai_boilerplate("Men sun'iy intellektman. Nima qidiryapsiz?", ctx) == []


# --- language matching -----------------------------------------------------


def test_russian_question_answered_in_uzbek_is_caught() -> None:
    ctx = _ctx(customer_message="Здравствуйте, сколько стоит?")
    findings = check_language_matches("Assalomu alaykum! Narxi 780 000 so'm.", ctx)
    assert [f.check for f in findings] == ["language"]


def test_russian_question_answered_in_russian_passes() -> None:
    ctx = _ctx(customer_message="Здравствуйте, сколько стоит?")
    assert check_language_matches("Здравствуйте! Цена 780 000 сум.", ctx) == []


def test_uzbek_question_answered_in_uzbek_passes() -> None:
    ctx = _ctx(customer_message="Salom, narxi qancha?")
    assert check_language_matches("Assalomu alaykum! Narxi 780 000 so'm.", ctx) == []


# --- discounts: delegates to the runtime's own guard -----------------------


def test_invented_discount_is_caught() -> None:
    findings = check_no_unverified_discount("Sizga 20% chegirma qilib beramiz!", _ctx())
    assert [f.check for f in findings] == ["unverified_discount"]


def test_honest_no_discount_passes() -> None:
    assert check_no_unverified_discount("Hozircha chegirma yo'q, kechirasiz.", _ctx()) == []


def test_verified_discount_passes() -> None:
    ctx = _ctx(
        executed_tools=[{"name": "get_active_discounts", "arguments": {}}],
        active_discounts=[{"type": "percent", "value": 10.0}],
    )
    assert check_no_unverified_discount("Ayni paytda 10% chegirma bor.", ctx) == []


# --- deflection, slots, phone ----------------------------------------------


def test_telling_the_customer_to_rephrase_is_caught() -> None:
    assert check_does_not_deflect("Topa olmadim, boshqacha yozib ko'ring.", _ctx())
    assert check_does_not_deflect("Aynan o'sha yo'q, lekin juda o'xshashi bor.", _ctx()) == []


def test_re_asking_a_known_slot_is_caught() -> None:
    ctx = _ctx(known_slots={"size": "42"})
    findings = check_does_not_reask_known_slots("Qaysi razmer kerak edi?", ctx)
    assert [f.check for f in findings] == ["reasked_slot"]

    # Mentioning the size while using it is fine — it's the question that's wrong.
    assert check_does_not_reask_known_slots("42 razmer bor, yuboraymi?", ctx) == []


def test_unknown_slots_may_be_asked_about() -> None:
    assert check_does_not_reask_known_slots("Qaysi razmer kerak?", _ctx(known_slots={})) == []


def test_missing_phone_ask_at_purchase_intent_is_caught() -> None:
    ctx = _ctx(expects_phone_ask=True)
    assert check_asks_for_phone("Zo'r, buyurtmangiz qabul qilindi.", ctx)
    assert check_asks_for_phone("Zo'r! Telefon raqamingizni qoldiring.", ctx) == []


def test_phone_ask_is_only_required_where_the_scenario_says_so() -> None:
    assert check_asks_for_phone("Qaysi razmer kerak?", _ctx(expects_phone_ask=False)) == []


# --- shape -----------------------------------------------------------------


def test_empty_reply_is_an_error() -> None:
    assert [f.check for f in check_shape("   ", _ctx())] == ["empty_reply"]


def test_wall_of_text_and_interrogation_are_warnings() -> None:
    wall = check_shape("x" * 900, _ctx())
    assert [f.check for f in wall] == ["too_long"]
    assert wall[0].severity == "warn"

    stacked = check_shape("Qaysi biri? Qaysi razmer? Rangi-chi?", _ctx())
    assert [f.check for f in stacked] == ["too_many_questions"]


def test_a_good_dm_reply_has_the_right_shape() -> None:
    assert check_shape("Nike Air Max — 780 000 so'm, 42 va 44 bor. Qaysi birini olamiz?", _ctx()) == []


# --- the harness itself ----------------------------------------------------


def test_a_genuinely_good_reply_passes_every_check() -> None:
    """The one that matters: no check may fire on a reply that is simply good."""
    ctx = _ctx(customer_message="Nike Air Max bormi? narxi qancha", known_slots={"use_case": "sportga"})
    reply = "Ha, bor! Nike Air Max — 780 000 so'm, 42 va 44 razmerlari mavjud. Sizga qaysi biri?"
    assert run_checks(reply, ctx) == []


def test_summarise_separates_errors_from_warnings() -> None:
    result = summarise([
        Finding("invented_price", "error", "x"),
        Finding("too_long", "warn", "y"),
    ])
    assert result["passed"] is False
    assert (result["errors"], result["warnings"]) == (1, 1)

    assert summarise([Finding("too_long", "warn", "y")])["passed"] is True
    assert summarise([])["passed"] is True


def test_scenarios_are_well_formed() -> None:
    assert SCENARIOS, "the suite must not be empty"
    keys = [s.key for s in SCENARIOS]
    assert len(keys) == len(set(keys)), "scenario keys must be unique"

    for scenario in SCENARIOS:
        assert scenario.turns, f"{scenario.key} has no turns"
        assert scenario.products, f"{scenario.key} seeds no catalog"
        for turn in scenario.turns:
            # A media message with no caption is stored with no text at all.
            assert turn.customer.strip() or turn.attachment_type, f"{scenario.key} has an empty customer message"

    # The regressions each phase fixed all have a scenario guarding them.
    assert {"vague_opener", "price_then_objection", "slot_memory", "closing_to_phone"} <= set(keys)
    assert {"shared_reel_no_caption", "template_message", "size_advice"} <= set(keys)
    assert by_key("russian_customer").turns[0].customer.startswith("здравствуйте")
