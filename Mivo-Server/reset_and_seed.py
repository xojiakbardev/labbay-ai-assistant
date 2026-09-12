"""DEV ONLY: wipes every table and seeds a demo superadmin, a demo shop and
its catalog.

Refuses to run unless APP_ENV=development AND MIVO_ALLOW_DB_WIPE is set in the
shell AND --yes-wipe-everything is given. The wipe and the seed are one
transaction: if seeding fails, nothing was wiped. Credentials come from the
command line, never from this file.

    MIVO_ALLOW_DB_WIPE=yes-delete-everything python reset_and_seed.py --yes-wipe-everything \
        --admin-email admin@example.com --admin-password '...' \
        --owner-email shop@example.com --owner-password '...'
"""
import argparse
import asyncio
import datetime as dt
import os
import sys
import uuid

from sqlalchemy import select, text

from app.auth.models import User
from app.businesses.models import Business
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.security import hash_password
from app.instagram.models import InstagramAccount
from app.products.models import Product, ProductImage, ProductVariant
from app.products.search_normalize import normalize_for_search

_TABLES = [
    "messages", "conversations", "customers", "leads", "notifications", "push_subscriptions",
    "ai_feedbacks", "ai_usage_logs", "payments", "webhook_events", "telegram_connections",
    "product_images", "product_variants", "products", "discounts", "refresh_tokens", "oauth_states",
    "instagram_accounts", "businesses", "users",
]

