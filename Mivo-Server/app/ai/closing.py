"""Knowing when a conversation is over.

"Hop", "ok", "rahmat", 👍 after the AI's last word used to get a fresh reply
every time — four "if you have questions, I'm here" in a row to a customer who
had already said goodbye. A person would stop there, or just react to the
message.

Whether the conversation has ended, and which reaction fits, is the model's
call (what "ok" means depends on what came before it). A cheap deterministic
filter decides only whether to ask: a burst with a question, a number, or more
than a few words is always answered normally, without spending a call.
"""
import logging
import re
import unicodedata
import uuid

from pydantic import BaseModel, Field

from app.ai.context.builder import build_message_history
from app.ai.provider.base import LLMProvider
from app.conversations.models import MESSAGE_TYPE_REACTION, Message

logger = logging.getLogger("app.ai.closing")

_MAX_WORDS = 6
_MAX_CHARS = 60
_TRANSCRIPT_MESSAGES = 6


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


_SYSTEM_PROMPT = """You watch an Instagram DM between a shop's sales assistant and a customer. \
Decide whether the customer's latest message has ended the conversation, so that a reply would \
only be noise — the way a person stops answering "ok" after the goodbye has been said — and, if \
so, which emoji reaction to leave on their message.

The conversation is NOT finished when:
- the shop's last message asked something — even a one-word "ha" or "M" is an answer to it;
- the customer backs out, changes their mind or hesitates ("olgim kelmay qoldi", "kerak emas", \
"keyinroq", "передумал") — a salesperson answers that once, warmly;
- the message carries a complaint, an objection, a question or anything new.

A reaction has to fit the message's tone; when none clearly does, leave it null."""


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
