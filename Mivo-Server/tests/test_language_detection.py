"""detect_preferred_language picks the language of ready-made replies."""
import pytest

from app.ai.orchestrator import detect_preferred_language


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["Salom, narxi qancha?"], "uz"),
        (["bu ko'k rangdami"], "uz"),
        (["Салом, нархи қанча?"], "uz"),
        (["Ассалому алайкум, 42 размер борми"], "uz"),
        (["йук, керак эмас"], "uz"),
        (["Здравствуйте, сколько стоит?"], "ru"),
        (["salom", "сколько стоит доставка?"], "ru"),
        (["Hi, how much is it?"], "en"),
    ],
)
def test_customer_language(texts, expected) -> None:
    assert detect_preferred_language("uz", texts) == expected


def test_business_language_when_the_customer_gave_nothing_to_go_on() -> None:
    assert detect_preferred_language("ru", ["42"]) == "ru"
    assert detect_preferred_language("o'zbek, rus, ingliz", []) == "uz"
    assert detect_preferred_language(None, None) == "uz"
