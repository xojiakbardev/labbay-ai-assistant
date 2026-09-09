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
