import asyncio
import logging
import httpx

from app.core.config import get_settings

_API_BASE = "https://api.telegram.org"
logger = logging.getLogger("app.telegram")


class TelegramAPIError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str | None = None, timeout: float = 10.0) -> None:
        settings = get_settings()
        raw_token = token or settings.telegram_bot_token or "8597912718:AAEAgp3TpRfz9gDOuGkQAHL7IFJQuvcxqvw"
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
        """Sends a message to the specified Telegram chat with bounded, safe retries
        on transient network or 5xx server errors. Client errors (4xx) fail fast."""
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                try:
                    response = await client.post(
                        f"{_API_BASE}/bot{self._token}/sendMessage",
                        json=payload,
                    )
                    # 4xx client errors (e.g. invalid chat_id, bot blocked) must fail fast without retry
                    if 400 <= response.status_code < 500:
                        body = response.text
                        raise TelegramAPIError(f"Telegram client error ({response.status_code}): {body}")

                    response.raise_for_status()
                    return response.json()
                except TelegramAPIError:
                    raise
                except httpx.HTTPError as exc:
                    last_error = exc
                    body = getattr(getattr(exc, "response", None), "text", "")
                    if attempt < max_retries:
                        delay = backoff_factor * (2 ** (attempt - 1))
                        logger.warning(
                            f"[TelegramClient] Attempt {attempt}/{max_retries} failed ({exc}). Retrying in {delay:.2f}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        raise TelegramAPIError(
                            f"Failed to send Telegram message after {max_retries} attempts: {exc} | body={body}"
                        ) from exc
                except Exception as exc:
                    last_error = exc
                    if attempt < max_retries:
                        delay = backoff_factor * (2 ** (attempt - 1))
                        await asyncio.sleep(delay)
                    else:
                        raise TelegramAPIError(
                            f"Unexpected error sending Telegram message after {max_retries} attempts: {exc}"
                        ) from exc

        raise TelegramAPIError(f"Failed to send Telegram message: {last_error}")
