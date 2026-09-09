import hashlib
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider.base import LLMProvider
from app.ai.provider.factory import get_llm_provider
from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import create_oauth_state_token, decode_oauth_state_token
from app.instagram import service
from app.instagram.client import MetaAPIError, MetaClient

router = APIRouter(tags=["instagram"])


def get_meta_client() -> MetaClient:
    return MetaClient()


class ConnectResponse(BaseModel):
    oauth_url: str


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
    return InstagramStatusResponse(
        connected=True,
        username=account.ig_username,
        expires_at=expires_str,
    )


@router.post("/integrations/instagram/connect", response_model=ConnectResponse)
async def connect_instagram(
    business: Business = Depends(get_current_business),
    meta_client: MetaClient = Depends(get_meta_client),
):
    state = create_oauth_state_token(str(business.id))
    return ConnectResponse(oauth_url=meta_client.build_oauth_url(state))


@router.delete("/integrations/instagram/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_instagram(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
    meta_client: MetaClient = Depends(get_meta_client),
) -> None:
    await service.disconnect_account(db, business.id, meta_client)


@router.get("/integrations/instagram/callback")
async def instagram_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
    meta_client: MetaClient = Depends(get_meta_client),
):
    """Instagram redirects the browser HERE (the backend), never straight to the
    frontend — the code -> token exchange needs client_secret, which must never
    reach the browser. Once done, we redirect the browser on to the dashboard
    (success or error) so the user lands back in the actual UI, not a bare
    JSON/error page."""
    settings = get_settings()
    integrations_url = f"{settings.frontend_url}/integrations"

    try:
        business_id = decode_oauth_state_token(state)
    except Exception:
        return RedirectResponse(f"{integrations_url}?instagram_error=invalid_state")

    try:
        account = await meta_client.exchange_code_for_account(code)
    except MetaAPIError as exc:
        print(f"[instagram_callback] Meta token exchange failed: {exc}")
        return RedirectResponse(f"{integrations_url}?instagram_error=connect_failed")

    import uuid as _uuid

    await service.connect_account(db, _uuid.UUID(business_id), account)
    return RedirectResponse(f"{integrations_url}?instagram_connected=1")


@router.get("/webhooks/instagram")
async def verify_instagram_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
):
    settings = get_settings()
    if hub_mode != "subscribe" or hub_verify_token != settings.meta_webhook_verify_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Verification failed.")
    return Response(content=hub_challenge, media_type="text/plain")


def _verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    if not app_secret:
        # No app secret configured (local dev without real Meta credentials) —
        # skip verification rather than lock out an otherwise-working dev setup.
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    computed = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(computed, received)


@router.post("/webhooks/instagram")
async def receive_instagram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
    meta_client: MetaClient = Depends(get_meta_client),
    x_hub_signature_256: str | None = Header(default=None),
):
    """Acks 200 immediately after processing — Meta expects a fast response and
    retries on non-2xx (plan §11). MVP processes inline via FastAPI's async
    request handling rather than a separate background job queue; at ~10k
    msgs/month this is well within a single request's latency budget."""
    raw_body = await request.body()
    settings = get_settings()
    if not _verify_signature(raw_body, x_hub_signature_256, settings.meta_app_secret):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid webhook signature.")

    body = await request.json()
    for entry in body.get("entry", []):
        for messaging_event in entry.get("messaging", []):
            message = messaging_event.get("message")
            if not message:
                continue

            message_text = message.get("text", "")
            attachments = message.get("attachments", [])
            attachment_url = None
            attachment_type = None

            for att in attachments:
                att_type_candidate = att.get("type")
                payload = att.get("payload", {}) if isinstance(att.get("payload"), dict) else {}
                url_candidate = payload.get("url")

                if att_type_candidate == "audio" and url_candidate:
                    attachment_url = url_candidate
                    attachment_type = "audio"
                    try:
                        from app.ai.audio import transcribe_audio_url
                        transcribed = await transcribe_audio_url(url_candidate)
                        if transcribed:
                            message_text = transcribed
                    except Exception as err:
                        print(f"[InstagramWebhook] Audio transcription error: {err}")
                    break
                elif att_type_candidate == "image" and url_candidate:
                    attachment_url = url_candidate
                    attachment_type = "image"
                    break

            if not attachment_url and attachments:
                att = attachments[0]
                attachment_type = att.get("type", "media")
                payload = att.get("payload", {}) if isinstance(att.get("payload"), dict) else {}
                attachment_url = payload.get("url")

            if not message_text.strip():
                if attachment_type == "image":
                    message_text = "[Mijoz rasm yubordi]"
                elif attachment_type == "video":
                    message_text = "[Video yuborildi]"
                elif attachment_type in ("share", "ig_reel", "story_mention"):
                    message_text = "[Reels / Story ulashildi]"
                elif attachment_type:
                    message_text = f"[{attachment_type.capitalize()} yuborildi]"

            if not message_text.strip() and not attachment_url:
                continue

            if message.get("is_echo"):
                await service.process_echo_message(
                    db,
                    ig_sender_id=messaging_event["sender"]["id"],
                    customer_ig_scoped_id=messaging_event["recipient"]["id"],
                    message_text=message_text,
                    external_message_id=message.get("mid", ""),
                    meta_client=meta_client,
                    attachment_url=attachment_url,
                    attachment_type=attachment_type,
                )
            else:
                await service.process_incoming_message(
                    db,
                    provider,
                    meta_client,
                    ig_recipient_id=messaging_event["recipient"]["id"],
                    customer_ig_scoped_id=messaging_event["sender"]["id"],
                    message_text=message_text,
                    external_message_id=message.get("mid", ""),
                    attachment_url=attachment_url,
                    attachment_type=attachment_type,
                )
    return {"status": "ok"}

