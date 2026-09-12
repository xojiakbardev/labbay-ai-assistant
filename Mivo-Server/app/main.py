"""Mivo FastAPI application entrypoint."""
import datetime as dt
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.ai.router import router as ai_router
from app.auth.router import router as auth_router
from app.businesses.router import router as businesses_router
from app.conversations.router import router as conversations_router
from app.core.config import get_settings
from app.core.db import async_session_factory, engine
from app.customers.router import router as customers_router
from app.instagram import pipeline
from app.instagram import service as instagram_service
from app.instagram.client import MetaClient
from app.instagram.router import router as instagram_router
from app.leads.router import router as leads_router
from app.notifications.router import router as notifications_router
from app.products.router import router as products_router
from app.push.router import router as push_router
from app.superadmin.router import router as superadmin_router
from app.telegram.router import router as telegram_router

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs every request URL at INFO — and the Telegram bot token is part of
# the URL path, Instagram token calls carry access_token/client_secret as query
# parameters. Only warnings and errors from the HTTP client are logged.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("app.main")

# Distinct advisory-lock keys per scheduled job. A job body only runs in the
# process that wins its lock, so running several API workers or replicas never
# sends every follow-up (or refreshes every token) N times.
_JOB_LOCKS = {
    "refresh_instagram_tokens": 7_400_001,
    "smart_follow_up": 7_400_002,
    "webhook_sweep": 7_400_003,
    "profile_backfill": 7_400_004,
}


async def _run_exclusively(job: str, body) -> None:
    async with engine.connect() as raw:
        conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
        got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _JOB_LOCKS[job]})).scalar()
        if not got:
            return
        try:
            await body()
        except Exception:  # noqa: BLE001 — a failed run is logged; the next run retries
            logger.exception("[scheduler] job %s failed", job)
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _JOB_LOCKS[job]})


async def _refresh_instagram_tokens_job() -> None:
    """Daily — see app/instagram/service.py:refresh_expiring_tokens for why
    an owner never has to reconnect Instagram every 60 days."""

    async def body() -> None:
        async with async_session_factory() as db:
            count = await instagram_service.refresh_expiring_tokens(db, MetaClient())
            if count:
                logger.info("[scheduler] refreshed %d Instagram access token(s)", count)

    await _run_exclusively("refresh_instagram_tokens", body)


async def _smart_follow_up_job() -> None:
    from app.ai.follow_up import sweep_inactive_leads_follow_up

    async def body() -> None:
        async with async_session_factory() as db:
            count = await sweep_inactive_leads_follow_up(db, MetaClient())
            if count:
                logger.info("[scheduler] sent %d follow-up(s)", count)

    await _run_exclusively("smart_follow_up", body)


async def _profile_backfill_job() -> None:
    async def body() -> None:
        async with async_session_factory() as db:
            await instagram_service.backfill_customer_profiles(db, MetaClient())

    await _run_exclusively("profile_backfill", body)


async def _webhook_sweep_job() -> None:
    """Re-drives webhook events that failed or whose worker died mid-turn."""
    from app.ai.provider.factory import get_llm_provider

    async def body() -> None:
        count = await pipeline.sweep_events(get_llm_provider, MetaClient())
        if count:
            logger.info("[scheduler] re-drove %d webhook event(s)", count)

    await _run_exclusively("webhook_sweep", body)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not settings.scheduler_enabled:
        yield
        return
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _refresh_instagram_tokens_job,
        "interval",
        hours=24,
        next_run_time=dt.datetime.now(),  # also sweep once on startup
        id="refresh_instagram_tokens",
    )
    scheduler.add_job(_smart_follow_up_job, "interval", minutes=15, id="smart_follow_up_job")
    scheduler.add_job(_profile_backfill_job, "interval", minutes=30, id="profile_backfill")
    scheduler.add_job(
        _webhook_sweep_job, "interval", seconds=30, next_run_time=dt.datetime.now(), id="webhook_sweep", max_instances=1
    )
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        try:
            await pipeline.release_interrupted()
        except Exception:  # noqa: BLE001 — the lease rescues them anyway, just later
            logger.exception("[shutdown] could not hand interrupted events back")


_docs_enabled = not settings.is_production
app = FastAPI(
    title="Mivo AI API",
    version="0.1.0",
    lifespan=lifespan,
    root_path=settings.root_path,
    # The full API map (superadmin routes included) is not published in
    # production.
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Header auth (Bearer), not cookies — so credentials are off and only the
# configured origins may call the API from a browser. The production SPA goes
# through the same-origin Cloudflare proxy and needs no CORS at all.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # The detail stays in the server log. Returning str(exc) sent SQL text and
    # bound parameters (customer data included) to whoever made the request.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


app.include_router(auth_router)
app.include_router(businesses_router)
app.include_router(products_router)
app.include_router(leads_router)
app.include_router(notifications_router)
app.include_router(instagram_router)
app.include_router(telegram_router)
app.include_router(push_router)
app.include_router(conversations_router)
app.include_router(customers_router)
app.include_router(ai_router)
app.include_router(superadmin_router)


@app.get("/health")
async def health():
    """Up means the database answers too — a deploy is checked against this."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — reported as 503, logged
        logger.exception("[health] database unreachable")
        return JSONResponse(status_code=503, content={"status": "db_unavailable"})
    return {"status": "ok"}
