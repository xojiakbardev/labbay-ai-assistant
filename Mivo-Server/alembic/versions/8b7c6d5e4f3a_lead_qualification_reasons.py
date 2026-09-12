"""leads.qualification_reasons: why the lead scored as it did, per language

Revision ID: 8b7c6d5e4f3a
Revises: 9c8d7e6f5a4b
Create Date: 2026-09-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8b7c6d5e4f3a"
down_revision: Union[str, None] = "9c8d7e6f5a4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("qualification_reasons", postgresql.JSONB(), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("leads", "qualification_reasons")
