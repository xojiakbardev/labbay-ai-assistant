"""review hardening: schema the code already assumed, plus the columns and
constraints the reliability/security fixes need

- product_variants.attributes/image_url/images/barcode were used by the model
  since the variants rework but no migration ever created them, so a fresh
  database came up unable to load a single product. Production already has
  them (added by hand), hence IF NOT EXISTS.
- products.search_vector indexed `attributes::text`, i.e. JSON keys and
  internal fields (image_url, ai_instructions) became search words. It now
  indexes attribute *values* only, minus internal keys.
- One conversation per (business, customer) and one business per Instagram
  account are now enforced by the database, not by luck.
- messages.delivery_status is the outbox: an outbound message is recorded as
  pending before it is sent and marked sent/failed after, so a retry resends
  exactly what didn't go out instead of generating a second reply.
- webhook_events gets a lease (claimed_at) and retry bookkeeping so an event
  stuck in `processing` after a crash is picked up again.
- refresh_tokens / oauth_states back token rotation+revocation and a
  single-use, session-bound Instagram OAuth flow.

Revision ID: b8c9d0e1f2a3
Revises: f5a6b7c8d9e0
Create Date: 2026-09-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_SEARCH_VECTOR = (
    "to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(description, '') "
    "|| ' ' || coalesce(attributes::text, ''))"
)
# Must stay identical to app/products/models.py:_SEARCH_VECTOR_EXPR.
_NEW_SEARCH_VECTOR = (
    "to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(description, '')) "
    "|| jsonb_to_tsvector('simple', coalesce(attributes, '{}'::jsonb) - 'image_url' - 'ai_instructions', "
    "'[\"string\", \"numeric\"]')"
)


def _refuse_if_duplicates(sql: str, what: str) -> None:
    count = op.get_bind().execute(sa.text(sql)).scalar()
    if count:
        raise RuntimeError(
            f"{count} duplicate {what} found. Resolve them by hand before upgrading — "
            "which row to keep is a business decision this migration will not guess."
        )


def upgrade() -> None:
    # --- product_variants: columns the model has used for weeks -------------
    op.execute("ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS attributes jsonb")
    op.execute("UPDATE product_variants SET attributes = '{}'::jsonb WHERE attributes IS NULL")
    op.execute(
        "ALTER TABLE product_variants ALTER COLUMN attributes SET DEFAULT '{}'::jsonb, "
        "ALTER COLUMN attributes SET NOT NULL"
    )
    op.execute("ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS image_url text")
    op.execute("ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS images jsonb")
    op.execute("UPDATE product_variants SET images = '[]'::jsonb WHERE images IS NULL")
    op.execute(
        "ALTER TABLE product_variants ALTER COLUMN images SET DEFAULT '[]'::jsonb, "
        "ALTER COLUMN images SET NOT NULL"
    )
    op.execute("ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS barcode varchar(100)")
    op.execute("ALTER TABLE product_images ALTER COLUMN url TYPE text")

    # --- money / stock sanity ----------------------------------------------
    op.create_check_constraint("ck_products_price_non_negative", "products", "price IS NULL OR price >= 0")
    op.create_check_constraint(
        "ck_product_variants_stock_non_negative", "product_variants", "stock_quantity IS NULL OR stock_quantity >= 0"
    )
    op.create_check_constraint(
        "ck_product_variants_price_non_negative", "product_variants", "price_override IS NULL OR price_override >= 0"
    )
    op.create_check_constraint("ck_discounts_type", "discounts", "discount_type IN ('percentage', 'fixed')")
    op.create_check_constraint(
        "ck_discounts_value", "discounts", "value >= 0 AND (discount_type <> 'percentage' OR value <= 100)"
    )

    # --- search_vector: attribute values only ------------------------------
    op.execute("DROP INDEX IF EXISTS ix_products_search_vector")
    op.execute("ALTER TABLE products DROP COLUMN search_vector")
    op.execute(f"ALTER TABLE products ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ({_NEW_SEARCH_VECTOR}) STORED")
    op.execute("CREATE INDEX ix_products_search_vector ON products USING gin (search_vector)")

    # --- conversations ------------------------------------------------------
    _refuse_if_duplicates(
        "SELECT count(*) FROM (SELECT 1 FROM conversations GROUP BY business_id, customer_id HAVING count(*) > 1) d",
        "conversations per (business, customer)",
    )
    op.create_unique_constraint(
        "uq_conversations_business_id_customer_id", "conversations", ["business_id", "customer_id"]
    )
    op.add_column("conversations", sa.Column("last_answered_customer_message_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations", sa.Column("follow_up_sent_at", sa.DateTime(timezone=True), nullable=True))

    # --- instagram_accounts: one business per IG account --------------------
    _refuse_if_duplicates(
        "SELECT count(*) FROM (SELECT 1 FROM instagram_accounts GROUP BY ig_business_id HAVING count(*) > 1) d",
        "Instagram accounts linked to more than one business",
    )
    op.create_unique_constraint("uq_instagram_accounts_ig_business_id", "instagram_accounts", ["ig_business_id"])

    # --- messages: outbox ----------------------------------------------------
    op.add_column("messages", sa.Column("delivery_status", sa.String(length=10), nullable=True))
    op.add_column("messages", sa.Column("delivery_error", sa.Text(), nullable=True))
    # Existing outbound rows: whether the ones without an Instagram id ever
    # reached anyone is unknowable now, and resending week-old replies would
    # be far worse than leaving them — so they're "unknown", which the resend
    # path never touches.
    op.execute(
        "UPDATE messages SET delivery_status = CASE WHEN external_message_id IS NOT NULL THEN 'sent' ELSE 'unknown' END "
        "WHERE sender_type IN ('ai', 'human')"
    )
    op.create_index("ix_messages_conversation_id_created_at", "messages", ["conversation_id", "created_at"])

    # --- customers ------------------------------------------------------------
    # `name` is used by the model and the dashboard but was never migrated
    # (production has it, added by hand) — IF NOT EXISTS for the same reason
    # as the product_variants columns above.
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS name varchar(255)")
    # Don't hammer Meta for profiles that can't be fetched.
    op.add_column("customers", sa.Column("profile_fetched_at", sa.DateTime(timezone=True), nullable=True))

    # --- leads survive their conversation being deleted ---------------------
    op.alter_column("leads", "conversation_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.drop_constraint("leads_conversation_id_fkey", "leads", type_="foreignkey")
    op.create_foreign_key(
        "leads_conversation_id_fkey", "leads", "conversations", ["conversation_id"], ["id"], ondelete="SET NULL"
    )

    # --- businesses: platform kill switch the owner can't override ---------
    op.add_column(
        "businesses", sa.Column("ai_suspended", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.execute("UPDATE businesses SET ai_suspended = true WHERE deleted_at IS NOT NULL")

    # --- webhook_events: lease + retry bookkeeping --------------------------
    op.add_column("webhook_events", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webhook_events", sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("webhook_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webhook_events", sa.Column("last_error", sa.Text(), nullable=True))
    # Rows from the old inline pipeline carry a payload shape the new worker
    # doesn't read, and they're days old — re-driving them would answer
    # customers about messages they sent last week.
    op.execute("UPDATE webhook_events SET status = 'abandoned' WHERE status IN ('received', 'processing', 'failed')")
    op.create_index("ix_webhook_events_status_next_attempt_at", "webhook_events", ["status", "next_attempt_at"])

    # --- refresh tokens (rotation + revocation) ----------------------------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    # --- Instagram OAuth: single-use state, completed by the owner's session -
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completion_id", sa.String(length=64), nullable=True),
        sa.Column("code_encrypted", sa.Text(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("completion_id", name="uq_oauth_states_completion_id"),
    )


def downgrade() -> None:
    op.drop_table("oauth_states")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index("ix_webhook_events_status_next_attempt_at", table_name="webhook_events")
    op.drop_column("webhook_events", "last_error")
    op.drop_column("webhook_events", "next_attempt_at")
    op.drop_column("webhook_events", "attempts")
    op.drop_column("webhook_events", "claimed_at")

    op.drop_column("businesses", "ai_suspended")

    op.drop_constraint("leads_conversation_id_fkey", "leads", type_="foreignkey")
    op.create_foreign_key(
        "leads_conversation_id_fkey", "leads", "conversations", ["conversation_id"], ["id"], ondelete="CASCADE"
    )
    op.execute("DELETE FROM leads WHERE conversation_id IS NULL")
    op.alter_column("leads", "conversation_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)

    op.drop_column("customers", "profile_fetched_at")

    op.drop_index("ix_messages_conversation_id_created_at", table_name="messages")
    op.drop_column("messages", "delivery_error")
    op.drop_column("messages", "delivery_status")

    op.drop_constraint("uq_instagram_accounts_ig_business_id", "instagram_accounts", type_="unique")

    op.drop_column("conversations", "follow_up_sent_at")
    op.drop_column("conversations", "last_answered_customer_message_at")
    op.drop_constraint("uq_conversations_business_id_customer_id", "conversations", type_="unique")

    op.execute("DROP INDEX IF EXISTS ix_products_search_vector")
    op.execute("ALTER TABLE products DROP COLUMN search_vector")
    op.execute(f"ALTER TABLE products ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ({_OLD_SEARCH_VECTOR}) STORED")
    op.execute("CREATE INDEX ix_products_search_vector ON products USING gin (search_vector)")

    op.drop_constraint("ck_discounts_value", "discounts", type_="check")
    op.drop_constraint("ck_discounts_type", "discounts", type_="check")
    op.drop_constraint("ck_product_variants_price_non_negative", "product_variants", type_="check")
    op.drop_constraint("ck_product_variants_stock_non_negative", "product_variants", type_="check")
    op.drop_constraint("ck_products_price_non_negative", "products", type_="check")
    # product_variants columns stay: production had them before this revision.
