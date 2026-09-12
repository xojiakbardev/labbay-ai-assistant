"""Re-encrypt stored secrets under the primary FERNET_KEY.

  1. FERNET_KEY=<new>,<old>  2. python rotate_fernet_key.py  3. FERNET_KEY=<new>"""
import asyncio

from cryptography.fernet import InvalidToken
from sqlalchemy import select

import app.core.models_registry  # noqa: F401
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
