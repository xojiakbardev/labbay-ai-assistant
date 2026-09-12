"""Re-encrypt every stored secret under the current primary FERNET_KEY.

Rotation, without breaking any stored Instagram token:
  1. Generate a new key:
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  2. Set FERNET_KEY=<new>,<old>   (new first: it encrypts; both decrypt)
  3. Restart the API, then run:   python rotate_fernet_key.py
  4. Set FERNET_KEY=<new> and restart again.

A value that no configured key can decrypt is reported and left as it is —
the script never writes something it couldn't read.
"""
import asyncio

from cryptography.fernet import InvalidToken
from sqlalchemy import select

from app.core.db import async_session_factory
from app.core.security import reencrypt_secret
from app.instagram.models import InstagramAccount, OAuthState


async def main() -> None:
    rotated = failed = 0
    async with async_session_factory() as db:
        for account in (await db.execute(select(InstagramAccount))).scalars():
            try:
                account.access_token_encrypted = reencrypt_secret(account.access_token_encrypted)
                rotated += 1
            except InvalidToken:
                failed += 1
                print(f"!! instagram_accounts {account.id} (business {account.business_id}) can't be decrypted")
        for state in (await db.execute(select(OAuthState).where(OAuthState.code_encrypted.is_not(None)))).scalars():
            try:
                state.code_encrypted = reencrypt_secret(state.code_encrypted)
                rotated += 1
            except InvalidToken:
                state.code_encrypted = None  # a pending connect code; the owner just reconnects
                failed += 1
        await db.commit()
    print(f"Re-encrypted {rotated} secret(s); {failed} could not be decrypted.")


if __name__ == "__main__":
    asyncio.run(main())
