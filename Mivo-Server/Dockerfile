# syntax=docker/dockerfile:1

# ---- deps: resolve + install into /app/.venv, with build tools that never
# reach the final image -------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# build-essential only for any dependency lacking a manylinux wheel for this
# platform; psycopg[binary]/asyncpg/cryptography/bcrypt ship prebuilt ones,
# so this mostly just keeps the build resilient without bloating runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /usr/local/bin/

WORKDIR /app

# Lockfile-only layer first — `docker build` only redoes dependency
# resolution/install when pyproject.toml/uv.lock actually change, not on
# every source edit.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- runtime: slim image, no compilers, no lockfiles ----------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# curl for the compose/orchestrator healthcheck only — psycopg[binary] and
# asyncpg both bundle their own Postgres client, no libpq needed here.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Run as non-root — the source tree is bind-mounted read/write in dev, so keep
# ownership consistent with the host user via a plain, unprivileged account.
RUN useradd --create-home --shell /bin/bash app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app . .

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
