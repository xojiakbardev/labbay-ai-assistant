"""Mivo FastAPI application entrypoint."""
import datetime as dt
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.ai.router import router as ai_router
from app.auth.router import router as auth_router
from app.businesses.router import router as businesses_router
from app.conversations.router import router as conversations_router
from app.customers.router import router as customers_router
from app.core.db import async_session_factory
from app.instagram import service as instagram_service
from app.instagram.client import MetaClient
from app.instagram.router import router as instagram_router
from app.leads.router import router as leads_router
from app.notifications.router import router as notifications_router
from app.products.router import router as products_router
from app.push.router import router as push_router
from app.superadmin.router import router as superadmin_router
from app.telegram.router import router as telegram_router
from app.core.config import get_settings

settings = get_settings()


async def _refresh_instagram_tokens_job() -> None:
    """Runs daily — see app/instagram/service.py:refresh_expiring_tokens for
    why this means a business owner never has to manually reconnect
    Instagram every 60 days."""
    async with async_session_factory() as db:
        try:
            count = await instagram_service.refresh_expiring_tokens(db, MetaClient())
            if count:
                print(f"[scheduler] refreshed {count} Instagram access token(s)")
        except Exception as exc:  # noqa: BLE001 — a bad sweep must not crash the process
            print(f"[scheduler] Instagram token refresh sweep failed: {exc}")


async def _smart_follow_up_job() -> None:
    """Runs periodically to re-engage warm/hot leads who stopped responding."""
    from app.ai.follow_up import sweep_inactive_leads_follow_up
    async with async_session_factory() as db:
        try:
            count = await sweep_inactive_leads_follow_up(db, MetaClient())
            if count:
                print(f"[scheduler] Sent {count} smart follow-up(s) to inactive leads")
        except Exception as exc:
            print(f"[scheduler] Smart follow-up sweep failed: {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _refresh_instagram_tokens_job,
        "interval",
        hours=24,
        next_run_time=dt.datetime.now(),  # also sweep once on startup
        id="refresh_instagram_tokens",
    )
    scheduler.add_job(
        _smart_follow_up_job,
        "interval",
        minutes=15,
        id="smart_follow_up_job",
    )
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Mivo AI API", version="0.1.0", lifespan=lifespan, root_path=settings.root_path)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    response = JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
    origin = request.headers.get("origin")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


@app.exception_handler(Exception)
async def custom_general_exception_handler(request: Request, exc: Exception):
    response = JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )
    origin = request.headers.get("origin")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


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
async def health() -> dict:
    return {"status": "ok"}
