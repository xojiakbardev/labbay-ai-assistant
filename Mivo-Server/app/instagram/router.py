import hashlib
import hmac
import json
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider.factory import get_llm_provider
from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.core.config import get_settings
from app.core.db import get_db
from app.instagram import pipeline, service
from app.instagram.client import MetaClient

router = APIRouter(tags=["instagram"])
logger = logging.getLogger("app.instagram.router")


def get_meta_client() -> MetaClient:
    return MetaClient()


class ConnectResponse(BaseModel):
    oauth_url: str


class CompleteConnectRequest(BaseModel):
    completion_id: str = Field(min_length=10, max_length=64)


class InstagramStatusResponse(BaseModel):
    connected: bool
    username: str | None = None
    expires_at: str | None = None


@router.get("/integrations/instagram/status", response_model=InstagramStatusResponse)
async def instagram_status(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    account = await service.get_account(db, business.id)
    if account is None or account.status != "connected":
        return InstagramStatusResponse(connected=False)

    expires_str = account.token_expires_at.isoformat() if account.token_expires_at else None
    return InstagramStatusResponse(connected=True, username=account.ig_username, expires_at=expires_str)


@router.post("/integrations/instagram/connect", response_model=ConnectResponse)
async def connect_instagram(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
    meta_client: MetaClient = Depends(get_meta_client),
):
    state = await service.start_oauth(db, business.id)
    return ConnectResponse(oauth_url=meta_client.build_oauth_url(state))


@router.post("/integrations/instagram/complete", response_model=InstagramStatusResponse)
async def complete_instagram_connect(
    body: CompleteConnectRequest,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
    meta_client: MetaClient = Depends(get_meta_client),
):
    """Second half of the connect flow — see app/instagram/models.py:OAuthState."""
    try:
        account = await service.complete_oauth(db, business.id, body.completion_id, meta_client)
    except service.OAuthFlowError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    expires_str = account.token_expires_at.isoformat() if account.token_expires_at else None
    return InstagramStatusResponse(connected=True, username=account.ig_username, expires_at=expires_str)


@router.delete("/integrations/instagram/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_instagram(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
    meta_client: MetaClient = Depends(get_meta_client),
) -> None:
    await service.disconnect_account(db, business.id, meta_client)


@router.get("/integrations/instagram/callback")
async def instagram_callback(
    state: str = Query(..., max_length=64),
    code: str | None = Query(default=None, max_length=2048),
    error: str | None = Query(default=None, max_length=200),
    db: AsyncSession = Depends(get_db),
):
    """Instagram redirects the browser HERE. The code is parked, not used:
    the browser is sent back to the dashboard with a one-time completion id,
    and the logged-in owner's dashboard finishes the connection
    (POST /integrations/instagram/complete)."""
    integrations_url = f"{get_settings().frontend_url}/integrations"
    if error or not code:
        return RedirectResponse(f"{integrations_url}?{urlencode({'instagram_error': 'denied'})}")
    try:
        completion_id = await service.park_oauth_code(db, state, code)
    except service.OAuthFlowError:
        return RedirectResponse(f"{integrations_url}?{urlencode({'instagram_error': 'invalid_state'})}")
    return RedirectResponse(f"{integrations_url}?{urlencode({'instagram_pending': completion_id})}")


@router.get("/webhooks/instagram")
async def verify_instagram_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge", max_length=256),
    hub_verify_token: str = Query(alias="hub.verify_token", max_length=256),
):
    expected = get_settings().meta_webhook_verify_token
    if (
        hub_mode != "subscribe"
        or not expected
        or not hmac.compare_digest(hub_verify_token.encode(), expected.encode())
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Verification failed.")
    return Response(content=hub_challenge, media_type="text/plain")


def verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Fails closed: without an app secret nothing can be verified, so nothing
    is accepted — an unverified webhook would let anyone inject messages into
    a business's inbox and spend its LLM budget."""
    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    computed = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature_header.removeprefix("sha256="))


@router.post("/webhooks/instagram")
async def receive_instagram_webhook(
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    meta_client: MetaClient = Depends(get_meta_client),
    x_hub_signature_256: str | None = Header(default=None),
):
    """Verify, persist, 200 — the turn itself runs after the response (see
    app/instagram/pipeline.py). Meta wants an answer within seconds; a turn is
    several LLM calls, so running it here made Meta give up and retry.

    The LLM provider is handed over as a factory, resolved only when a turn
    actually runs: a misconfigured provider must not stop customer messages
    from being recorded (the pipeline hands that conversation to a human)."""
    provider_factory = request.app.dependency_overrides.get(get_llm_provider, get_llm_provider)
    raw_body = await request.body()
    if not verify_signature(raw_body, x_hub_signature_256, get_settings().meta_app_secret):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid webhook signature.")

    try:
        body = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not JSON.") from exc

    for event in pipeline.parse_webhook_body(body):
        event_id = await pipeline.ingest_event(db, event)
        if event_id is not None:
            background.add_task(pipeline.process_event, event_id, provider=provider_factory, meta_client=meta_client)
    return {"status": "ok"}
