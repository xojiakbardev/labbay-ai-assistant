"""Application settings, loaded from environment variables / .env.

Secrets have no defaults. A default secret is a secret everyone who has read
this repository knows, so the app refuses to start without real ones instead of
quietly running on them — which is exactly how production ended up with a
publicly known JWT signing key.
"""
import hashlib
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that have appeared in this repository (code, .env.example, docs).
# Anything here is public knowledge and can never be a real secret.
_KNOWN_PUBLIC_VALUES = frozenset(
    {
        "change-me-in-prod",
        "replace-with-a-long-random-string",
        "bXgkuUYS8btCnR64znDi0WPkACjDopgX0zvfbjYCvwE=",
        "hNuBvXcWegF1975n0v8wsfAt71_G_biGsIvqENEg9Ec",
        "change-me-verify-token",
        "replace-with-a-verify-token",
        "change-me-telegram-secret",
        "replace-with-a-telegram-secret",
        "mivo-telegram-webhook-secret-2026",
        "CHANGE_ME",
    }
)

# Test-only values from tests/conftest.py — public too, accepted only when
# APP_ENV=test.
_TEST_ONLY_VALUES = frozenset(
    {
        "test-jwt-secret-that-is-long-enough-0123456789",
        "yQ2Ky0-PWXVYd5o1S4cTL8C2n0Wf7tT7xmFJwlm3i-8=",
        "test-meta-app-secret",
        "test-telegram-webhook-secret",
        "test-only-vapid-private-key-not-real",
    }
)

# SHA-256 of credentials that leaked through git history. Stored as hashes so
# the secret itself never reappears in the code.
_LEAKED_SECRET_SHA256 = frozenset(
    {
        "861d0796fd82db876eb95bb6c1407db1d74fa5c91e81ded9dbf4c3b94c564876",  # old Telegram bot token
    }
)

