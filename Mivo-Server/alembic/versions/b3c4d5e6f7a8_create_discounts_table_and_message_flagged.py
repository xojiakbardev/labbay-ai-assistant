"""create discounts table and add flagged_for_review to messages

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-09-09 11:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create discounts table
    op.create_table(
        'discounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=True),
        sa.Column('discount_type', sa.String(length=20), nullable=False, server_default='percentage'),
        sa.Column('value', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('conditions', sa.Text(), nullable=True),
        sa.Column('valid_from', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_discounts_business_id', 'discounts', ['business_id'])

    # 2. Add flagged_for_review to messages table
    op.add_column(
        'messages',
        sa.Column('flagged_for_review', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('messages', 'flagged_for_review')
    op.drop_index('ix_discounts_business_id', table_name='discounts')
    op.drop_table('discounts')
