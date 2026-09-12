"""When a conversation is over. Short plain-text bursts after our reply go to a
small model call; if finished, no reply, at most a reaction."""
import logging
import re
import unicodedata
import uuid

from pydantic import BaseModel, Field

from app.ai.context.builder import build_message_history
from app.ai.provider.base import LLMProvider
from app.conversations.models import MESSAGE_TYPE_REACTION, Message
from app.core.config import get_settings
from app import prompts

logger = logging.getLogger("app.ai.closing")

_MAX_WORDS = get_settings().closing_max_words
_MAX_CHARS = get_settings().closing_max_chars
_TRANSCRIPT_MESSAGES = get_settings().closing_transcript_messages


class ClosingDecision(BaseModel):
    reasoning: str = Field(description="One short sentence: why the conversation is or isn't over.")
    conversation_finished: bool = Field(
        description=(
            "True only if the customer's last message is a friendly close — thanks, ok, "
            "agreement, a positive emoji — after the conversation has already reached its end, "
            "and nothing in it needs an answer. False if it answers a question the shop asked, "
            "asks something, carries new information, or is negative in any way: backing out of "
            "a purchase, changing their mind, hesitating, complaining or objecting."
        )
    )
    reaction: str | None = Field(
        default=None,
        description=(
            "When finished: the one emoji a friendly shop assistant would react with, matching "
            "the tone of their message (for example ❤️ or 🙏 for thanks, 🔥 or 😊 for good news), "
            "or null when no reaction fits."
        ),
    )


_SYSTEM_PROMPT = prompts.load("closing.md")


def may_be_closing(burst: list[Message], last_outbound: Message | None) -> bool:
    """Whether the customer's unanswered messages are short, plain text that
    could just close the conversation. Only then is the model asked."""
    if last_outbound is None or not burst:
        return False
    if any(m.attachment_type for m in burst):
        return False  # a photo, voice note or share always gets an answer
    joined = " ".join((m.content or "").strip() for m in burst)
    if not joined.strip() or len(joined) > _MAX_CHARS or "?" in joined or re.search(r"\d", joined):
        return False
    return len(re.findall(r"\w+", joined)) <= _MAX_WORDS


def valid_reaction(value: str | None) -> str | None:
    """The model's reaction, if it's a single emoji — anything else means none."""
    emoji = (value or "").strip()
    if not emoji or len(emoji) > 8:
        return None
    if any(ch.isalnum() or ch.isspace() for ch in emoji):
        return None
    if not any(unicodedata.category(ch) == "So" for ch in emoji):
        return None
    return emoji


def _transcript(messages: list[Message]) -> str:
    # build_message_history drops our reactions; so must this, or the two
    # lists fall out of step.
    messages = [m for m in messages if getattr(m, "message_type", None) != MESSAGE_TYPE_REACTION]
    lines = []
    for m, rendered in zip(messages, build_message_history(messages), strict=True):
        content = rendered["content"]
        if isinstance(content, list):
            content = next((part["text"] for part in content if part.get("type") == "text"), "")
        who = "Customer" if m.sender_type == "customer" else "Shop"
        lines.append(f"{who}: {content}")
    return "\n".join(lines)


async def decide_closing(
    provider: LLMProvider, recent: list[Message], *, business_id: uuid.UUID | None
) -> ClosingDecision:
    """Raises LLMProviderError when the model can't decide; the caller then
    answers normally."""
    return await provider.generate_structured(
        system_prompt=_SYSTEM_PROMPT,
        user_content=_transcript(recent[-_TRANSCRIPT_MESSAGES:]),
        response_schema=ClosingDecision,
        business_id=business_id,
    )


def unanswered_burst(recent: list[Message]) -> tuple[list[Message], Message | None]:
    """(the customer's messages since the last outbound one, oldest first;
    that outbound message, or None if there never was one)."""
    burst: list[Message] = []
    for m in reversed(recent):
        if m.sender_type != "customer":
            return list(reversed(burst)), m
        burst.append(m)
    return list(reversed(burst)), None
