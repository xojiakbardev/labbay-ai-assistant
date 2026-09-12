"""Application settings, loaded from environment variables / .env.

Secrets have no defaults. A default secret is a secret everyone who has read
this repository knows, so the app refuses to start without real ones instead of
quietly running on them — which is exactly how production ended up with a
publicly known JWT signing key.
"""
import hashlib
import logging
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import (
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

_Fraction = Annotated[float, Field(ge=0, le=1)]
_Temperature = Annotated[float, Field(ge=0, le=2)]
_Score = Annotated[int, Field(ge=1, le=99)]
# What the AI finds out about a customer before recommending
# (app/ai/conversation_state.py).
DiscoverySlot = Literal["use_case", "size", "color", "budget", "recipient"]

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
    # Background jobs (webhook sweep, follow-ups, token refresh). Off in tests,
    # where each job is driven directly.
    scheduler_enabled: bool = True

    # Database
    database_url: str = "postgresql+asyncpg://mivo:mivo@localhost:5432/mivo"
    # Sync URL used by Alembic (psycopg) — same DB, different driver.
    database_url_sync: str = "postgresql+psycopg://mivo:mivo@localhost:5432/mivo"
    db_pool_size: PositiveInt = 10
    db_max_overflow: NonNegativeInt = 20

    # Auth — required, no defaults. Generate with:
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: PositiveInt = 30
    refresh_token_expire_days: PositiveInt = 30
    # Failed logins allowed per email inside the window before 429.
    login_max_failures: PositiveInt = 5
    login_failure_window_minutes: PositiveInt = 15

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
    llm_request_timeout_seconds: PositiveFloat = 30.0
    # Hard ceiling on one whole customer turn (grounding loop + writer +
    # analyst, retries and fallback models included). Past it the customer
    # gets the handoff reply instead of silence.
    llm_turn_deadline_seconds: PositiveFloat = 60.0
    # A 429's Retry-After is honoured up to this — a provider asking for an
    # hour must not park a customer's reply for an hour.
    llm_retry_after_cap_seconds: NonNegativeFloat = 5.0

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
    llm_writer_temperature: _Temperature = 0.75
    llm_analyst_temperature: _Temperature = 0.0

    # Spend guards. Past the daily budget, or the per-conversation turn rate,
    # the AI stops answering that business/conversation and the owner is told
    # — a loop or an abusive customer must not be able to run up the bill.
    ai_daily_cost_limit_usd: PositiveFloat = 5.0
    ai_max_turns_per_conversation_per_hour: PositiveInt = 20

    # Conversation tuning.
    # Waiting for the customer's next message (Instagram doesn't say they're
    # typing): short after a complete question, longer after a fragment
    # ("salom", a photo on its own), never past the max from the burst's
    # first message. A reply written while they wrote again is dropped and
    # rewritten for the whole burst, unless the burst is older than the
    # supersede age.
    # Kept short on purpose: a message sent while the reply is being written
    # (~4 s) is caught by the supersede check anyway.
    reply_debounce_seconds: NonNegativeFloat = 1.5
    reply_fragment_debounce_seconds: NonNegativeFloat = 3.0
    reply_debounce_max_seconds: NonNegativeFloat = 4.0
    reply_fragment_max_words: PositiveInt = 3
    reply_supersede_max_age_seconds: NonNegativeFloat = 45.0
    reply_part_delay_seconds: NonNegativeFloat = 0.9
    history_limit: PositiveInt = 20
    max_multimodal_images: NonNegativeInt = 2
    max_image_age_hours: PositiveInt = 6
    max_images_per_reply: PositiveInt = 3
    voice_note_max_age_minutes: PositiveInt = 60
    closing_max_words: PositiveInt = 6
    closing_max_chars: PositiveInt = 60
    follow_up_after_minutes: PositiveInt = 30
    follow_up_window_hours: PositiveInt = 23
    follow_up_batch: PositiveInt = 50
    profile_retry_hours: PositiveInt = 24

    # Webhook processing.
    webhook_concurrency: PositiveInt = 8
    webhook_max_attempts: PositiveInt = 5
    webhook_retry_backoff_seconds: Annotated[list[PositiveInt], Field(min_length=1)] = [30, 60, 120, 300]
    webhook_lease_minutes: PositiveInt = 10
    webhook_orphan_after_seconds: PositiveInt = 60
    resend_window_minutes: PositiveInt = 20
    conversation_lock_wait_seconds: PositiveInt = 120
    refresh_reuse_grace_seconds: NonNegativeInt = 30

    # LLM calls.
    llm_api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    llm_credits_url: str = "https://openrouter.ai/api/v1/credits"
    llm_fallback_models: list[str] = ["anthropic/claude-3.5-haiku", "openai/gpt-4o-mini"]
    llm_max_tool_calls_per_round: PositiveInt = 3
    tool_description_max_chars: PositiveInt = 600
    closing_transcript_messages: PositiveInt = 6
    stt_max_audio_bytes: PositiveInt = 25 * 1024 * 1024
    stt_download_timeout_seconds: PositiveFloat = 20.0
    stt_timeout_seconds: PositiveFloat = 45.0
    embedding_api_url: str = "https://api.openai.com/v1/embeddings"
    embedding_max_batch: PositiveInt = 64
    embedding_usd_per_million_tokens: dict[str, float] = {
        "text-embedding-3-large": 0.13,
        "text-embedding-3-small": 0.02,
    }

    # Replies and conversation state.
    reply_split_min_total_chars: PositiveInt = 150
    reply_split_min_part_chars: PositiveInt = 30
    reply_split_max_parts: PositiveInt = 3
    reply_text_max_chars: PositiveInt = 500
    guard_price_tolerance: Annotated[float, Field(ge=0, le=0.05)] = 0.01
    guard_budget_window_chars: PositiveInt = 25
    state_max_tracked_products: PositiveInt = 6
    state_max_tracked_objections: PositiveInt = 4
    state_max_variants_per_product: PositiveInt = 8
    # Discovery answers the AI chases before recommending (a subset of
    # use_case, size, color, budget, recipient).
    essential_slots: list[DiscoverySlot] = ["use_case", "size", "budget"]
    caption_max_chars: PositiveInt = 500
    sandbox_ttl_hours: PositiveInt = 24

    # Leads.
    lead_cold_max_score: _Score = 34
    lead_warm_max_score: _Score = 69
    lead_max_interested_products: PositiveInt = 10
    lead_phone_lookback_messages: PositiveInt = 6

    # Products and search.
    search_default_limit: PositiveInt = 5
    search_candidate_pool: PositiveInt = 20
    search_rrf_k: PositiveInt = 60
    search_trigram_threshold: _Fraction = 0.25
    semantic_max_distance: Annotated[float, Field(gt=0, le=2)] = 0.62
    semantic_query_cache_size: PositiveInt = 256
    embedding_cooldown_seconds: PositiveFloat = 60.0
    browse_limit: PositiveInt = 6
    similar_limit: PositiveInt = 4
    max_categories: PositiveInt = 12
    similar_price_spread: _Fraction = 0.4
    product_import_max_chars: PositiveInt = 50_000
    product_max_images: PositiveInt = 20
    upload_max_bytes: PositiveInt = 5 * 1024 * 1024

    # Dashboard and accounts.
    trial_days: PositiveInt = 14
    business_text_max_chars: PositiveInt = 4000
    conversation_detail_messages: PositiveInt = 100
    conversation_preview_chars: PositiveInt = 120
    sse_ticket_ttl_seconds: PositiveInt = 60
    sse_heartbeat_seconds: PositiveFloat = 15.0
    sse_reauth_every_pings: PositiveInt = 20
    bcrypt_concurrency: PositiveInt = 4

    # Instagram, Telegram, push.
    instagram_graph_version: str = "v21.0"
    # "Seen" when the AI takes a message, "typing…" while it writes.
    instagram_sender_actions: bool = True
    instagram_oauth_state_ttl_minutes: PositiveInt = 15
    instagram_token_refresh_window_days: PositiveInt = 10
    instagram_token_min_age_hours: PositiveInt = 24
    telegram_api_base: str = "https://api.telegram.org"
    telegram_connect_token_ttl_minutes: PositiveInt = 30
    # Browser push services the server may POST to (SSRF allowlist); a leading
    # dot allows subdomains.
    push_service_hosts: list[str] = [
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "web.push.apple.com",
        ".push.apple.com",
        ".notify.windows.com",
    ]

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
    embedding_dimensions: Annotated[int, Field(gt=0, le=2000)] = 1536
    embedding_request_timeout_seconds: PositiveFloat = 15.0
    # The customer-facing search path can't wait 15s x retries on a slow
    # embedding API; this is its own budget, no retries.
    embedding_query_timeout_seconds: PositiveFloat = 2.0
    # How much the semantic tier counts against the lexical one when both
    # return results. Below 1.0 because an exact keyword match is still the
    # stronger signal — semantics adds recall, it doesn't outrank precision.
    semantic_fusion_weight: _Fraction = 0.6

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
    # Temporary, owner's decision: run on the leaked bot token until it's
    # revoked at launch. Remove together with the token.
    telegram_token_leak_acknowledged: bool = False

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
    def _refuse_contradictory_tuning(self) -> "Settings":
        """Each value can be fine alone and still break the app together."""
        problems: list[str] = []
        event_seconds = (
            self.reply_debounce_max_seconds + self.conversation_lock_wait_seconds + self.llm_turn_deadline_seconds
        )
        if self.webhook_lease_minutes * 60 <= event_seconds:
            problems.append(
                "WEBHOOK_LEASE_MINUTES must outlast REPLY_DEBOUNCE_MAX_SECONDS + CONVERSATION_LOCK_WAIT_SECONDS "
                "+ LLM_TURN_DEADLINE_SECONDS, or a live turn is taken over by a second worker"
            )
        if self.reply_debounce_max_seconds < max(self.reply_debounce_seconds, self.reply_fragment_debounce_seconds):
            problems.append("REPLY_DEBOUNCE_MAX_SECONDS must be at least the other debounce waits")
        if self.lead_cold_max_score >= self.lead_warm_max_score:
            problems.append("LEAD_COLD_MAX_SCORE must be below LEAD_WARM_MAX_SCORE")
        if self.reply_split_min_part_chars > self.reply_split_min_total_chars:
            problems.append("REPLY_SPLIT_MIN_PART_CHARS must not exceed REPLY_SPLIT_MIN_TOTAL_CHARS")
        if self.search_default_limit > self.search_candidate_pool:
            problems.append("SEARCH_DEFAULT_LIMIT must not exceed SEARCH_CANDIDATE_POOL")
        if problems:
            raise ValueError("Contradictory settings:\n- " + "\n- ".join(problems))
        return self

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
            if self.telegram_token_leak_acknowledged:
                logging.getLogger("app.core.config").warning(
                    "TELEGRAM_BOT_TOKEN is the one that leaked in git history (accepted by "
                    "TELEGRAM_TOKEN_LEAK_ACKNOWLEDGED) — revoke it in @BotFather before launch"
                )
            else:
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
