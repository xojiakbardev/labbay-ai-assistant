"""The reply guards (app/ai/orchestrator.py): a price or discount the catalog
doesn't back is caught, and the arithmetic a salesperson does with real
numbers is not mistaken for an invented price."""
from types import SimpleNamespace

import pytest

from app.ai.orchestrator import (
    _contains_unverified_discount_claim,
    extract_discount_numbers,
    find_unverified_prices,
)


def _business(**texts):
    fields = ("delivery_info", "payment_info", "discount_policy", "rules_text", "description", "handoff_instructions")
    return SimpleNamespace(**{f: texts.get(f) for f in fields})


def _state(prices, discounts=None, called_discounts=True):
    return {
        "catalog_prices": prices,
        "active_discounts": discounts or [],
        "executed_tools": [{"name": "get_active_discounts"}] if called_discounts else [],
    }


def _invented(reply, state, business=None, customer_texts=(), working_state=None) -> bool:
    return bool(find_unverified_prices(reply, state, working_state, business or _business(), list(customer_texts)))


def test_made_up_prices_near_real_ones_are_caught() -> None:
    """A price 5-20% off a real one, with no quantity, total or delivery in the
    reply, is invented. The old guard let most of these through (every price
    ×1-10, every pair sum, every number in the settings)."""
    catalog = [99000.0, 149000.0, 199000.0, 249000.0, 299000.0, 349000.0, 399000.0, 449000.0, 499000.0, 549000.0]
    business = _business(description="2019 yildan beri ishlaymiz, karta 8600 1234", rules_text="Tel: 90 123 45 67")
    offsets = (0.95, 0.93, 0.9, 0.88, 0.85, 0.82, 1.05, 1.07, 1.1, 1.12, 1.15, 1.2)
    fakes = [round(p * f, -3) for p in catalog for f in offsets]
    # Some land on another real price; those aren't invented.
    fakes = [x for x in fakes if not any(abs(x - a) <= a * 0.01 for a in catalog)]
    passed = [x for x in fakes if not _invented(f"Narxi {int(x):,} so'm".replace(",", " "), _state(catalog), business)]
    assert passed == []


def test_arithmetic_needs_the_reply_to_say_so() -> None:
    state = _state([780000.0, 520000.0])
    business = _business(delivery_info="Yetkazib berish 25 000 so'm")
    assert _invented("Narxi 1 560 000 so'm", state)  # no quantity stated
    assert not _invented("2 ta olsangiz 1 560 000 so'm", state)
    assert _invented("Narxi 1 300 000 so'm", state)  # a sum, but no "jami"
    assert not _invented("Ikkalasi jami 1 300 000 so'm", state)
    assert _invented("Narxi 805 000 so'm", state, business)  # price + fee without delivery
    assert not _invented("Yetkazib berish bilan 805 000 so'm", state, business)


@pytest.mark.parametrize(
    "reply",
    [
        "Narxi 780 000 so'm.",
        "2 tasi 1 560 000 so'm bo'ladi.",
        "6 ta olsangiz 4 680 000 so'm.",
        "Air Max 90 780 000 so'm turadi.",
        "Narxi 0,78 mln so'm atrofida",
    ],
)
def test_catalog_price_and_its_arithmetic_pass(reply) -> None:
    assert not _invented(reply, _state([780000.0]))


def test_total_with_the_business_delivery_fee_passes() -> None:
    business = _business(delivery_info="Toshkent bo'ylab yetkazib berish 30 000 so'm")
    assert not _invented("Jami 810 000 so'm (yetkazib berish bilan)", _state([780000.0]), business)
    assert not _invented("2 tasi yetkazib berish bilan 1 590 000 so'm", _state([780000.0]), business)


def test_discounted_quantities_pass() -> None:
    pct = _state([780000.0], [{"type": "percentage", "value": 10, "description": "Kuzgi aksiya"}])
    assert not _invented("Chegirma bilan 2 tasi 1 404 000 so'm", pct)
    fixed = _state([780000.0], [{"type": "fixed", "value": 50000, "description": "Chegirma"}])
    assert not _invented("2 tasi 1 460 000 so'm", fixed)


def test_amount_in_discount_conditions_passes() -> None:
    state = _state(
        [780000.0],
        [{"type": "percentage", "value": 10, "description": "Aksiya", "conditions": "500 000 so'mdan ortiq xarid"}],
    )
    reply = "500 000 so'mdan ortiq xaridga 10% chegirma bor."
    assert not _invented(reply, state)
    assert not _contains_unverified_discount_claim(
        reply, state["executed_tools"], state["active_discounts"], {780000.0}
    )


def test_variant_price_told_in_an_earlier_turn_passes() -> None:
    working_state = {"product_facts": {"p": {"price": 780000.0, "variant_prices": [820000.0]}}}
    assert not _invented("45-razmer 820 000 so'm", _state([]), working_state=working_state)


def test_customer_number_is_a_budget_only_next_to_budget_words() -> None:
    assert not _invented("500 000 so'mgacha variantlar bor", _state([]), customer_texts=["500 minggacha kerak"])
    assert _invented("Narxi 901 234 so'm", _state([]), customer_texts=["raqamim 901234567"])


@pytest.mark.parametrize("reply", ["Narxi 650 000 so'm.", "Narxi 1,2 mln so'm", "2 tasi 1 700 000 so'm"])
def test_invented_prices_are_caught(reply) -> None:
    assert _invented(reply, _state([780000.0]))


def test_fabric_percentage_is_not_a_discount_claim() -> None:
    assert 100.0 not in extract_discount_numbers("100% paxtadan, 10% chegirma bilan 702 000 so'm")
    state = _state([780000.0], [{"type": "percentage", "value": 10, "description": "Aksiya"}])
    for reply in (
        "100% paxtadan, 10% chegirma bilan 702 000 so'm",
        "Narxi 780 000 so'm chegirmadan keyin 702 000 so'm",
    ):
        assert not _contains_unverified_discount_claim(
            reply, state["executed_tools"], state["active_discounts"], {780000.0}
        ), reply


def test_invented_discount_is_still_caught() -> None:
    state = _state([780000.0], [{"type": "percentage", "value": 10, "description": "Aksiya"}])
    assert _contains_unverified_discount_claim(
        "Sizga 25% chegirma beramiz", state["executed_tools"], state["active_discounts"], {780000.0}
    )
    assert _contains_unverified_discount_claim("Sizga 10% chegirma beramiz", [], [], {780000.0})
