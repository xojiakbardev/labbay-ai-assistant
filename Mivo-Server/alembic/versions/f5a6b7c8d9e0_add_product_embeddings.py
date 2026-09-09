"""add product embeddings for semantic search

Lexical search can only match words the catalog already contains, so a customer
who describes what they want instead of naming it ("qishga issiq narsa kerak")
finds nothing. This adds the vector column and its HNSW index.

Requires the pgvector extension, which the stock postgres image does NOT carry —
docker-compose.yml now uses pgvector/pgvector:pg16. On a managed Postgres,
enable the extension before running this.

The column width must match EMBEDDING_DIMENSIONS. It is hard-coded here rather
than read from settings because a migration describes what the database
actually contains, and it must not change meaning with the environment.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-09-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f5a6b7c8d9e0'
down_revision: Union[str, None] = 'e4f5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# pgvector's HNSW index supports at most 2000 dimensions, which is why the
# embeddings are shortened to 1536 rather than the model's native 3072.
EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(f"ALTER TABLE products ADD COLUMN embedding vector({EMBEDDING_DIMENSIONS})")
    op.add_column('products', sa.Column('embedding_hash', sa.String(length=64), nullable=True))
    op.add_column('products', sa.Column('embedding_model', sa.String(length=100), nullable=True))

    # Cosine distance, matching how the query is compared in search.py. The
    # index is partial: most rows have no embedding on day one, and there's no
    # point indexing nulls.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_embedding_hnsw "
        "ON products USING hnsw (embedding vector_cosine_ops) "
        "WHERE embedding IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_embedding_hnsw")
    op.drop_column('products', 'embedding_model')
    op.drop_column('products', 'embedding_hash')
    op.drop_column('products', 'embedding')
    # The extension is left in place: other things may depend on it, and
    # dropping it would destroy any other vector column in the database.
