"""Which business the current work is billed to.

Some paid calls (embeddings) happen deep inside code that has no business in
its signature — a search tool, a product write. Callers that know the business
set it here for the duration of the work, and the provider logs usage against
it, so the superadmin cost figures include every paid call, not just chat.
"""
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_billed_business: ContextVar[uuid.UUID | None] = ContextVar("billed_business", default=None)


def billed_business() -> uuid.UUID | None:
    return _billed_business.get()


@contextmanager
def billing_to(business_id: uuid.UUID | None):
    token = _billed_business.set(business_id)
    try:
        yield
    finally:
        _billed_business.reset(token)
