"""One turn at a time per conversation: a Postgres advisory lock on its own
AUTOCOMMIT connection. Commit your session before waiting on it."""
import uuid
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import engine

# Longer than a whole turn (LLM deadline + delivery); a waiter past this fails
# its event, which the sweeper retries — never waits forever.
LOCK_WAIT_TIMEOUT = f"{get_settings().conversation_lock_wait_seconds}s"


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
