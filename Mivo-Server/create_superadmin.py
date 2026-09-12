"""One-time bootstrap: create the first superadmin account.

There's no public signup and no superadmin can create another superadmin
through the API (by design — /superadmin/businesses only ever creates regular
business owners), so the very first superadmin has to be inserted directly.
Every superadmin after this one can just be promoted via SQL
(`UPDATE users SET is_superadmin = true WHERE email = '...'`) — or you can
reuse this script's logic.

Usage:
    .venv/bin/python create_superadmin.py owner@yourcompany.com
    (prompts for a password; or pass it as a second arg for non-interactive use)
"""
import asyncio
import getpass
import sys

from sqlalchemy import func, select

from app.auth.models import User
from app.core.db import async_session_factory
from app.core.security import hash_password


async def main(email: str, password: str) -> None:
    email = email.strip().lower()  # logins match case-insensitively
    async with async_session_factory() as db:
        existing = await db.scalar(select(User).where(func.lower(User.email) == email))
        if existing is not None:
            existing.is_superadmin = True
            existing.password_hash = hash_password(password)
            await db.commit()
            print(f"Updated existing user {email} -> superadmin.")
            return

        user = User(email=email, password_hash=hash_password(password), is_superadmin=True)
        db.add(user)
        await db.commit()
        print(f"Created superadmin {email}.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python create_superadmin.py <email> [password]")
        sys.exit(1)
    email_arg = sys.argv[1]
    password_arg = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass("Password (min 12 chars): ")
    if len(password_arg) < 12:
        print("Password must be at least 12 characters — this account controls every business.")
        sys.exit(1)
    asyncio.run(main(email_arg, password_arg))
