"""Search normalization utilities for fuzzy and multi-alphabet matching.

Translates Cyrillic to Latin, unifies Uzbek apostrophe variants (oʻ/o'/gʻ/g'),
strips punctuation, and returns a clean lowercased string for trigram matching.
"""
import re
from app import prompts

_CYRILLIC_TO_LATIN = prompts.lexicon()["cyrillic_to_latin"]

_APOSTROPHES = r"['ʻ’‘ʼ`´\"״]"


def normalize_for_search(text: str | None) -> str:
    """Normalizes text for fuzzy search comparison:
    1. Converts to lowercase.
    2. Equates oʻ/o'/o‘/o` variants to 'o' and gʻ/g'/g‘/g` variants to 'g'.
    3. Translates Cyrillic characters to Latin.
    4. Strips remaining apostrophes and tutuq marks.
    5. Replaces any non-alphanumeric character with a space.
    6. Collapses multiple whitespace characters and strips ends.
    """
    if not text:
        return ""

    s = text.lower()

    # Normalize Uzbek o' and g' variants to simple 'o' and 'g'
    s = re.sub(rf"o{_APOSTROPHES}", "o", s)
    s = re.sub(rf"g{_APOSTROPHES}", "g", s)

    # Transliterate Cyrillic to Latin (multi-character like yo, yu, sh first)
    for cyr, lat in sorted(_CYRILLIC_TO_LATIN.items(), key=lambda item: len(item[1]), reverse=True):
        s = s.replace(cyr, lat)

    # Remove any remaining apostrophes / quotation marks
    s = re.sub(_APOSTROPHES, "", s)

    # Replace anything other than ASCII alphanumeric characters with a space
    s = re.sub(r"[^a-z0-9\s]", " ", s)

    # Collapse multiple whitespace characters and strip
    return re.sub(r"\s+", " ", s).strip()


_SYNONYM_MAP: dict[str, list[str]] = prompts.lexicon()["synonyms"]


def get_synonyms_for_word(word: str) -> list[str]:
    w = normalize_for_search(word)
    return _SYNONYM_MAP.get(w, [])
