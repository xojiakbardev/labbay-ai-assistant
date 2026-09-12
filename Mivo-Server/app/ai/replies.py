"""Ready-made replies sent without the model. Defaults in app/prompts/replies.json;
a business can override any of them on the AI settings page (businesses.reply_texts)."""

import json

from app import prompts
from app.core.config import get_settings

LANGUAGES = ("uz", "ru", "en")
MAX_REPLY_CHARS = get_settings().reply_text_max_chars

# Keys and the default texts, per language: app/prompts/replies.json.
DEFAULT_REPLIES: dict[str, dict[str, str]] = json.loads(prompts.load("replies.json"))


def reply_text(business, key: str, lang: str, **values: str) -> str:
    """The business's own text for `key` in `lang`, else the default.
    `values` fill placeholders like {product}."""
    overrides = getattr(business, "reply_texts", None)
    custom = overrides.get(key, {}).get(lang) if isinstance(overrides, dict) else None
    defaults = DEFAULT_REPLIES[key]
    text = custom.strip() if isinstance(custom, str) and custom.strip() else defaults.get(lang, defaults["uz"])
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text
