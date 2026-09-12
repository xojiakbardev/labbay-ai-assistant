"""Business-level rules shared by every path that makes the AI speak."""
import datetime as dt

from app.businesses.models import Business


def ai_may_reply(business: Business, now: dt.datetime | None = None) -> bool:
    """Whether this business may have AI replies sent on its behalf at all —
    the one definition used by the webhook pipeline, follow-ups and anything
    else that messages customers automatically.

    Needs: the owner's switch on, the platform's kill switch off, not
    soft-deleted, and a subscription that hasn't lapsed.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    if not business.ai_enabled or business.ai_suspended or business.deleted_at is not None:
        return False
    expires = business.subscription_expires_at
    return expires is None or expires > now
