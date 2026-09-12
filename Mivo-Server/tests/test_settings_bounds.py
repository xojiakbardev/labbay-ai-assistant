"""A tunable set to something that can't work stops the app at startup, not
in the middle of a customer's conversation."""
import secrets

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**over) -> Settings:
    base = dict(
        jwt_secret=secrets.token_urlsafe(40),
        fernet_key=Fernet.generate_key().decode(),
        telegram_webhook_secret=secrets.token_urlsafe(32),
        vapid_private_key=secrets.token_urlsafe(32),
    )
    return Settings(_env_file=None, **{**base, **over})


def test_defaults_are_consistent() -> None:
    _settings()


@pytest.mark.parametrize(
    "over",
    [
        {"webhook_concurrency": 0},
        {"webhook_retry_backoff_seconds": []},
        {"webhook_retry_backoff_seconds": [30, -1]},
        {"reply_debounce_seconds": -1},
        {"reply_debounce_max_seconds": 5},
        {"guard_price_tolerance": 0.5},
        {"llm_writer_temperature": 3},
        {"essential_slots": ["use_case", "shoe_size"]},
        {"lead_cold_max_score": 70, "lead_warm_max_score": 60},
        {"lead_warm_max_score": 100},
        {"webhook_lease_minutes": 2},
        {"search_default_limit": 50, "search_candidate_pool": 20},
    ],
)
def test_impossible_tuning_is_refused(over) -> None:
    with pytest.raises(ValidationError):
        _settings(**over)
