"""add search_normalized to products with gin trigram index

Revision ID: a2b3c4d5e6f7
Revises: c1d2e3f4a5b6
Create Date: 2026-09-09 11:37:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column('products', sa.Column('search_normalized', sa.Text(), nullable=True))
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_search_normalized_trgm "
        "ON products USING gin (search_normalized gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_search_normalized_trgm")
    op.drop_column('products', 'search_normalized')
