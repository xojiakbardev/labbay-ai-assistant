"""Unit tests for search normalization utilities."""
import pytest
from app.products.search_normalize import normalize_for_search


def test_cyrillic_to_latin_transliteration():
    assert normalize_for_search("спорт костюм") == "sport kostyum"
    assert normalize_for_search("Спорт Костюм") == "sport kostyum"
    assert normalize_for_search("Футболка оверсайз") == "futbolka oversayz"
    assert normalize_for_search("Жакет шим") == "jaket shim"
    assert normalize_for_search("Қora shim") == "qora shim"
    assert normalize_for_search("Ғisht rang") == "gisht rang"
    assert normalize_for_search("Ўrik rangli ko'ylak") == "orik rangli koylak"
    assert normalize_for_search("Ҳamkor") == "hamkor"


def test_uzbek_apostrophes_unified():
    # Various styles of o' and g'
    assert normalize_for_search("ko'ylak") == "koylak"
    assert normalize_for_search("koʻylak") == "koylak"
    assert normalize_for_search("ko‘ylak") == "koylak"
    assert normalize_for_search("ko`ylak") == "koylak"
    assert normalize_for_search("g'isht") == "gisht"
    assert normalize_for_search("gʻisht") == "gisht"
    assert normalize_for_search("g‘isht") == "gisht"
    assert normalize_for_search("o'rik") == "orik"
    assert normalize_for_search("oʻrik") == "orik"


def test_typo_word_normalization():
    assert normalize_for_search("krossofka") == "krossofka"
    assert normalize_for_search("krossovka") == "krossovka"


def test_punctuation_and_whitespace_cleaned():
    assert normalize_for_search("  Qizil ko'ylak (oversize) - 2024!  ") == "qizil koylak oversize 2024"
    assert normalize_for_search(None) == ""
    assert normalize_for_search("") == ""
