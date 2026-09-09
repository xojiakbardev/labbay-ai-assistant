import uuid

from sqlalchemy import Boolean, Computed, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

_SEARCH_VECTOR_EXPR = (
    "to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(description, '') "
    "|| ' ' || coalesce(attributes::text, ''))"
)

from app.common.mixins import TimestampMixin, UUIDPk
from app.core.db import Base


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

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant_type: Mapped[str] = mapped_column(String(50), nullable=False, default="combination")  # combination | size | color | ...
    value: Mapped[str] = mapped_column(String(100), nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price_override: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    stock_quantity: Mapped[int | None] = mapped_column(nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    images: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
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
