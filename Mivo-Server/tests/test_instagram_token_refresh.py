"""Keeps a connected Instagram account alive indefinitely: Meta caps a
long-lived token at 60 days, but refreshing it before it expires resets that
clock — app/instagram/service.py:refresh_expiring_tokens, run daily from
app/main.py's lifespan."""
import datetime as dt
import uuid

import pytest

from app.auth.models import User
from app.businesses.models import Business
from app.core.security import decrypt_secret, encrypt_secret
from app.instagram.client import RefreshedToken
from app.instagram.models import InstagramAccount
from app.instagram.service import refresh_expiring_tokens

pytestmark = pytest.mark.asyncio


class FakeMetaClient:
    def __init__(self, *, fail_for: set[str] | None = None):
        self.calls: list[str] = []
        self._fail_for = fail_for or set()

    async def refresh_long_lived_token(self, access_token: str) -> RefreshedToken:
        self.calls.append(access_token)
        if access_token in self._fail_for:
            raise RuntimeError("simulated Meta API failure")
        return RefreshedToken(
            access_token=f"refreshed-{access_token}",
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=60),
        )


async def _seed_account(
    db_session, *, token: str, expires_in: dt.timedelta | None, connected_ago: dt.timedelta
) -> InstagramAccount:
    now = dt.datetime.now(dt.timezone.utc)
    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name=f"Biz-{token}")
    db_session.add(business)
    await db_session.flush()

    account = InstagramAccount(
        business_id=business.id,
        ig_business_id=f"ig-{token}",
        ig_username=token,
        fb_page_id="page-1",
        access_token_encrypted=encrypt_secret(token),
        token_expires_at=(now + expires_in) if expires_in is not None else None,
        status="connected",
        connected_at=now - connected_ago,
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


async def test_refreshes_tokens_inside_the_window(db_session) -> None:
    account = await _seed_account(
        db_session, token="due", expires_in=dt.timedelta(days=5), connected_ago=dt.timedelta(hours=48)
    )
    meta = FakeMetaClient()

    count = await refresh_expiring_tokens(db_session, meta)

    assert count == 1
    assert meta.calls == ["due"]
    await db_session.refresh(account)
    assert decrypt_secret(account.access_token_encrypted) == "refreshed-due"
    assert account.token_expires_at > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=55)


async def test_skips_tokens_not_close_to_expiry(db_session) -> None:
    await _seed_account(
        db_session, token="fresh", expires_in=dt.timedelta(days=40), connected_ago=dt.timedelta(hours=48)
    )
    meta = FakeMetaClient()

    count = await refresh_expiring_tokens(db_session, meta)

    assert count == 0
    assert meta.calls == []


async def test_skips_tokens_younger_than_24h(db_session) -> None:
    """Meta rejects refreshing a token that isn't at least 24h old yet."""
    await _seed_account(
        db_session, token="new", expires_in=dt.timedelta(days=5), connected_ago=dt.timedelta(hours=2)
    )
    meta = FakeMetaClient()

    count = await refresh_expiring_tokens(db_session, meta)

    assert count == 0
    assert meta.calls == []


async def test_skips_already_expired_tokens(db_session) -> None:
    """Meta can't refresh a token that has already lapsed — that account
    needs a real reconnect (manual "Qayta ulash"), not a silent refresh."""
    await _seed_account(
        db_session, token="dead", expires_in=-dt.timedelta(days=1), connected_ago=dt.timedelta(days=70)
    )
    meta = FakeMetaClient()

    count = await refresh_expiring_tokens(db_session, meta)

    assert count == 0
    assert meta.calls == []


async def test_one_failure_does_not_stop_the_sweep(db_session) -> None:
    await _seed_account(
        db_session, token="broken", expires_in=dt.timedelta(days=3), connected_ago=dt.timedelta(hours=48)
    )
    ok_account = await _seed_account(
        db_session, token="healthy", expires_in=dt.timedelta(days=4), connected_ago=dt.timedelta(hours=48)
    )
    meta = FakeMetaClient(fail_for={"broken"})

    count = await refresh_expiring_tokens(db_session, meta)

    assert count == 1
    assert set(meta.calls) == {"broken", "healthy"}
    await db_session.refresh(ok_account)
    assert decrypt_secret(ok_account.access_token_encrypted) == "refreshed-healthy"
