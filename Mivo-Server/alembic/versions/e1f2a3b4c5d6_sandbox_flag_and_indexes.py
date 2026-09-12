"""customers.is_sandbox; indexes for the inbox, the spend check and lead lookups

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-09-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("is_sandbox", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute("UPDATE customers SET is_sandbox = true WHERE ig_scoped_id LIKE 'sandbox\\_%'")
    op.create_index(
        "ix_conversations_business_recent",
        "conversations",
        ["business_id", sa.text("last_message_at DESC NULLS LAST"), "id"],
    )
    op.create_index(
        "ix_ai_usage_logs_business_created",
        "ai_usage_logs",
        ["business_id", "created_at"],
        postgresql_include=["cost_usd"],
    )
    op.create_index("ix_leads_conversation_id", "leads", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_leads_conversation_id", table_name="leads")
    op.drop_index("ix_ai_usage_logs_business_created", table_name="ai_usage_logs")
    op.drop_index("ix_conversations_business_recent", table_name="conversations")
    op.drop_column("customers", "is_sandbox")
