"""Password hashing, JWT issuance/verification, and secret-at-rest encryption."""
import datetime as dt
import uuid

import bcrypt
import jwt
from cryptography.fernet import Fernet

from app.core.config import get_settings

settings = get_settings()
_fernet = Fernet(settings.fernet_key.encode())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _create_token(subject: str, expires_delta: dt.timedelta, token_type: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str) -> str:
    return _create_token(
        user_id, dt.timedelta(minutes=settings.access_token_expire_minutes), "access"
    )


def create_refresh_token(user_id: str) -> str:
    return _create_token(
        user_id, dt.timedelta(days=settings.refresh_token_expire_days), "refresh"
    )


def decode_token(token: str) -> dict:
    """Raises jwt.PyJWTError on invalid/expired token — caller maps to 401."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def create_oauth_state_token(business_id: str) -> str:
    """Signed, short-lived `state` param for the Instagram OAuth redirect — Meta's
    callback is an unauthenticated browser redirect, so business identity has to
    travel in a tamper-proof state token rather than a bearer header."""
    return _create_token(business_id, dt.timedelta(minutes=10), "oauth_state")


def decode_oauth_state_token(token: str) -> str:
    """Returns the business_id. Raises jwt.PyJWTError on invalid/expired/tampered
    token, or ValueError if it's a token of the wrong type."""
    payload = decode_token(token)
    if payload.get("type") != "oauth_state":
        raise ValueError("Invalid state token type.")
    return payload["sub"]


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret (e.g. Instagram access token) for storage at rest."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
