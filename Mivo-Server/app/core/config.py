"""Application settings, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: str = "development"
    debug: bool = True
    cors_allow_origins: str = "http://localhost:5173"
    # Set to "/api" in prod, where nginx reverse-proxies /api/* here and
    # strips the prefix before forwarding — FastAPI needs to know that
    # prefix exists so the URLs it generates for itself (Swagger's
    # openapi.json href, docs assets) come back with /api/... instead of a
    # bare path the SPA's own catch-all route would otherwise swallow.
    # Empty locally, where the frontend calls this app directly with no
    # prefix. See app/main.py.
    root_path: str = ""
    # Where browser-facing redirects (e.g. after the Instagram OAuth callback,
    # which must land on the backend to safely exchange the code — see
    # app/instagram/router.py) send the user back to.
    frontend_url: str = "http://localhost:3000"

    # Database
    database_url: str = "postgresql+asyncpg://mivo:mivo@localhost:5432/mivo"
    # Sync URL used by Alembic (psycopg) — same DB, different driver.
    database_url_sync: str = "postgresql+psycopg://mivo:mivo@localhost:5432/mivo"

    # Auth
    jwt_secret: str = "change-me-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # Secrets encryption (Fernet key). This default is a DEV-ONLY placeholder valid
    # enough to boot the app locally — production MUST override via env/.env with a
    # freshly generated key:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str = "bXgkuUYS8btCnR64znDi0WPkACjDopgX0zvfbjYCvwE="

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

    # Semantic product search. Lexical search can only match words the catalog
    # already contains, so "qishga issiq narsa kerak" finds nothing unless some
    # product literally says "qish" — the customer has to guess the catalog's
    # vocabulary. Embeddings close that gap.
    #
    # OpenRouter has no embeddings endpoint, so this is a separate key (OpenAI
    # by default). Leave it unset and search falls back to full-text + trigram
    # exactly as before — nothing breaks, it just can't bridge wording.
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
    # How much the semantic tier counts against the lexical one when both
    # return results. Below 1.0 because an exact keyword match is still the
    # stronger signal — semantics adds recall, it doesn't outrank precision.
    semantic_fusion_weight: float = 0.6

    # Instagram / Meta
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_webhook_verify_token: str = "change-me-verify-token"
    meta_redirect_uri: str = "http://localhost:8000/integrations/instagram/callback"

    # Telegram
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_secret: str = "change-me-telegram-secret"

    # Web Push / VAPID
    vapid_public_key: str = "BBN_dHQ1Za3z-UnB2Zcw5QmSmfN-9WiSlsTv_gUzffy4yr9gqajnK_VA0XbfRvin9AA86-lTrEl2ljmjSalN2Ww"
    vapid_private_key: str = "hNuBvXcWegF1975n0v8wsfAt71_G_biGsIvqENEg9Ec"
    vapid_claims_sub: str = "mailto:support@nasriddinov.dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
