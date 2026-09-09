"""Unit tests for deterministic score bands + phone extraction and normalization."""
import pytest
from app.leads.scoring import extract_valid_phone, normalize_phone_candidate, status_from_score


def test_status_from_score_boundaries() -> None:
    assert status_from_score(0) == "cold"
    assert status_from_score(34) == "cold"
    assert status_from_score(35) == "warm"
    assert status_from_score(69) == "warm"
    assert status_from_score(70) == "hot"
    assert status_from_score(100) == "hot"


@pytest.mark.parametrize(
    "raw_input, expected",
    [
        # Standard Uzbek +998 formats
        ("+998 90 123 45 67", "+998901234567"),
        ("+998901234567", "+998901234567"),
        ("+998 (90) 123-45-67", "+998901234567"),
        ("+998(90)1234567", "+998901234567"),
        ("+998.90.123.45.67", "+998901234567"),
        ("+ 998 90 123 45 67", "+998901234567"),
        # Standard Uzbek 998 formats (without +)
        ("998901234567", "+998901234567"),
        ("998 90 123 45 67", "+998901234567"),
        ("998-90-123-45-67", "+998901234567"),
        ("998 (90) 123 45 67", "+998901234567"),
        # Local 9-digit Uzbek formats (e.g. 90 123 45 67)
        ("901234567", "+998901234567"),
        ("90 123 45 67", "+998901234567"),
        ("90-123-45-67", "+998901234567"),
        ("90.123.45.67", "+998901234567"),
        ("(90) 123-45-67", "+998901234567"),
        ("(90) 123 45 67", "+998901234567"),
        # Other Uzbek operator prefixes (33, 88, 77, 99, 93, etc.)
        ("33 123 45 67", "+998331234567"),
        ("+998 88 555 44 33", "+998885554433"),
        ("+998 77 000 11 22", "+998770001122"),
        ("+998 99 999 88 77", "+998999998877"),
        ("+998 94 444 33 22", "+998944443322"),
        ("+998 95 555 66 77", "+998955556677"),
        ("+998 50 111 22 33", "+998501112233"),
        ("+998 55 500 60 70", "+998555006070"),
        # Trunk 8 format
        ("8 90 123 45 67", "+998901234567"),
        ("8901234567", "+998901234567"),
        # In sentence / surrounding text
        ("Mening raqamim: +998 90 123 45 67, yetkazib bering", "+998901234567"),
        ("telefon: (90) 123-45-67 raqamiga yozing", "+998901234567"),
        ("mana nomerim: 998901234567", "+998901234567"),
        ("aloqaga chiqing 90 123 45 67", "+998901234567"),
        # International E.164
        ("+1 555 123 4567", "+15551234567"),
        ("+7 (999) 123-45-67", "+79991234567"),
    ],
)
def test_extract_valid_phone_formats(raw_input: str, expected: str) -> None:
    assert extract_valid_phone(raw_input) == expected


@pytest.mark.parametrize(
    "invalid_input",
    [
        None,
        "",
        "   ",
        "hoodie 250000 so'm",
        "narxi 150 000",
        "2026-08-26",
        "2026.08.26",
        "size M",
        "razmer 42",
        "1234",
        "12345",
        "123456",
        "123456789",  # 9 digits but invalid operator prefix (12)
        "001234567",  # invalid prefix 00
        "random text with no numbers",
    ],
)
def test_extract_valid_phone_rejects_non_phone_text(invalid_input: str | None) -> None:
    assert extract_valid_phone(invalid_input) is None


def test_same_phone_appearing_multiple_times() -> None:
    text = "Mening raqamim 90 123 45 67, takrorlayman: +998 (90) 123-45-67"
    assert extract_valid_phone(text) == "+998901234567"
