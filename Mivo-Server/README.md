# Mivo — Backend (`server/`)

FastAPI + PostgreSQL. Fully self-contained: everything needed to run and
deploy the backend lives in this folder. See `MIVO_MVP.md` in this folder for
the full product/architecture spec.

## Local development

```bash
cp .env.example .env   # fill in real secrets
uv sync                          # creates .venv from pyproject.toml/uv.lock (incl. dev deps)
docker compose up db -d          # Postgres only, or run your own local instance
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Dependencies live in `pyproject.toml`/`uv.lock`, managed with [uv](https://docs.astral.sh/uv/).
Add a runtime dependency with `uv add <pkg>`, a dev-only one with
`uv add --dev <pkg>`; either updates both files — commit both. Plain
`uv sync` re-installs from the lockfile (add `--no-dev` to skip test-only
packages, e.g. for prod).

Tests: `.venv/bin/pytest`.

## Docker (this folder only — no nginx, no client)

```bash
docker compose up -d --build
curl http://localhost:8000/health
```

## Production

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

No nginx/certbot in this repo — put a Cloudflare Tunnel (or your own reverse
proxy) in front of the `api` container's port 8000 for TLS/domain routing.

### First-deploy checklist

1. **Secrets** (`.env`, never commit): `JWT_SECRET` (`openssl rand -hex 32`),
   `FERNET_KEY` (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`),
   strong `POSTGRES_PASSWORD`.
2. **Cloudflare R2**: bucket + S3 API token + a bound custom domain for public
   image URLs (`R2_*` — don't use `*.r2.dev` in production).
3. **OpenRouter**: `OPENROUTER_API_KEY` + `OPENROUTER_MODEL`.
4. **Meta app**: Instagram Business Login (`instagram_business_basic` +
   `instagram_business_manage_messages`, account linked to a Facebook Page).
   `META_REDIRECT_URI=https://api.yourbusiness.com/integrations/instagram/callback`.
   Register the webhook (Meta dashboard → Webhooks → `messages` field →
   `https://api.yourbusiness.com/webhooks/instagram`, verify token =
   `META_WEBHOOK_VERIFY_TOKEN`).
5. **Telegram bot** via [@BotFather](https://t.me/BotFather):
   ```bash
   curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -d "url=https://api.yourbusiness.com/webhooks/telegram" \
     -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
   ```
6. **CORS**: `CORS_ALLOW_ORIGINS` must include the deployed `client/` URL(s).
7. **DB backups**: the `mivo_pgdata` volume is the source of truth — snapshot
   it on whatever schedule your VPS provider supports (not automated here).
8. **First superadmin**: there's no public signup — every business account is
   created from the superadmin panel, and every superadmin after the first is
   just an `UPDATE users SET is_superadmin = true`. Bootstrap the very first
   one directly:
   ```bash
   .venv/bin/python create_superadmin.py you@yourcompany.com
   ```

## Day-2

- Deploy: `git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
- Logs: `docker compose logs -f api`
