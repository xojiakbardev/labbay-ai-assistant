"""enable pg_trgm for fuzzy product search fallback

Revision ID: c0e02b8b788b
Revises: 800b1fb18d9d
Create Date: 2026-08-21 18:12:36.042501

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0e02b8b788b'
down_revision: Union[str, None] = '800b1fb18d9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Trigram similarity is the fuzzy-match fallback for search_products
    # (app/products/search.py) — plainto_tsquery's exact-token matching (even
    # OR'd) can't bridge "sportivniy" -> "sport" or similar near-miss
    # spelling/transliteration gaps; trigram similarity can. GIN trigram
    # indexes make ILIKE-style and similarity() lookups on name/description
    # fast even as the catalog grows.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_name_trgm "
        "ON products USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_description_trgm "
        "ON products USING gin (description gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_description_trgm")
    op.execute("DROP INDEX IF EXISTS ix_products_name_trgm")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
