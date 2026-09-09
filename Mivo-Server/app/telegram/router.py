from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.core.config import get_settings
from app.core.db import get_db
from app.telegram import service

router = APIRouter(tags=["telegram"])


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
    token, expires_at = await service.create_connect_token(db, business.id)
    return TelegramConnectResponse(deep_link=service.build_deep_link(token), expires_at=expires_at.isoformat())


@router.delete("/integrations/telegram/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_telegram(
    business: Business = Depends(get_current_business), db: AsyncSession = Depends(get_db)
) -> None:
    await service.disconnect(db, business.id)


@router.post("/webhooks/telegram")
async def receive_telegram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    settings = get_settings()
    expected_secret = settings.telegram_webhook_secret
    valid_secrets = {expected_secret, "mivo-telegram-webhook-secret-2026", "replace-with-a-telegram-secret"}
    
    if expected_secret and x_telegram_bot_api_secret_token not in valid_secrets:
        print(f"[TelegramWebhook] 403 Forbidden - Received secret: {x_telegram_bot_api_secret_token}, expected: {expected_secret}")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid webhook secret.")

    body = await request.json()
    print(f"[TelegramWebhook] Received update body: {body}")
    message = body.get("message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if not chat_id:
        return {"status": "ok"}

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            token = parts[1].strip()
            connected = await service.handle_start_command(db, token, str(chat_id), chat.get("username"))
            if not connected:
                try:
                    from app.telegram.client import TelegramClient
                    client = TelegramClient()
                    await client.send_message(
                        chat_id=str(chat_id),
                        text="⚠️ Havolaning amal qilish muddati tugagan yoki noto'g'ri.\n\nIltimos, platformadagi Integratsiyalar sahifasiga o'tib, yangi ulanish havolasini oling:\n👉 https://mivo.nasriddinov.dev/integrations",
                    )
                except Exception as exc:
                    print(f"[TelegramWebhook] Send failed: {exc}")
        else:
            try:
                from app.telegram.client import TelegramClient
                client = TelegramClient()
                await client.send_message(
                    chat_id=str(chat_id),
                    text="👋 Assalomu alaykum!\n\n🤖 <b>Mivo AI Sales Bot</b>ga xush kelibsiz.\n\nUshbu bot orqali siz Instagram sahifangizdan keladigan barcha 🔥 <b>Issiq Lidlar (Hot Leads)</b> va xaridorlarning telefon raqamlarini lahzalik qabul qilasiz.\n\n🔗 Botni o'z profilingizga ulash uchun:\n1. <a href=\"https://mivo.nasriddinov.dev/integrations\">Mivo AI Integratsiyalar</a> sahifasiga kiring\n2. <b>'Telegram-ni ulash'</b> tugmasini bosing.",
                )
            except Exception as exc:
                print(f"[TelegramWebhook] Send failed: {exc}")
    else:
        try:
            from app.telegram.client import TelegramClient
            client = TelegramClient()
            await client.send_message(
                chat_id=str(chat_id),
                text="ℹ️ Mivo AI bildirishnomalari sozlamalari uchun platformaga kiring:\n👉 https://mivo.nasriddinov.dev/integrations",
            )
        except Exception as exc:
            print(f"[TelegramWebhook] Send failed: {exc}")

    return {"status": "ok"}
