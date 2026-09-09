"""Backfill script to populate search_normalized for all existing products.

Usage:
    python backfill_search_normalized.py
"""
import asyncio

from sqlalchemy import select
import app.core.models_registry  # noqa: F401
from app.core.db import async_session_factory
from app.products.models import Product
from app.products.search_normalize import normalize_for_search


async def backfill() -> None:
    print("[Backfill] Starting search_normalized backfill for existing products...")
    async with async_session_factory() as db:
        result = await db.execute(select(Product))
        products = list(result.scalars().all())
        total = len(products)
        print(f"[Backfill] Found {total} product(s) in database.")

        updated_count = 0
        for p in products:
            normalized = normalize_for_search(f"{p.name} {p.description or ''}")
            if p.search_normalized != normalized:
                p.search_normalized = normalized
                updated_count += 1

        if updated_count > 0:
            await db.commit()
            print(f"[Backfill] Successfully updated and saved {updated_count}/{total} products.")
        else:
            print("[Backfill] All products already have up-to-date search_normalized. No changes needed.")


if __name__ == "__main__":
    asyncio.run(backfill())
