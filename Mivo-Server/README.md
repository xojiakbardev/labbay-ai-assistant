# Mivo — Backend (`Mivo-Server/`)

FastAPI + PostgreSQL (pgvector). Everything needed to run and deploy the
backend lives in this folder.

## Local development

```bash
cp .env.example .env             # fill in the REQUIRED values (the app refuses to start without them)
uv sync                          # creates .venv from pyproject.toml/uv.lock (incl. dev deps)
docker compose up db -d          # Postgres + pgvector on 127.0.0.1:5432
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Or everything in Docker: `docker compose up -d --build` (API on
`127.0.0.1:8000`, source bind-mounted with `--reload`).

Dependencies live in `pyproject.toml`/`uv.lock`, managed with [uv](https://docs.astral.sh/uv/).
`uv add <pkg>` / `uv add --dev <pkg>` update both files — commit both.

Tests need a Postgres with the `vector` extension and a `mivo_test` database:
`.venv/bin/pytest`. A failed migration fails the test run.

## Production

`docker-compose.prod.yml` is a complete, standalone file (not an override of
the dev one):

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

- `migrate` runs `alembic upgrade head` once; the API starts only after it
  succeeded, so new code never runs against the old schema.
- The database is not published to the host at all; the API listens on
  `127.0.0.1:8000` for the host's reverse proxy (nginx).
- Run one API process: the SSE broadcaster is in-memory. Scheduled jobs and
  the webhook sweeper take Postgres advisory locks, so extra processes never
  double-send — they would only split SSE subscribers.

### Required configuration (`.env`, never commit)

The app refuses to start with a missing secret or with any value that has
ever appeared in this repository. With `APP_ENV=production` it additionally
requires `DEBUG=false`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`, and
`TELEGRAM_WEBHOOK_SECRET` when a bot token is set.

1. **Secrets**: `JWT_SECRET` (≥ 32 chars, `python -c "import secrets; print(secrets.token_urlsafe(48))"`),
   `FERNET_KEY`, a strong `POSTGRES_PASSWORD`.
   - Rotating `FERNET_KEY`: set `FERNET_KEY=<new>,<old>`, restart, run
     `python rotate_fernet_key.py`, then set `FERNET_KEY=<new>`.
   - Rotating `JWT_SECRET` signs every user out (they log in again).
2. **Cloudflare R2**: bucket + S3 API token + a bound custom domain for public
   image URLs (`R2_*`).
3. **OpenRouter**: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`; spend guards
   `AI_DAILY_COST_LIMIT_USD`, `AI_MAX_TURNS_PER_CONVERSATION_PER_HOUR`.
4. **Meta app**: Instagram Business Login (`instagram_business_basic` +
   `instagram_business_manage_messages`). `META_REDIRECT_URI` points at
   `/integrations/instagram/callback` on this API. Webhook: `messages` field →
   `/webhooks/instagram`, verify token = `META_WEBHOOK_VERIFY_TOKEN`. Without
   `META_APP_SECRET` every webhook is rejected (signatures can't be verified).
5. **Telegram bot** via [@BotFather](https://t.me/BotFather):
   ```bash
   curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -d "url=https://<api-host>/webhooks/telegram" \
     -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
   ```
6. **Web push**: `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` (`npx web-push generate-vapid-keys`); empty = push off.
7. **Voice notes**: `STT_PROVIDER` (`openrouter` | `groq` | `openai` | empty).
8. **DB backups**: the `mivo_pgdata` volume is the source of truth — snapshot it.
9. **First superadmin**: `python create_superadmin.py you@yourcompany.com`
   (every business account is then created from the superadmin panel).

## Day-2

- Deploy: `git pull && docker compose -f docker-compose.prod.yml up -d --build`
- Logs: `docker compose -f docker-compose.prod.yml logs -f api`
- Customer profiles now, instead of waiting for the scheduled backfill: `python sync_script.py`
