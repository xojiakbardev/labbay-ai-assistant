"""Instagram account lifecycle: connect (OAuth), disconnect, token refresh.

Inbound message processing lives in app/instagram/pipeline.py. All
Meta-specific logic stays inside app/instagram/; other modules only ever see
the neutral conversations/messages domain model.
"""
import datetime as dt
import logging
import secrets
import uuid

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret, encrypt_secret
from app.instagram.client import ConnectedAccount, MetaAPIError, MetaClient
from app.instagram.models import InstagramAccount, OAuthState

logger = logging.getLogger("app.instagram.service")

OAUTH_STATE_TTL = dt.timedelta(minutes=15)


class OAuthFlowError(Exception):
    """The connect attempt can't be completed; the message is user-facing."""


class AccountLinkedElsewhere(OAuthFlowError):
    pass


async def start_oauth(db: AsyncSession, business_id: uuid.UUID) -> str:
    """Records a connect attempt and returns its id, which travels as the
    OAuth `state`."""
    state = OAuthState(
        id=uuid.uuid4(),
        business_id=business_id,
        expires_at=dt.datetime.now(dt.timezone.utc) + OAUTH_STATE_TTL,
    )
    db.add(state)
    await db.commit()
    return str(state.id)


async def park_oauth_code(db: AsyncSession, state: str, code: str) -> str:
    """Meta's callback (an unauthenticated browser redirect). Stores the code
    against the attempt and returns a fresh completion id for the browser to
    carry back to the dashboard. Nothing is connected yet: that needs the
    business's own logged-in session (complete_oauth)."""
    try:
        state_id = uuid.UUID(state)
    except ValueError as exc:
        raise OAuthFlowError("invalid_state") from exc

    now = dt.datetime.now(dt.timezone.utc)
    row = await db.scalar(select(OAuthState).where(OAuthState.id == state_id).with_for_update())
    if row is None or row.expires_at <= now or row.used_at is not None or row.completion_id is not None:
        raise OAuthFlowError("invalid_state")

    row.completion_id = secrets.token_urlsafe(32)
    row.code_encrypted = encrypt_secret(code)
    await db.commit()
    return row.completion_id


async def complete_oauth(
    db: AsyncSession, business_id: uuid.UUID, completion_id: str, meta_client: MetaClient
) -> InstagramAccount:
    """Called by the dashboard with the completion id from its own URL. Only
    the business that started the attempt can complete it, and only once."""
    now = dt.datetime.now(dt.timezone.utc)
    row = await db.scalar(
        select(OAuthState).where(OAuthState.completion_id == completion_id).with_for_update()
    )
    if (
        row is None
        or row.business_id != business_id
        or row.used_at is not None
        or row.expires_at <= now
        or row.code_encrypted is None
    ):
        raise OAuthFlowError("Ulanish havolasi eskirgan yoki noto'g'ri. Qaytadan ulang.")

    code = decrypt_secret(row.code_encrypted)
    row.used_at = now
    row.code_encrypted = None
    await db.commit()

    try:
        account = await meta_client.exchange_code_for_account(code)
    except MetaAPIError as exc:
        logger.warning("[instagram] code exchange failed for business %s: %s", business_id, exc)
        raise OAuthFlowError("Instagram ulanmadi. Qaytadan urinib ko'ring.") from exc
    return await connect_account(db, business_id, account)


async def connect_account(
    db: AsyncSession, business_id: uuid.UUID, account: ConnectedAccount
) -> InstagramAccount:
    other = await db.scalar(
        select(InstagramAccount).where(
            InstagramAccount.ig_business_id == account.ig_business_id,
            InstagramAccount.business_id != business_id,
        )
    )
    if other is not None:
        raise AccountLinkedElsewhere(
            "Bu Instagram akkaunt boshqa biznesga ulangan. Avval u yerdan uzing."
        )

    existing = await db.scalar(
        select(InstagramAccount).where(InstagramAccount.business_id == business_id)
    )
    encrypted = encrypt_secret(account.access_token)
    now = dt.datetime.now(dt.timezone.utc)
    if existing is None:
        existing = InstagramAccount(business_id=business_id)
        db.add(existing)
    existing.ig_business_id = account.ig_business_id
    existing.ig_username = account.ig_username
    existing.fb_page_id = account.fb_page_id
    existing.access_token_encrypted = encrypted
    existing.token_expires_at = account.expires_at
    existing.status = "connected"
    existing.connected_at = now
    await db.commit()
    await db.refresh(existing)
    return existing


async def get_account(db: AsyncSession, business_id: uuid.UUID) -> InstagramAccount | None:
    return await db.scalar(
        select(InstagramAccount).where(InstagramAccount.business_id == business_id)
    )


