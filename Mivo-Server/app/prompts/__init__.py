"""Prompts and message templates as text files, so they can be edited without
touching code. {name} placeholders are filled by `render`."""
import json
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache
def load(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


def render(name: str, **values: object) -> str:
    text = load(name)
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


@lru_cache
def lexicon() -> dict:
    """Word lists the code matches customer text against (lexicon.json)."""
    return json.loads(load("lexicon.json"))
