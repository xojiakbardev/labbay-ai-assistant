"""Customer upsert keyed on (business_id, ig_scoped_id) — plan §5."""
import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.customers.models import Customer


async def get_or_create_customer(
    db: AsyncSession, business_id: uuid.UUID, ig_scoped_id: str, username: str | None = None
) -> Customer:
    customer = await db.scalar(
        select(Customer).where(
            Customer.business_id == business_id, Customer.ig_scoped_id == ig_scoped_id
        )
    )
    now = dt.datetime.now(dt.timezone.utc)
    if customer is None:
        customer = Customer(
            business_id=business_id,
            ig_scoped_id=ig_scoped_id,
            username=username,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(customer)
        await db.flush()
    else:
        customer.last_seen_at = now
        if username:
            customer.username = username
    return customer
