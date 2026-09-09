"""Phase 2 acceptance test: the full baseline schema exists with the expected
constraints/indexes that the rest of the app relies on (plan §5, §19)."""
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

EXPECTED_TABLES = {
    "users",
    "businesses",
    "instagram_accounts",
    "telegram_connections",
    "products",
    "product_variants",
    "product_images",
    "customers",
    "conversations",
    "messages",
    "leads",
    "webhook_events",
}


async def test_all_tables_exist(db_session) -> None:
    result = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    tables = {row[0] for row in result.fetchall()}
    assert EXPECTED_TABLES <= tables


async def test_search_vector_is_generated_and_indexed(db_session) -> None:
    business_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"
        ),
        {"id": user_id, "email": f"{user_id}@test.com"},
    )
    await db_session.execute(
        text(
            "INSERT INTO businesses (id, owner_user_id, name, ai_enabled) "
            "VALUES (:id, :owner_id, 'Test Biz', true)"
        ),
        {"id": business_id, "owner_id": user_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO products (id, business_id, name, description, currency, "
            "availability, attributes, source) VALUES "
            "(:id, :business_id, 'Nike Hoodie', 'Issiq oversize hoodie', 'UZS', "
            "true, '{}'::jsonb, 'manual')"
        ),
        {"id": uuid.uuid4(), "business_id": business_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text(
            "SELECT name FROM products WHERE search_vector @@ plainto_tsquery('simple', 'hoodie')"
        )
    )
    assert [row[0] for row in result.fetchall()] == ["Nike Hoodie"]


async def test_webhook_event_external_id_is_unique(db_session) -> None:
    await db_session.execute(
        text(
            "INSERT INTO webhook_events (id, provider, external_event_id, payload, "
            "status, created_at) VALUES (:id, 'instagram', 'evt-1', '{}'::jsonb, "
            "'received', now())"
        ),
        {"id": uuid.uuid4()},
    )
    await db_session.commit()

    with pytest.raises(Exception):
        await db_session.execute(
            text(
                "INSERT INTO webhook_events (id, provider, external_event_id, payload, "
                "status, created_at) VALUES (:id, 'instagram', 'evt-1', '{}'::jsonb, "
                "'received', now())"
            ),
            {"id": uuid.uuid4()},
        )
        await db_session.commit()
    await db_session.rollback()
