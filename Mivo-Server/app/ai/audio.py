"""Voice-message transcription (Uzbek / Russian / English).

One provider, chosen by STT_PROVIDER — not a chain that silently tries three
vendors and hands the AI a placeholder when all of them fail. If transcription
isn't configured or doesn't work, the caller gets TranscriptionError and the
voice note goes to a human instead of being answered blind.
"""
import base64
import logging
import uuid

import httpx

from app.core.config import get_settings

logger = logging.getLogger("app.ai.audio")

# Instagram voice notes are well under this; anything bigger isn't one.
MAX_AUDIO_BYTES = 25 * 1024 * 1024
_DOWNLOAD_TIMEOUT = 20.0
_STT_TIMEOUT = 45.0

_PROMPT = (
    "Ushbu audio xabarni so'zma-so'z matnga aylantiring (o'zbek, rus yoki ingliz tilida, qaysi tilda "
    "gapirilgan bo'lsa). Faqat eshitilgan so'zlarni qaytaring, hech qanday izoh qo'shmang."
)


class TranscriptionError(Exception):
    pass


def _audio_format(url: str, content_type: str | None) -> str:
    lowered = url.lower().split("?", 1)[0]
    for ext in ("mp3", "wav", "ogg", "oga", "aac", "m4a", "mp4"):
        if lowered.endswith(f".{ext}"):
            return "ogg" if ext == "oga" else ext
    if content_type:
        subtype = content_type.split("/")[-1].split(";")[0].strip()
        if subtype in ("mpeg", "mp3"):
            return "mp3"
        if subtype in ("wav", "ogg", "aac", "mp4"):
            return subtype
    return "mp4"  # Instagram voice notes are AAC in an MP4 container


async def _download(client: httpx.AsyncClient, url: str) -> tuple[bytes, str | None]:
    if not url.startswith("https://"):
        raise TranscriptionError("audio URL is not https")
    chunks: list[bytes] = []
    size = 0
    async with client.stream("GET", url, timeout=_DOWNLOAD_TIMEOUT) as response:
        if response.status_code != 200:
            raise TranscriptionError(f"audio download failed: HTTP {response.status_code}")
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                raise TranscriptionError("audio exceeds the size limit")
            chunks.append(chunk)
    if not size:
        raise TranscriptionError("audio download was empty")
    return b"".join(chunks), response.headers.get("content-type")


def _json_body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise TranscriptionError("transcription returned a non-JSON response") from exc
    if not isinstance(body, dict):
        raise TranscriptionError("transcription returned an unexpected response")
    return body


async def _whisper(client: httpx.AsyncClient, endpoint: str, model: str, key: str, audio: bytes, fmt: str) -> str:
    response = await client.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}"},
        files={"file": (f"voice.{fmt}", audio, f"audio/{fmt}")},
        data={"model": model},
        timeout=_STT_TIMEOUT,
    )
    if response.status_code != 200:
        raise TranscriptionError(f"transcription failed: HTTP {response.status_code}: {response.text[:200]}")
    return str(_json_body(response).get("text") or "").strip()


async def _openrouter(
    client: httpx.AsyncClient, key: str, model: str, audio: bytes, fmt: str
) -> tuple[str, dict]:
    response = await client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "X-Title": "Mivo AI STT"},
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {
                            "type": "input_audio",
                            "input_audio": {"data": base64.b64encode(audio).decode(), "format": fmt},
                        },
                    ],
                }
            ],
            "temperature": 0.0,
            "usage": {"include": True},
        },
        timeout=_STT_TIMEOUT,
    )
    if response.status_code != 200:
        raise TranscriptionError(f"transcription failed: HTTP {response.status_code}: {response.text[:200]}")
    data = _json_body(response)
    choices = data.get("choices") or []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise TranscriptionError("transcription returned no message")
    return str(message.get("content") or "").strip(), data.get("usage") or {}


async def transcribe_audio_url(audio_url: str, business_id: uuid.UUID | None = None) -> str:
    """Returns the transcript. Raises TranscriptionError on anything else."""
    settings = get_settings()
    provider = settings.stt_provider.strip().lower()
    if not provider:
        raise TranscriptionError("voice transcription is not configured (STT_PROVIDER is empty)")

    async with httpx.AsyncClient() as client:
        try:
            audio, content_type = await _download(client, audio_url)
            fmt = _audio_format(audio_url, content_type)
            if provider == "openrouter":
                if not settings.openrouter_api_key:
                    raise TranscriptionError("OPENROUTER_API_KEY is not set")
                text, usage = await _openrouter(client, settings.openrouter_api_key, settings.stt_model, audio, fmt)
                if business_id is not None:
                    from app.ai.provider.openrouter import log_usage

                    await log_usage(business_id, "transcription", settings.stt_model, usage)
            elif provider in ("groq", "openai"):
                if not settings.stt_api_key:
                    raise TranscriptionError("STT_API_KEY is not set")
                endpoint = (
                    "https://api.groq.com/openai/v1/audio/transcriptions"
                    if provider == "groq"
                    else "https://api.openai.com/v1/audio/transcriptions"
                )
                text = await _whisper(client, endpoint, settings.stt_model, settings.stt_api_key, audio, fmt)
            else:
                raise TranscriptionError(f"unknown STT_PROVIDER {provider!r}")
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"transcription request failed: {type(exc).__name__}") from exc

    if not text:
        raise TranscriptionError("transcription came back empty")
    return text
