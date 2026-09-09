import asyncio
from sqlalchemy import select
from app.core.db import async_session_factory
from app.customers.models import Customer
from app.instagram.models import InstagramAccount
from app.core.security import decrypt_secret
from app.instagram.client import MetaClient

async def sync_all_customers():
    async with async_session_factory() as db:
        customers = (await db.execute(select(Customer))).scalars().all()
        account = await db.scalar(select(InstagramAccount).where(InstagramAccount.status == "connected"))
        if not account:
            print("No connected Instagram account found.")
            return

        access_token = decrypt_secret(account.access_token_encrypted)
        meta_client = MetaClient()

        for c in customers:
            print(f"[Sync] Customer {c.id} - ig_scoped_id: {c.ig_scoped_id}, current username: {c.username}")
            prof = await meta_client.get_user_profile(c.ig_scoped_id, access_token)
            print(f"[Sync] Meta Graph API response for {c.ig_scoped_id}: {prof}")
            if prof and prof.get("username"):
                c.username = prof["username"]
                await db.commit()
                print(f"[Sync] SUCCESS: Updated customer {c.id} username to @{c.username}")
            else:
                print(f"[Sync] Meta Graph API did not return username for scoped_id {c.ig_scoped_id}")

if __name__ == "__main__":
    asyncio.run(sync_all_customers())
