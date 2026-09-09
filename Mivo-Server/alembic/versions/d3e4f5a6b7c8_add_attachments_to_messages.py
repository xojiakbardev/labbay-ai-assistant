"""add attachment_url and attachment_type to messages

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-09-09 12:55:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: str | None = 'c2d3e4f5a6b7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('attachment_url', sa.Text(), nullable=True))
    op.add_column('messages', sa.Column('attachment_type', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'attachment_type')
    op.drop_column('messages', 'attachment_url')