_PRODUCTION_ENVS = frozenset({"production", "prod"})


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: str = "development"
    # SQL echo logs bind parameters — phone numbers, names — so it is opt-in.
    debug: bool = False
    # Comma-separated. In production the SPA calls the API through the
    # Cloudflare Pages proxy (same origin), so this only matters for local dev.
    cors_allow_origins: str = "http://localhost:3000"
    # Set to "/api" in prod, where nginx reverse-proxies /api/* here and
    # strips the prefix before forwarding — FastAPI needs to know that
    # prefix exists so the URLs it generates for itself (Swagger's
    # openapi.json href, docs assets) come back with /api/... instead of a
    # bare path the SPA's own catch-all route would otherwise swallow.
    # Empty locally, where the frontend calls this app directly with no
    # prefix. See app/main.py.
    root_path: str = ""
    # Where browser-facing redirects (the Instagram OAuth callback) and every
    # dashboard link in Telegram/push notifications point.
    frontend_url: str = "http://localhost:3000"

    # Database
    database_url: str = "postgresql+asyncpg://mivo:mivo@localhost:5432/mivo"
    # Sync URL used by Alembic (psycopg) — same DB, different driver.
    database_url_sync: str = "postgresql+psycopg://mivo:mivo@localhost:5432/mivo"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Auth — required, no defaults. Generate with:
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    # Failed logins allowed per email inside the window before 429.
    login_max_failures: int = 5
    login_failure_window_minutes: int = 15

    # Secrets-at-rest encryption (Instagram tokens, pending OAuth codes).
    # Required. Comma-separated to rotate: the FIRST key encrypts, every key
    # decrypts. Rotate by prepending a new key, running
    # `python rotate_fernet_key.py`, then dropping the old one.
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str

    # Cloudflare R2 (S3-compatible)
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "mivo-media"
    r2_public_base_url: str = ""

    # LLM (OpenRouter default, provider-abstracted)
    llm_provider: str = "openrouter"
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    llm_request_timeout_seconds: float = 30.0
    # Hard ceiling on one whole customer turn (grounding loop + writer +
    # analyst, retries and fallback models included). Past it the customer
    # gets the handoff reply instead of silence.
    llm_turn_deadline_seconds: float = 60.0
    # A 429's Retry-After is honoured up to this — a provider asking for an
    # hour must not park a customer's reply for an hour.
    llm_retry_after_cap_seconds: float = 5.0

    # A customer-facing sales reply and the bookkeeping that goes with it
    # (lead score, trilingual summaries, extracted facts) are two different
    # jobs, and forcing one model to do both inside one JSON schema is what
    # made replies read as form-filling rather than selling. They now run as
    # two calls, so each gets the model and sampling temperature it actually
    # wants: the writer needs room to sound human, the analyst needs to be
    # boringly deterministic. Both default to OPENROUTER_MODEL — set
    # LLM_WRITER_MODEL to a stronger model (prose quality, and Uzbek fluency
    # in particular, is where the small/cheap tier is weakest) and leave
    # LLM_ANALYST_MODEL cheap.
    llm_writer_model: str = ""
    llm_analyst_model: str = ""
    llm_writer_temperature: float = 0.75
    llm_analyst_temperature: float = 0.0

    # Spend guards. Past the daily budget, or the per-conversation turn rate,
    # the AI stops answering that business/conversation and the owner is told
    # — a loop or an abusive customer must not be able to run up the bill.
    ai_daily_cost_limit_usd: float = 5.0
    ai_max_turns_per_conversation_per_hour: int = 20

    # Voice messages. One provider, chosen explicitly: "openrouter" (uses
    # OPENROUTER_API_KEY and STT_MODEL), "groq" or "openai" (use STT_API_KEY),
    # or "" to not transcribe at all — a voice note then goes to the owner.
    stt_provider: str = "openrouter"
    stt_api_key: str = ""
    stt_model: str = "google/gemini-2.5-flash"

    # Semantic product search. Lexical search can only match words the catalog
    # already contains, so "qishga issiq narsa kerak" finds nothing unless some
    # product literally says "qish" — the customer has to guess the catalog's
    # vocabulary. Embeddings close that gap.
    #
    # OpenRouter has no embeddings endpoint, so this is a separate key (OpenAI
    # by default). Leave it unset and search runs full-text + trigram only.
    #
    # `embedding_dimensions` is capped at 1536 because pgvector cannot build an
    # HNSW index above 2000, and it must match the products.embedding column:
    # changing either means a migration and a full re-embed.
    embedding_enabled: bool = True
    embedding_provider: str = "openai"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 1536
    embedding_request_timeout_seconds: float = 15.0
    # The customer-facing search path can't wait 15s x retries on a slow
    # embedding API; this is its own budget, no retries.
    embedding_query_timeout_seconds: float = 2.0
    # How much the semantic tier counts against the lexical one when both
    # return results. Below 1.0 because an exact keyword match is still the
    # stronger signal — semantics adds recall, it doesn't outrank precision.
    semantic_fusion_weight: float = 0.6

    # Instagram / Meta. The app secret is what verifies webhook signatures —
    # without it every webhook is rejected, never accepted unverified.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_webhook_verify_token: str = ""
    meta_redirect_uri: str = "http://localhost:8000/integrations/instagram/callback"

    # Telegram. Without a webhook secret the Telegram webhook rejects
    # everything.
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_secret: str = ""

    # Web Push / VAPID. Unset = push notifications disabled.
    #   npx web-push generate-vapid-keys
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_claims_sub: str = "mailto:support@nasriddinov.dev"

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in _PRODUCTION_ENVS

    @property
    def fernet_keys(self) -> list[str]:
        return [k.strip() for k in self.fernet_key.split(",") if k.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _refuse_insecure_configuration(self) -> "Settings":
        problems: list[str] = []
        public = _KNOWN_PUBLIC_VALUES if self.app_env == "test" else _KNOWN_PUBLIC_VALUES | _TEST_ONLY_VALUES

        if self.jwt_secret in public or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be a private random value of at least 32 characters")
        if not self.fernet_keys:
            problems.append("FERNET_KEY is empty")
        elif self.fernet_keys[0] in public:
            problems.append("FERNET_KEY's primary (first) key is a publicly known value — rotate it")
        for name in ("vapid_private_key", "meta_webhook_verify_token", "telegram_webhook_secret", "meta_app_secret"):
            value = getattr(self, name)
            if value and (value in public or value.upper().startswith("CHANGE_ME")):
                problems.append(f"{name.upper()} is a publicly known value")
        if self.telegram_bot_token and _sha256(self.telegram_bot_token.removeprefix("bot").strip()) in _LEAKED_SECRET_SHA256:
            problems.append("TELEGRAM_BOT_TOKEN leaked in git history — revoke it in @BotFather and set the new one")

        if self.is_production:
            if self.debug:
                problems.append("DEBUG must be false in production (SQL echo logs customer PII)")
            if not self.meta_app_secret:
                problems.append("META_APP_SECRET is required in production (webhook signatures)")
            if not self.meta_webhook_verify_token:
                problems.append("META_WEBHOOK_VERIFY_TOKEN is required in production")
            if self.telegram_bot_token and not self.telegram_webhook_secret:
                problems.append("TELEGRAM_WEBHOOK_SECRET is required when TELEGRAM_BOT_TOKEN is set")
            if "*" in self.cors_origins:
                problems.append("CORS_ALLOW_ORIGINS must list origins explicitly in production")

        if problems:
            raise ValueError("Refusing to start with an insecure configuration:\n- " + "\n- ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
