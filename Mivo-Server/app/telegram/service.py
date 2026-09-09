"""Telegram connect flow (deep link + /start token) and hot-lead notification
(plan §12): Mivo dashboard -> connect token -> t.me deep link -> /start <token>
-> business identified -> connected."""
import datetime as dt
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.telegram.models import TelegramConnection

CONNECT_TOKEN_TTL_MINUTES = 30


async def create_connect_token(db: AsyncSession, business_id: uuid.UUID) -> tuple[str, dt.datetime]:
    token = secrets.token_urlsafe(32)[:64]  # Telegram deep-link payloads are capped at 64 chars
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=CONNECT_TOKEN_TTL_MINUTES)

    connection = await db.scalar(
        select(TelegramConnection).where(TelegramConnection.business_id == business_id)
    )
    if connection is None:
        connection = TelegramConnection(
            business_id=business_id, connect_token=token, connect_token_expires_at=expires_at
        )
        db.add(connection)
    else:
        # Regenerating a token doesn't tear down an existing connection — the old
        # chat_id stays connected until a new /start actually completes.
        connection.connect_token = token
        connection.connect_token_expires_at = expires_at
    await db.commit()
    return token, expires_at


async def disconnect(db: AsyncSession, business_id: uuid.UUID) -> None:
    """Clears the chat link but keeps the row (and its connect_token history)
    — the owner just re-runs the deep-link flow to reconnect."""
    connection = await db.scalar(
        select(TelegramConnection).where(TelegramConnection.business_id == business_id)
    )
    if connection is None:
        return
    connection.telegram_chat_id = None
    connection.telegram_username = None
    connection.connected_at = None
    await db.commit()


def build_deep_link(token: str) -> str:
    settings = get_settings()
    username = (settings.telegram_bot_username or "MivoSalesBot").lstrip("@").strip()
    return f"https://t.me/{username}?start={token}"


async def get_connection(db: AsyncSession, business_id: uuid.UUID) -> TelegramConnection | None:
    return await db.scalar(
        select(TelegramConnection).where(TelegramConnection.business_id == business_id)
    )


async def handle_start_command(
    db: AsyncSession, token: str, chat_id: str, username: str | None
) -> bool:
    """Returns True if the token matched an unexpired connect request."""
    connection = await db.scalar(
        select(TelegramConnection).where(TelegramConnection.connect_token == token)
    )
    if connection is None:
        return False
    if connection.connect_token_expires_at < dt.datetime.now(dt.timezone.utc):
        return False

    connection.telegram_chat_id = str(chat_id)
    connection.telegram_username = username
    connection.connected_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()

    # Send confirmation message to the Telegram chat
    try:
        from app.telegram.client import TelegramClient
        client = TelegramClient()
        await client.send_message(
            chat_id=str(chat_id),
            text="✅ Mivo AI Sales Assistant-ga muvaffaqiyatli ulandingiz!\n\nEndi Instagram sahifangizdan keladigan 🔥 Hot Lead bildirishnomalari va kontaktlar ushbu bot orqali sizga lahzalik yuboriladi.",
        )
    except Exception as exc:
        print(f"[TelegramService] Failed to send start confirmation: {exc}")

    return True
