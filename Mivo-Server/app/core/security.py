"""Password hashing, JWT issuance/verification, and secret-at-rest encryption."""
import asyncio
import datetime as dt
import uuid

import bcrypt
import jwt
from cryptography.fernet import Fernet, MultiFernet

from app.core.config import get_settings

settings = get_settings()
# First key encrypts, all keys decrypt — see Settings.fernet_key for rotation.
_fernet = MultiFernet([Fernet(k.encode()) for k in settings.fernet_keys])

TOKEN_ACCESS = "access"
TOKEN_REFRESH = "refresh"
TOKEN_SSE = "sse"

# Hashed once at import. Verifying a login for an email that doesn't exist
# runs bcrypt against this, so "no such user" costs the same time as "wrong
# password" and response timing can't be used to enumerate accounts.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt()).decode()

SSE_TICKET_TTL = dt.timedelta(seconds=60)


# bcrypt only uses the first 72 bytes and bcrypt>=5 raises on longer input.
MAX_PASSWORD_BYTES = 72

# bcrypt is ~250ms of CPU per call. Run on the event loop it stalls every
# other request (webhooks, SSE) for that long, so it runs in worker threads —
# at most this many at once, so a login flood queues instead of eating the CPU.
_BCRYPT_SLOTS = asyncio.Semaphore(4)


def password_too_long(password: str) -> bool:
    return len(password.encode()) > MAX_PASSWORD_BYTES


def hash_password(password: str) -> str:
    if password_too_long(password):
        raise ValueError(f"password is longer than {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _check(password: str, password_hash: str | None) -> bool:
    if password_hash is None or password_too_long(password):
        # Same work as a real check, so timing doesn't tell a missing account
        # (or an over-long password) apart from a wrong password.
        bcrypt.checkpw(b"x", _DUMMY_PASSWORD_HASH.encode())
        return False
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def verify_password(password: str, password_hash: str | None) -> bool:
    """Synchronous check (scripts, tests). Request handlers use
    verify_password_async."""
    return _check(password, password_hash)


async def verify_password_async(password: str, password_hash: str | None) -> bool:
    """Constant-work check off the event loop: with no hash (unknown user) it
    still runs bcrypt against a dummy hash, then returns False."""
    async with _BCRYPT_SLOTS:
        return await asyncio.to_thread(_check, password, password_hash)


def _create_token(subject: str, expires_delta: dt.timedelta, token_type: str, jti: uuid.UUID | None = None) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(jti or uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str) -> str:
    return _create_token(
        user_id, dt.timedelta(minutes=settings.access_token_expire_minutes), TOKEN_ACCESS
    )


def create_refresh_token(user_id: str) -> tuple[str, uuid.UUID, dt.datetime]:
    """Returns (token, jti, expires_at) — the caller persists jti/expires_at so
    the token can be rotated and revoked (app/auth/service.py)."""
    jti = uuid.uuid4()
    lifetime = dt.timedelta(days=settings.refresh_token_expire_days)
    token = _create_token(user_id, lifetime, TOKEN_REFRESH, jti=jti)
    return token, jti, dt.datetime.now(dt.timezone.utc) + lifetime


def create_sse_ticket(user_id: str) -> str:
    """A one-minute token good only for opening the notification stream.

    EventSource can't send headers, so whatever authenticates the stream ends
    up in a URL — and URLs end up in access logs. Putting the 30-minute access
    token there leaked a live credential; this ticket is useless for anything
    else and dead within a minute.
    """
    return _create_token(user_id, SSE_TICKET_TTL, TOKEN_SSE)


def decode_token(token: str, expected_type: str) -> dict:
    """Raises jwt.PyJWTError on an invalid/expired/tampered token or one of the
    wrong type — callers map any of that to 401."""
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "iat", "sub", "type", "jti"]},
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"expected a {expected_type} token")
    return payload


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret (e.g. Instagram access token) for storage at rest."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Raises cryptography.fernet.InvalidToken if no configured key can
    decrypt it — never returns a placeholder."""
    return _fernet.decrypt(ciphertext.encode()).decode()


def reencrypt_secret(ciphertext: str) -> str:
    """Re-encrypts under the current primary key (key rotation)."""
    return _fernet.rotate(ciphertext.encode()).decode()
