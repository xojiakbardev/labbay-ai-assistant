"""Fill in missing Instagram @usernames / names for customers now, instead of
waiting for the scheduled backfill. Each customer is looked up with ITS OWN
business's Instagram token (app/instagram/service.py:backfill_customer_profiles).

    python sync_script.py
"""
import asyncio

import app.core.models_registry  # noqa: F401
from app.core.db import async_session_factory
from app.instagram.client import MetaClient
from app.instagram.service import backfill_customer_profiles


async def main() -> None:
    total = 0
    async with async_session_factory() as db:
        while attempted := await backfill_customer_profiles(db, MetaClient(), limit=50):
            total += attempted
    print(f"Looked up {total} customer profile(s); see the log for how many Meta returned.")


if __name__ == "__main__":
    asyncio.run(main())
