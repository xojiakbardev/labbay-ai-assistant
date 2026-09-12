"""Spend guards checked before every AI turn.

Every customer message costs at least three LLM calls. Without a ceiling, a
reply loop, a bot on the other end, or one customer typing all night runs up
the bill unnoticed. Past a limit the AI stops for that business/conversation
and the owner is told — the conversation goes to a human rather than silence.
"""
import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AiUsageLog
from app.conversations.models import Message
from app.core.config import get_settings


async def cost_today_usd(db: AsyncSession, business_id: uuid.UUID) -> float:
    start = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total = await db.scalar(
        select(func.coalesce(func.sum(AiUsageLog.cost_usd), 0)).where(
            AiUsageLog.business_id == business_id, AiUsageLog.created_at >= start
        )
    )
    return float(total or 0)


async def ai_turns_last_hour(db: AsyncSession, conversation_id: uuid.UUID) -> int:
    """AI *turns*, not messages: the 2-3 parts of one split reply are written
    together in one commit, so they share their second; photos don't count."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    return int(
        await db.scalar(
            select(func.count(func.distinct(func.date_trunc("second", Message.created_at)))).where(
                Message.conversation_id == conversation_id,
                Message.sender_type == "ai",
                Message.message_type == "text",
                Message.created_at >= since,
            )
        )
        or 0
    )


async def limit_reason(db: AsyncSession, business_id: uuid.UUID, conversation_id: uuid.UUID) -> str | None:
    """None if the AI may take another turn here, else a human-readable
    reason (shown to the owner)."""
    settings = get_settings()
    spent = await cost_today_usd(db, business_id)
    if spent >= settings.ai_daily_cost_limit_usd:
        return (
            f"Bugungi AI xarajati limiti tugadi (${spent:.2f} / ${settings.ai_daily_cost_limit_usd:.2f}). "
            "Suhbatlar operatorga o'tkazildi."
        )
    turns = await ai_turns_last_hour(db, conversation_id)
    if turns >= settings.ai_max_turns_per_conversation_per_hour:
        return (
            f"Bu suhbatda so'nggi bir soatda AI {turns} marta javob berdi — limit "
            f"({settings.ai_max_turns_per_conversation_per_hour}). Suhbat operatorga o'tkazildi."
        )
    return None
