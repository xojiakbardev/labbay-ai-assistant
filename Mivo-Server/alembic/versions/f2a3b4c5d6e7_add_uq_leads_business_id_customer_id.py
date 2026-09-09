"""add uq_leads_business_id_customer_id

Revision ID: f2a3b4c5d6e7
Revises: e8f9a0b1c2d3
Create Date: 2026-08-26 22:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e8f9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Deduplicate existing leads per (business_id, customer_id) keeping latest updated
    op.execute("""
        DELETE FROM leads a USING leads b
        WHERE a.updated_at < b.updated_at
          AND a.business_id = b.business_id
          AND a.customer_id = b.customer_id;
    """)
    op.create_unique_constraint(
        'uq_leads_business_id_customer_id',
        'leads',
        ['business_id', 'customer_id']
    )


def downgrade() -> None:
    op.drop_constraint('uq_leads_business_id_customer_id', 'leads', type_='unique')
