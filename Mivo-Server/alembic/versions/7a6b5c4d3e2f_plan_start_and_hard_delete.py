"""businesses.plan_started_at; reply usage per plan month; payments outlive a business

Revision ID: 7a6b5c4d3e2f
Revises: 8b7c6d5e4f3a
Create Date: 2026-09-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "7a6b5c4d3e2f"
down_revision: Union[str, None] = "8b7c6d5e4f3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Existing counters were calendar months starting at local midnight.
_OFFSET = get_settings().billing_utc_offset_hours


def upgrade() -> None:
    op.add_column("businesses", sa.Column("plan_started_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column(
        "ai_reply_usage", "period_start", type_=sa.DateTime(timezone=True),
        postgresql_using=f"(period_start::timestamp AT TIME ZONE 'UTC') - interval '{_OFFSET} hours'",
    )
    op.drop_constraint("payments_business_id_fkey", "payments", type_="foreignkey")
    op.alter_column("payments", "business_id", nullable=True)
    op.create_foreign_key(
        "payments_business_id_fkey", "payments", "businesses", ["business_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.execute("DELETE FROM payments WHERE business_id IS NULL")
    op.drop_constraint("payments_business_id_fkey", "payments", type_="foreignkey")
    op.alter_column("payments", "business_id", nullable=False)
    op.create_foreign_key(
        "payments_business_id_fkey", "payments", "businesses", ["business_id"], ["id"], ondelete="CASCADE"
    )
    op.alter_column(
        "ai_reply_usage", "period_start", type_=sa.Date(),
        postgresql_using=f"(period_start + interval '{_OFFSET} hours')::date",
    )
    op.drop_column("businesses", "plan_started_at")
