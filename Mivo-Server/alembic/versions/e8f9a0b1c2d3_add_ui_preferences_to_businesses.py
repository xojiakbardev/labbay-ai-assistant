"""add ui_preferences to businesses and summaries to leads

Revision ID: e8f9a0b1c2d3
Revises: 800b1fb18d9d
Create Date: 2026-08-26 01:58:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e8f9a0b1c2d3'
down_revision: Union[str, None] = 'c0e02b8b788b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'businesses',
        sa.Column(
            'ui_preferences',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
    )
    op.add_column(
        'leads',
        sa.Column(
            'summaries',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('leads', 'summaries')
    op.drop_column('businesses', 'ui_preferences')
