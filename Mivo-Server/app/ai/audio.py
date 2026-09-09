"""Audio transcription service (Speech-to-Text) for voice messages in Uzbek and Russian.

Supports:
1. Groq Whisper-large-v3 (if GROQ_API_KEY is configured)
2. OpenAI Whisper (if OPENAI_API_KEY is configured)
3. Gemini-2.5-Flash multimodal audio via OpenRouter (standard with OPENROUTER_API_KEY)
"""
import base64
import logging
import os
import httpx

from app.core.config import get_settings

logger = logging.getLogger("app.ai.audio")


async def transcribe_audio_url(audio_url: str) -> str:
    """Download audio file from URL and transcribe it accurately to Uzbek/Russian text."""
    if not audio_url:
        return ""

    settings = get_settings()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Download audio file bytes
        try:
            resp = await client.get(audio_url)
            resp.raise_for_status()
            audio_bytes = resp.content
        except Exception as err:
            logger.error(f"[transcribe_audio_url] Failed to download audio from {audio_url}: {err}")
            return ""

        if not audio_bytes:
            return ""

        # Determine audio extension / format
        fmt = "m4a"
        if ".mp3" in audio_url.lower():
            fmt = "mp3"
        elif ".wav" in audio_url.lower():
            fmt = "wav"
        elif ".ogg" in audio_url.lower() or ".oga" in audio_url.lower():
            fmt = "ogg"
        elif ".aac" in audio_url.lower():
            fmt = "aac"

        # 2. Try Groq Whisper (fastest, high accuracy) if GROQ_API_KEY is configured
        groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if groq_api_key:
            try:
                files = {"file": (f"voice.{fmt}", audio_bytes, f"audio/{fmt}")}
                stt_resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {groq_api_key}"},
                    files=files,
                    data={"model": "whisper-large-v3"},
                )
                if stt_resp.status_code == 200:
                    text = stt_resp.json().get("text", "").strip()
                    if text:
                        logger.info(f"[transcribe_audio_url] Groq transcribed: {text[:60]}")
                        return text
            except Exception as err:
                logger.warning(f"[transcribe_audio_url] Groq whisper failed, falling back: {err}")

        # 3. Try OpenAI Whisper if OPENAI_API_KEY is configured
        openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_api_key:
            try:
                files = {"file": (f"voice.{fmt}", audio_bytes, f"audio/{fmt}")}
                stt_resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {openai_api_key}"},
                    files=files,
                    data={"model": "whisper-1"},
                )
                if stt_resp.status_code == 200:
                    text = stt_resp.json().get("text", "").strip()
                    if text:
                        logger.info(f"[transcribe_audio_url] OpenAI transcribed: {text[:60]}")
                        return text
            except Exception as err:
                logger.warning(f"[transcribe_audio_url] OpenAI whisper failed, falling back: {err}")

        # 4. Primary Default: OpenRouter with Gemini 2.5 Flash Multimodal Audio
        api_key = settings.openrouter_api_key
        if api_key:
            try:
                base64_data = base64.b64encode(audio_bytes).decode("utf-8")
                payload = {
                    "model": "google/gemini-2.5-flash",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Ushbu audio xabarni so'zma-so'z matnga aylantiring (Uzbek yoki Rus tilida). "
                                        "Faqat eshitilgan so'zlarni qaytaring, boshqa hech qanday izoh, sharh yoki qo'shimcha so'z qo'shmang."
                                    ),
                                },
                                {
                                    "type": "input_audio",
                                    "input_audio": {
                                        "data": base64_data,
                                        "format": fmt,
                                    },
                                },
                            ],
                        }
                    ],
                    "temperature": 0.1,
                }
                gemini_resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "HTTP-Referer": "https://mivo.uz",
                        "X-Title": "Mivo AI STT",
                    },
                    json=payload,
                )
                if gemini_resp.status_code == 200:
                    data = gemini_resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        raw_text = choices[0].get("message", {}).get("content", "").strip()
                        if raw_text:
                            logger.info(f"[transcribe_audio_url] Gemini transcribed: {raw_text[:60]}")
                            return raw_text
                else:
                    logger.warning(
                        f"[transcribe_audio_url] OpenRouter audio error {gemini_resp.status_code}: {gemini_resp.text[:200]}"
                    )
            except Exception as err:
                logger.error(f"[transcribe_audio_url] OpenRouter Gemini audio transcription error: {err}")

    return ""