async def disconnect_account(db: AsyncSession, business_id: uuid.UUID, meta_client: MetaClient) -> bool:
    """Removes the account link. Returns whether Meta also confirmed the app's
    permissions were revoked.

    The local link always goes: the owner asked to disconnect, and a token
    that Meta won't accept any more (expired, already revoked) must not keep
    them connected. If Meta keeps delivering webhooks for the account, they're
    dropped — routing is exact, so they can't land in anyone's inbox.
    """
    account = await get_account(db, business_id)
    if account is None:
        return True

    revoked = False
    try:
        await meta_client.deauthorize_account(decrypt_secret(account.access_token_encrypted))
        revoked = True
    except InvalidToken:
        logger.error("[instagram] stored token for business %s can't be decrypted with any FERNET_KEY", business_id)
    except MetaAPIError as exc:
        logger.warning("[instagram] Meta did not confirm deauthorization for business %s: %s", business_id, exc)

    await db.delete(account)
    await db.commit()
    return revoked


async def backfill_customer_profiles(db: AsyncSession, meta_client: MetaClient, limit: int = 20) -> int:
    """Fills in @username / name for customers that don't have them yet, a
    few at a time on a schedule — the dashboard used to do this on every page
    load, serially, re-asking Meta about profiles it had already refused.
    Each attempt is recorded (profile_fetched_at), successful or not, so a
    refused profile is retried at most daily. Returns how many customers were
    attempted (0 = nothing left to do right now)."""
    from sqlalchemy import or_

    from app.customers.models import Customer
    from app.customers.service import PROFILE_RETRY_AFTER

    now = dt.datetime.now(dt.timezone.utc)
    rows = (
        await db.execute(
            select(Customer, InstagramAccount)
            .join(InstagramAccount, InstagramAccount.business_id == Customer.business_id)
            .where(
                InstagramAccount.status == "connected",
                or_(Customer.username.is_(None), Customer.name.is_(None)),
                or_(Customer.profile_fetched_at.is_(None), Customer.profile_fetched_at < now - PROFILE_RETRY_AFTER),
                ~Customer.ig_scoped_id.startswith("sandbox_"),
            )
            .order_by(Customer.last_seen_at.desc())
            .limit(limit)
        )
    ).all()

    filled = 0
    for customer, account in rows:
        try:
            profile = await meta_client.get_user_profile(
                customer.ig_scoped_id, decrypt_secret(account.access_token_encrypted)
            )
        except (MetaAPIError, InvalidToken) as exc:
            logger.info("[instagram] profile for customer %s unavailable: %s", customer.id, exc)
        else:
            if profile.get("username"):
                customer.username = str(profile["username"]).lstrip("@")[:255]
            if profile.get("name"):
                customer.name = str(profile["name"])[:255]
            filled += 1
        customer.profile_fetched_at = now
        await db.commit()
    if rows:
        logger.info("[instagram] profile backfill: %d of %d filled", filled, len(rows))
    return len(rows)


REFRESH_WINDOW = dt.timedelta(days=10)
MIN_TOKEN_AGE = dt.timedelta(hours=24)


async def refresh_expiring_tokens(db: AsyncSession, meta_client: MetaClient) -> int:
    """Keeps connected Instagram accounts alive indefinitely without the
    owner ever clicking "Qayta ulash" again: Meta's long-lived tokens are
    hard-capped at 60 days, but refreshing one (while it's still valid)
    resets that clock to another 60 — see app/instagram/client.py. Runs on a
    daily schedule (app/main.py); returns the number refreshed.

    Only touches tokens inside REFRESH_WINDOW of expiring. Each account is
    independent: one that fails is logged and the rest still refresh.
    """
    now = dt.datetime.now(dt.timezone.utc)
    candidates = (
        await db.execute(
            select(InstagramAccount).where(
                InstagramAccount.status == "connected",
                InstagramAccount.token_expires_at.is_not(None),
                InstagramAccount.token_expires_at > now,
                InstagramAccount.token_expires_at <= now + REFRESH_WINDOW,
                InstagramAccount.connected_at <= now - MIN_TOKEN_AGE,
            )
        )
    ).scalars().all()

    refreshed = 0
    for account in candidates:
        try:
            current_token = decrypt_secret(account.access_token_encrypted)
        except InvalidToken:
            logger.error("[instagram] token for business %s can't be decrypted — owner must reconnect", account.business_id)
            continue
        try:
            result = await meta_client.refresh_long_lived_token(current_token)
        except MetaAPIError as exc:
            logger.warning("[instagram] token refresh failed for business %s: %s", account.business_id, exc)
            continue

        account.access_token_encrypted = encrypt_secret(result.access_token)
        account.token_expires_at = result.expires_at
        await db.commit()
        refreshed += 1

    return refreshed
