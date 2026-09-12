"""Which business an AI call is billed to, for calls made deep inside tools."""
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
