"""Embed every product that isn't embedded yet.

Run once after enabling semantic search, and again after changing
EMBEDDING_MODEL or EMBEDDING_DIMENSIONS — vectors from a different model live in
a different space, so search ignores them until they're rebuilt.

    python backfill_embeddings.py            # only what's missing or stale
    python backfill_embeddings.py --all      # re-embed everything

Safe to re-run and safe to interrupt: work is committed batch by batch, and
anything already up to date is skipped without an API call.
"""
import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

import app.core.models_registry  # noqa: F401
from app.ai.embeddings.factory import get_embedding_provider
from app.core.db import async_session_factory
from app.products.embeddings import refresh_many
from app.products.models import Product

BATCH = 50


async def backfill(force: bool) -> int:
    provider = get_embedding_provider()
    if provider is None:
        print("Embeddings are not configured (EMBEDDING_API_KEY / EMBEDDING_ENABLED) — nothing to do.")
        return 0

    print(f"[backfill] model={provider.model} dimensions={provider.dimensions}")
    embedded = 0

    async with async_session_factory() as db:
        result = await db.execute(select(Product).options(selectinload(Product.variants)))
        products = list(result.scalars().unique().all())
        print(f"[backfill] {len(products)} product(s) in the catalog")

        for start in range(0, len(products), BATCH):
            batch = products[start : start + BATCH]
            if force:
                for product in batch:
                    product.embedding_hash = None
            count = await refresh_many(db, batch, provider=provider)
            await db.commit()
            embedded += count
            print(f"[backfill] {min(start + BATCH, len(products))}/{len(products)} — {embedded} embedded")

    print(f"[backfill] done: {embedded} product(s) embedded")
    return embedded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all", action="store_true", help="re-embed every product, not just missing/stale ones"
    )
    asyncio.run(backfill(force=parser.parse_args().all))


if __name__ == "__main__":
    main()