_IMG = "https://images.unsplash.com/{}?auto=format&fit=crop&w=800&q=80"
PRODUCTS = [
    ("Premium Black Hoodie", "Issiq va qulay qora rangli hudi (kaputonkali kofta). 100% paxta, uch ipli mato.", 319000,
     "photo-1556905055-8f358a7a47b2", {"color": "qora", "category": "hoodie", "season": "kuz-qish"}, ["M", "L", "XL"]),
    ("Soft Beige Hoodie", "Yumshoq bej rangli hudi. Qulay bichim va zamonaviy ko'rinish.", 289000,
     "photo-1620799140408-edc6dcb6d633", {"color": "bej", "category": "hoodie"}, ["S", "M", "L"]),
    ("White Essential T-Shirt", "Klassik oq futbolka. Premium paxta, yengil va nafas oluvchi mato.", 139000,
     "photo-1521572267360-ee0c2909d518", {"color": "oq", "category": "t-shirt"}, ["S", "M", "L", "XL"]),
    ("Classic Black T-Shirt", "Har kungi kiyish uchun qulay qora futbolka.", 159000,
     "photo-1583743814966-8936f5b7be1a", {"color": "qora", "category": "t-shirt"}, ["M", "L", "XL"]),
    ("Oversize Basic T-Shirt", "Keng bichimli (oversize) zamonaviy futbolka.", 149000,
     "photo-1503342217505-b0a15ec3261c", {"color": "kulrang", "category": "t-shirt"}, ["M", "L"]),
    ("Classic Blue Jeans", "Sifatli ko'k jinsi shim. To'g'ri bichim (straight fit), cho'ziluvchan va chidamli.", 279000,
     "photo-1541099649105-f69ad21f3246", {"color": "ko'k", "category": "jeans"}, ["30", "32", "34"]),
    ("Black Straight Jeans", "Qora klassik to'g'ri bichimli jinsi shim.", 289000,
     "photo-1624378439575-d8705ad7ae80", {"color": "qora", "category": "jeans"}, ["30", "32", "34"]),
    ("White Casual Sneakers", "Oq krossovka. Yengil, qulay va kundalik kiyishga mo'ljallangan.", 399000,
     "photo-1595950653106-6c9ebd614d3a", {"color": "oq", "category": "sneakers"}, ["40", "41", "42", "43"]),
    ("Black Everyday Sneakers", "Qora krossovka. Qulay taglik va zamonaviy dizayn.", 429000,
     "photo-1491553895911-0055eca6402d", {"color": "qora", "category": "sneakers"}, ["41", "42", "43", "44"]),
    ("Black Baseball Cap", "Sifatli matodan tikilgan qora kepka. Bosh o'lchamiga moslanadi.", 99000,
     "photo-1588850561407-ed78c282e89b", {"color": "qora", "category": "cap"}, ["standard"]),
]


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes-wipe-everything", action="store_true", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--owner-password", required=True)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    expires = now + dt.timedelta(days=365)
    async with async_session_factory() as db, db.begin():
        # Keep a connected Instagram account for the demo shop, if there is one.
        acc = await db.scalar(select(InstagramAccount).where(InstagramAccount.status == "connected").limit(1))
        saved_ig = (
            {c: getattr(acc, c) for c in ("ig_business_id", "ig_username", "fb_page_id",
                                          "access_token_encrypted", "token_expires_at", "connected_at")}
            if acc else None
        )

        await db.execute(text(f"TRUNCATE TABLE {', '.join(_TABLES)} CASCADE"))

        superadmin = User(email=args.admin_email.strip().lower(), password_hash=hash_password(args.admin_password),
                          is_superadmin=True)
        owner = User(email=args.owner_email.strip().lower(), password_hash=hash_password(args.owner_password))
        db.add_all([superadmin, owner])
        await db.flush()

        shop = Business(
            owner_user_id=owner.id,
            name="Playzon Store",
            description="Kiyim do'koni. Erkaklar va ayollar uchun sifatli oversize kiyimlar, hudilar, futbolkalar va jinsilar.",
            target_customers="Yoshlar, talabalar, zamonaviy va qulay kiyinishni xush ko'ruvchilar",
            tone="samimiy va professional",
            language="o'zbek, rus, ingliz",
            selling_approach=(
                "Samimiy salomlashish, mijoz nima qidirayotganini aniqlash, mos mahsulotlarni tavsiya qilish, "
                "razmer va ranglarini aytish va buyurtma uchun telefon raqamini so'rash."
            ),
            discount_policy="3 tadan ko'p mahsulot olsa 10% chegirma bor",
            delivery_info="Toshkent bo'ylab 1 kunda yetkaziladi (25,000 so'm). Viloyatlarga 2-3 kunda (35,000 so'm).",
            payment_info="Click, Payme yoki naqd pul (qabul qilib olganda)",
            handoff_instructions="Operator faqat mijoz o'zi odam bilan gaplashmoqchi bo'lsa ulanadi.",
            ai_enabled=True,
            subscription_expires_at=expires,
        )
        db.add(shop)
        await db.flush()

        if saved_ig:
            db.add(InstagramAccount(business_id=shop.id, status="connected",
                                    **{**saved_ig, "connected_at": saved_ig["connected_at"] or now}))

        for name, description, price, photo, attributes, sizes in PRODUCTS:
            url = _IMG.format(photo)
            product = Product(
                business_id=shop.id,
                name=name,
                description=description,
                price=price,
                currency="UZS",
                availability=True,
                attributes={**attributes, "image_url": url},
                source="manual",
                search_normalized=normalize_for_search(f"{name} {description}"),
            )
            product.images = [ProductImage(r2_key=f"external/{uuid.uuid4()}", url=url, is_primary=True)]
            product.variants = [ProductVariant(variant_type="size", value=size, availability=True) for size in sizes]
            db.add(product)

    print("Wiped and seeded. Run `python backfill_embeddings.py` if semantic search is configured.")


if __name__ == "__main__":
    # Two independent locks: APP_ENV alone isn't enough (a server can be
    # mislabelled "development"), so the shell running this must also opt in
    # explicitly — nothing in any .env ever sets MIVO_ALLOW_DB_WIPE.
    if get_settings().app_env.strip().lower() != "development":
        sys.exit("Refusing to run: APP_ENV is not 'development'. This script deletes every row in the database.")
    if os.environ.get("MIVO_ALLOW_DB_WIPE") != "yes-delete-everything":
        sys.exit("Refusing to run: set MIVO_ALLOW_DB_WIPE=yes-delete-everything in THIS shell to confirm.")
    asyncio.run(run(_args()))
