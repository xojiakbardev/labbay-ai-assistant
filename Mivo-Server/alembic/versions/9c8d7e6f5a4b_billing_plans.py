"""plans, monthly AI-reply usage, businesses.plan_id; four starter plans

Existing businesses get no plan (no limit) until the superadmin assigns one.

Revision ID: 9c8d7e6f5a4b
Revises: e1f2a3b4c5d6
Create Date: 2026-09-12
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9c8d7e6f5a4b"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# name, price, currency, monthly AI replies, default, order — drafts the
# superadmin edits in the panel.
_STARTER_PLANS = (
    ("Sinov", 0, "USD", 500, True, 0),
    ("Start", 39, "USD", 1500, False, 1),
    ("Biznes", 99, "USD", 5000, False, 2),
    ("Pro", 199, "USD", 12000, False, 3),
)


def upgrade() -> None:
    plans = op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("price", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
        sa.Column("monthly_ai_replies", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "uq_plans_single_default", "plans", ["is_default"], unique=True, postgresql_where=sa.text("is_default")
    )
    op.bulk_insert(
        plans,
        [
            {
                "id": uuid.uuid4(), "name": name, "price": price, "currency": currency,
                "monthly_ai_replies": replies, "is_active": True, "is_default": default, "sort_order": order,
            }
            for name, price, currency, replies, default, order in _STARTER_PLANS
        ],
    )

    op.create_table(
        "ai_reply_usage",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("replies", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("business_id", "period_start"),
    )

    op.add_column("businesses", sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(None, "businesses", "plans", ["plan_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_businesses_plan_id", "businesses", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_businesses_plan_id", table_name="businesses")
    op.drop_column("businesses", "plan_id")
    op.drop_table("ai_reply_usage")
    op.drop_index("uq_plans_single_default", table_name="plans")
    op.drop_table("plans")
