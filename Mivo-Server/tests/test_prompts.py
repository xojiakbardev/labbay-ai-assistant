"""Prompts and word lists live in app/prompts as editable files."""
import re

from app import prompts
from app.ai.context.builder import _ANALYST_SYSTEM_PROMPT, _BASE_SYSTEM_PROMPT
from app.ai.provider.openrouter import _WRITER_FORMAT_REMINDER, _WRITER_INSTRUCTION, _WRITER_MESSAGE_MARKER
from app.leads.scoring import COLD_MAX_SCORE, WARM_MAX_SCORE

_PLACEHOLDER = re.compile(r"\{(cold_max|warm_min|warm_max|hot_min|marker)\}")


def test_rendered_prompts_have_no_unfilled_placeholders() -> None:
    for text in (_BASE_SYSTEM_PROMPT, _ANALYST_SYSTEM_PROMPT, _WRITER_INSTRUCTION, _WRITER_FORMAT_REMINDER):
        assert text.strip() and not _PLACEHOLDER.search(text)
    assert _WRITER_MESSAGE_MARKER in _WRITER_INSTRUCTION


def test_analyst_prompt_uses_the_configured_lead_bands() -> None:
    assert f"cold (0-{COLD_MAX_SCORE})" in _ANALYST_SYSTEM_PROMPT
    assert f"hot ({WARM_MAX_SCORE + 1}-100)" in _ANALYST_SYSTEM_PROMPT


def test_lexicon_has_every_list_the_code_reads() -> None:
    lexicon = prompts.lexicon()
    for key in (
        "discount_terms", "price_lowering_terms", "budget_terms", "product_hints", "no_grounding_needed",
        "cyrillic_to_latin", "synonyms", "currency_aliases", "uz_phone_prefixes", "embedded_attributes",
        "language_markers", "price_arithmetic",
    ):
        assert lexicon[key], key
