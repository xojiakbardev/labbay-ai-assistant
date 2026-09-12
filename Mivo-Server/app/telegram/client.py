import asyncio
import logging

import httpx

from app.core.config import get_settings

_API_BASE = get_settings().telegram_api_base
logger = logging.getLogger("app.telegram")


class TelegramAPIError(Exception):
    pass


def _describe(exc: httpx.HTTPError) -> str:
    """Error text that never contains the request URL — the bot token is part
    of the URL path, and exception messages end up in logs and in
    leads.last_notification_error."""
    response = getattr(exc, "response", None)
    if response is not None:
        return f"HTTP {response.status_code}: {response.text[:300]}"
    return type(exc).__name__


class TelegramClient:
    def __init__(self, token: str | None = None, timeout: float = 10.0) -> None:
        raw_token = token or get_settings().telegram_bot_token
        if not raw_token:
            raise TelegramAPIError("TELEGRAM_BOT_TOKEN is not configured.")
        self._token = raw_token.removeprefix("bot").strip()
        self._timeout = timeout

    async def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
        max_retries: int = 3,
        backoff_factor: float = 0.2,
    ) -> dict:
        """Sends a message to the specified Telegram chat with bounded retries
        on transient network or 5xx errors. Client errors (4xx) fail fast."""
        payload: dict = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        last_error = ""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(1, max_retries + 1):
                try:
                    response = await client.post(f"{_API_BASE}/bot{self._token}/sendMessage", json=payload)
                except httpx.HTTPError as exc:
                    last_error = _describe(exc)
                else:
                    if 400 <= response.status_code < 500:
                        raise TelegramAPIError(
                            f"Telegram client error ({response.status_code}): {response.text[:300]}"
                        )
                    if response.status_code < 400:
                        return response.json()
                    last_error = f"HTTP {response.status_code}: {response.text[:300]}"

                if attempt < max_retries:
                    delay = backoff_factor * (2 ** (attempt - 1))
                    logger.warning("[TelegramClient] attempt %d/%d failed (%s)", attempt, max_retries, last_error)
                    await asyncio.sleep(delay)

        raise TelegramAPIError(f"Failed to send Telegram message after {max_retries} attempts: {last_error}")
