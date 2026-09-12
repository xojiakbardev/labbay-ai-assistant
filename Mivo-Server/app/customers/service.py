"""Customer upsert keyed on (business_id, ig_scoped_id) — plan §5."""
import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.customers.models import Customer

# A profile Meta couldn't (or wouldn't) return isn't asked for again before this.
PROFILE_RETRY_AFTER = dt.timedelta(hours=get_settings().profile_retry_hours)


async def get_or_create_customer(
    db: AsyncSession, business_id: uuid.UUID, ig_scoped_id: str, username: str | None = None
) -> Customer:
    """Atomic upsert. Two webhooks for a brand-new customer arriving together
    both land on the same row — the second no longer dies on the unique
    constraint (which used to lose that customer's message)."""
    now = dt.datetime.now(dt.timezone.utc)
    stmt = (
        insert(Customer)
        .values(
            id=uuid.uuid4(),
            business_id=business_id,
            ig_scoped_id=ig_scoped_id,
            username=username,
            first_seen_at=now,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_customers_business_id_ig_scoped_id",
            set_={"last_seen_at": now},
        )
        .returning(Customer.id)
    )
    customer_id = (await db.execute(stmt)).scalar_one()
    customer = await db.scalar(
        select(Customer).where(Customer.id == customer_id).execution_options(populate_existing=True)
    )
    if username and customer.username != username:
        customer.username = username
    return customer


def profile_fetch_due(customer: Customer, now: dt.datetime | None = None) -> bool:
    """Whether the Instagram profile should be looked up for this customer."""
    if customer.username and customer.name:
        return False
    if customer.profile_fetched_at is None:
        return True
    now = now or dt.datetime.now(dt.timezone.utc)
    return now - customer.profile_fetched_at >= PROFILE_RETRY_AFTER
