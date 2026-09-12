import uuid

from sqlalchemy import Boolean, CheckConstraint, Computed, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship
from pgvector.sqlalchemy import Vector

# Attribute *values* only, and never the internal keys: indexing
# `attributes::text` made JSON keys ("color", "fit") and fields like image_url
# searchable words, so a query mentioning "color" matched every product that
# merely had a colour attribute. Must match the b8c9d0e1f2a3 migration.
_SEARCH_VECTOR_EXPR = (
    "to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(description, '')) "
    "|| jsonb_to_tsvector('simple', coalesce(attributes, '{}'::jsonb) - 'image_url' - 'ai_instructions', "
    "'[\"string\", \"numeric\"]')"
)

from app.common.mixins import TimestampMixin, UUIDPk
from app.core.config import get_settings
from app.core.db import Base


# Pinned at import time rather than read per-query: the column width is part of
# the schema, so it must match whatever the migration created, not whatever the
# environment happens to say today.
EMBEDDING_DIMENSIONS = get_settings().embedding_dimensions


class Product(Base, UUIDPk, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_business_id_availability", "business_id", "availability"),
        Index("ix_products_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_products_search_normalized_trgm",
            "search_normalized",
            postgresql_using="gin",
            postgresql_ops={"search_normalized": "gin_trgm_ops"},
        ),
        # Declared so `alembic revision --autogenerate` doesn't propose
        # dropping indexes the migrations created by hand.
        Index("ix_products_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        Index(
            "ix_products_description_trgm",
            "description",
            postgresql_using="gin",
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
        Index(
            "ix_products_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("embedding IS NOT NULL"),
        ),
        CheckConstraint("price IS NULL OR price >= 0", name="ck_products_price_non_negative"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="UZS", nullable=False)
    availability: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)  # manual | ai_import

    # Normalized text for fuzzy cross-alphabet (Cyrillic <-> Latin) & typo matching
    search_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Semantic search vector. Null is normal and safe: a business without
    # embeddings configured, or a product written while the embedding API was
    # down, simply doesn't take part in the semantic tier (app/products/search.py).
    # Width is pinned by EMBEDDING_DIMENSIONS — changing it needs a migration
    # and a full re-embed, which is what embedding_hash detects.
    # Deferred: 1536 floats per row, and nothing but the embedding code reads
    # it — every search, list and discovery query would otherwise haul it along.
    embedding: Mapped[list[float] | None] = deferred(
        mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=True)
    )
    embedding_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # GENERATED ALWAYS ... STORED (DDL owned by the Alembic migration — this
    # Computed() mirror just tells the ORM to never include it in INSERT/UPDATE,
    # since Postgres rejects any explicit value for a generated column).
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(_SEARCH_VECTOR_EXPR, persisted=True), nullable=True
    )

    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    images: Mapped[list["ProductImage"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductVariant(Base, UUIDPk):
    __tablename__ = "product_variants"
    __table_args__ = (
        CheckConstraint("stock_quantity IS NULL OR stock_quantity >= 0", name="ck_product_variants_stock_non_negative"),
        CheckConstraint("price_override IS NULL OR price_override >= 0", name="ck_product_variants_price_non_negative"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant_type: Mapped[str] = mapped_column(String(50), nullable=False, default="combination")  # combination | size | color | ...
    value: Mapped[str] = mapped_column(String(100), nullable=False)
    attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price_override: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    stock_quantity: Mapped[int | None] = mapped_column(nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    images: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True)
    availability: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="variants")


class ProductImage(Base, UUIDPk):
    __tablename__ = "product_images"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    r2_key: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="images")
