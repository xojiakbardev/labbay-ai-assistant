"""One turn at a time per conversation.

Debouncing only covers messages that arrive within DEBOUNCE_SECONDS of each
other. A message that lands while the previous turn is still waiting on the
LLM would otherwise start a second, parallel turn: two overlapping replies, and
whichever finished last overwrote the other's working_state. Everything that
writes a reply to a customer runs under this lock.

A Postgres advisory lock rather than an in-process one, so it holds across
worker processes. It lives on its own AUTOCOMMIT connection: holding a session
lock needs no open transaction, and the turn's own session stays free to
commit as often as it needs to. Callers should commit their session before
waiting here, so a waiter holds exactly one pooled connection.
"""
import uuid
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.core.db import engine

# Longer than a whole turn (LLM deadline + delivery); a waiter past this fails
# its event, which the sweeper retries — never waits forever.
LOCK_WAIT_TIMEOUT = "120s"


def lock_key(conversation_id: uuid.UUID) -> int:
    return int.from_bytes(conversation_id.bytes[:8], "big", signed=True)


@asynccontextmanager
async def conversation_lock(conversation_id: uuid.UUID):
    key = lock_key(conversation_id)
    async with engine.connect() as raw:
        conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text(f"SET lock_timeout = '{LOCK_WAIT_TIMEOUT}'"))
        try:
            await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        finally:
            await conn.execute(text("RESET lock_timeout"))
        try:
            yield
        finally:
            try:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            except BaseException:
                # A session-level lock that couldn't be released must not go
                # back into the pool with the connection — drop the connection,
                # which releases the lock with it.
                await raw.invalidate()
                raise
