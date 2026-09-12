"""Business-level rules shared by every path that makes the AI speak."""
import datetime as dt

from app.businesses.models import Business


def ai_may_reply(business: Business, now: dt.datetime | None = None) -> bool:
    """Whether automated replies may go out: owner switch on, not suspended, not
    deleted, subscription active."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if not business.ai_enabled or business.ai_suspended or business.deleted_at is not None:
        return False
    expires = business.subscription_expires_at
    return expires is None or expires > now
