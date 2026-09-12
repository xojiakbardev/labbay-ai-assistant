import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.core.config import get_settings
from app.core.db import get_db
from app.telegram import service
from app.telegram.client import TelegramAPIError, TelegramClient

router = APIRouter(tags=["telegram"])
logger = logging.getLogger("app.telegram.router")


class TelegramConnectResponse(BaseModel):
    deep_link: str
    expires_at: str


class TelegramStatusResponse(BaseModel):
    connected: bool
    username: str | None = None
    chat_id: str | None = None


@router.get("/integrations/telegram/status", response_model=TelegramStatusResponse)
async def telegram_status(
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    connection = await service.get_connection(db, business.id)
    if connection is None or not connection.telegram_chat_id:
        return TelegramStatusResponse(connected=False)

    return TelegramStatusResponse(
        connected=True,
        username=connection.telegram_username,
        chat_id=connection.telegram_chat_id,
    )


@router.post("/integrations/telegram/connect", response_model=TelegramConnectResponse)
async def connect_telegram(
    business: Business = Depends(get_current_business), db: AsyncSession = Depends(get_db)
):
    if not get_settings().telegram_bot_username or not get_settings().telegram_bot_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Telegram bot is not configured on this server.")
    token, expires_at = await service.create_connect_token(db, business.id)
    return TelegramConnectResponse(deep_link=service.build_deep_link(token), expires_at=expires_at.isoformat())


@router.delete("/integrations/telegram/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_telegram(
    business: Business = Depends(get_current_business), db: AsyncSession = Depends(get_db)
) -> None:
    await service.disconnect(db, business.id)


def _secret_is_valid(received: str | None) -> bool:
    expected = get_settings().telegram_webhook_secret
    # No configured secret means nothing can be verified, so nothing is
    # accepted — an unauthenticated webhook here would let anyone bind their
    # own chat to a business's hot-lead alerts.
    if not expected or not received:
        return False
    return hmac.compare_digest(received.encode(), expected.encode())


async def _reply(chat_id: str, text: str) -> None:
    try:
        await TelegramClient().send_message(chat_id=chat_id, text=text)
    except TelegramAPIError as exc:
        logger.warning("[TelegramWebhook] reply to chat %s failed: %s", chat_id, exc)


@router.post("/webhooks/telegram")
async def receive_telegram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not _secret_is_valid(x_telegram_bot_api_secret_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid webhook secret.")

    body = await request.json()
    message = body.get("message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if not chat_id:
        return {"status": "ok"}

    integrations_url = f"{get_settings().frontend_url}/integrations"
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            connected = await service.handle_start_command(db, parts[1].strip(), str(chat_id), chat.get("username"))
            if connected:
                await _reply(
                    str(chat_id),
                    "✅ Mivo AI Sales Assistant-ga muvaffaqiyatli ulandingiz!\n\nEndi Instagram sahifangizdan "
                    "keladigan 🔥 Hot Lead bildirishnomalari va kontaktlar ushbu bot orqali sizga lahzalik yuboriladi.",
                )
            else:
                await _reply(
                    str(chat_id),
                    "⚠️ Havolaning amal qilish muddati tugagan yoki noto'g'ri.\n\nIltimos, platformadagi "
                    f"Integratsiyalar sahifasiga o'tib, yangi ulanish havolasini oling:\n👉 {integrations_url}",
                )
        else:
            await _reply(
                str(chat_id),
                "👋 Assalomu alaykum!\n\n🤖 <b>Mivo AI Sales Bot</b>ga xush kelibsiz.\n\nUshbu bot orqali siz "
                "Instagram sahifangizdan keladigan barcha 🔥 <b>Issiq Lidlar (Hot Leads)</b> va xaridorlarning "
                "telefon raqamlarini lahzalik qabul qilasiz.\n\n🔗 Botni o'z profilingizga ulash uchun:\n"
                f"1. <a href=\"{integrations_url}\">Mivo AI Integratsiyalar</a> sahifasiga kiring\n"
                "2. <b>'Telegram-ni ulash'</b> tugmasini bosing.",
            )
    else:
        await _reply(
            str(chat_id),
            f"ℹ️ Mivo AI bildirishnomalari sozlamalari uchun platformaga kiring:\n👉 {integrations_url}",
        )

    return {"status": "ok"}
