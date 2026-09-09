import asyncio
import datetime as dt
import uuid

from sqlalchemy import select, text

from app.auth.models import User
from app.businesses.models import Business
from app.core.db import async_session_factory
from app.core.security import hash_password
from app.instagram.models import InstagramAccount
from app.products.models import Product, ProductVariant


async def run():
    async with async_session_factory() as db:
        # 1. Back up existing Instagram connection if present
        acc = await db.scalar(select(InstagramAccount).where(InstagramAccount.status == "connected"))
        saved_ig = None
        if acc:
            saved_ig = {
                "ig_business_id": acc.ig_business_id,
                "ig_username": acc.ig_username,
                "fb_page_id": acc.fb_page_id,
                "access_token_encrypted": acc.access_token_encrypted,
                "token_expires_at": acc.token_expires_at,
                "status": acc.status,
                "connected_at": acc.connected_at,
            }
            print(f"Backed up Instagram account: @{acc.ig_username}")

        # 2. Truncate all tables
        tables = [
            "messages",
            "conversations",
            "customers",
            "leads",
            "notifications",
            "push_subscriptions",
            "ai_feedbacks",
            "ai_usage_logs",
            "payments",
            "webhook_events",
            "telegram_connections",
            "product_images",
            "product_variants",
            "products",
            "instagram_accounts",
            "businesses",
            "users",
        ]
        stmt = f"TRUNCATE TABLE {', '.join(tables)} CASCADE;"
        await db.execute(text(stmt))
        await db.commit()
        print("Truncated all tables successfully.")

        now = dt.datetime.now(dt.timezone.utc)
        expires_2028 = dt.datetime(2028, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc)

        # 3. Superuser
        superadmin = User(
            id=uuid.uuid4(),
            email="admin",
            password_hash=hash_password("qwertyuiop"),
            is_superadmin=True,
            created_at=now,
            updated_at=now,
        )
        db.add(superadmin)
        await db.flush()

        superadmin_biz = Business(
            id=uuid.uuid4(),
            owner_user_id=superadmin.id,
            name="Mivo Platform HQ",
            description="Mivo boshqaruv markazi va superadmin kabineti.",
            tone="professional",
            language="o'zbek, rus, ingliz",
            ai_enabled=True,
            subscription_expires_at=expires_2028,
            created_at=now,
            updated_at=now,
        )
        db.add(superadmin_biz)

        # 4. Oddiy User (Regular Business Owner)
        user = User(
            id=uuid.uuid4(),
            email="test@gmail.com",
            password_hash=hash_password("qwertyuiop"),
            is_superadmin=False,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        await db.flush()

        user_biz = Business(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name="Playzon Store",
            description="Kiyim do'koni. Erkaklar va ayollar uchun sifatli oversize kiyimlar, hudilar, futbolkalar va jinsilar.",
            target_customers="Yoshlar, talabalar, zamonaviy va qulay kiyinishni xush ko'ruvchilar",
            tone="samimiy va professional",
            language="o'zbek, rus, ingliz",
            selling_approach=(
                "Samimiy salomlashish, mijoz nima qidirayotganini aniqlash, mos mahsulotlarni tavsiya qilish, "
                "razmer va ranglarini aytish, narxini aytib qiziqish uyg'otish va buyurtmani rasmiylashtirish uchun "
                "telefon raqamini so'rash."
            ),
            discount_policy="3 tadan ko'p mahsulot olsa 10% chegirma bor",
            delivery_info="Toshkent bo'ylab 1 kunda yetkaziladi (25,000 so'm). Viloyatlarga BTS yoki Fargo orqali 2-3 kunda yetkaziladi (35,000 so'm).",
            payment_info="Click, Payme yoki naqd pul (qabul qilib olganda)",
            handoff_instructions="Operator faqat mijoz o'zi shaxsan odam bilan gaplashmoqchi ekanini so'ragandagina ulanadi.",
            ai_enabled=True,
            subscription_expires_at=expires_2028,
            created_at=now,
            updated_at=now,
        )
        db.add(user_biz)
        await db.flush()

        # 5. Restore Instagram connection for the user's business
        if saved_ig:
            ig_acc = InstagramAccount(
                id=uuid.uuid4(),
                business_id=user_biz.id,
                ig_business_id=saved_ig["ig_business_id"],
                ig_username=saved_ig["ig_username"],
                fb_page_id=saved_ig["fb_page_id"],
                access_token_encrypted=saved_ig["access_token_encrypted"],
                token_expires_at=saved_ig["token_expires_at"],
                status=saved_ig["status"],
                connected_at=saved_ig["connected_at"] or now,
            )
            db.add(ig_acc)
            print(f"Re-connected Instagram @{saved_ig['ig_username']} to user business.")

        # 6. Seed clean product catalog for the user's business
        products_data = [
            {
                "name": "Premium Black Hoodie",
                "description": "Issiq va qulay qora rangli hudi (kaputonkali kofta). 100% paxta, uch ipli mato.",
                "price": 319000,
                "image_url": "https://images.unsplash.com/photo-1556905055-8f358a7a47b2?auto=format&fit=crop&w=800&q=80",
                "attributes": {"color": "qora", "category": "hoodie", "season": "kuz-qish", "image_url": "https://images.unsplash.com/photo-1556905055-8f358a7a47b2?auto=format&fit=crop&w=800&q=80"},
                "variants": [("size", "M"), ("size", "L"), ("size", "XL")],
            },
            {
                "name": "Soft Beige Hoodie",
                "description": "Yumshoq bej rangli hudi. Qulay bichim va zamonaviy ko'rinish.",
                "price": 289000,
                "image_url": "https://images.unsplash.com/photo-1620799140408-edc6dcb6d633?auto=format&fit=crop&w=800&q=80",
                "attributes": {"color": "bej", "category": "hoodie", "image_url": "https://images.unsplash.com/photo-1620799140408-edc6dcb6d633?auto=format&fit=crop&w=800&q=80"},
                "variants": [("size", "S"), ("size", "M"), ("size", "L")],
            },
            {
                "name": "White Essential T-Shirt",
                "description": "Klassik oq futbolka. Premium paxta, yengil va nafas oluvchi mato.",
                "price": 139000,
                "image_url": "https://images.unsplash.com/photo-1521572267360-ee0c2909d518?auto=format&fit=crop&w=800&q=80",
                "attributes": {"color": "oq", "category": "t-shirt", "image_url": "https://images.unsplash.com/photo-1521572267360-ee0c2909d518?auto=format&fit=crop&w=800&q=80"},
                "variants": [("size", "S"), ("size", "M"), ("size", "L"), ("size", "XL")],
            },
            {
                "name": "Classic Black T-Shirt",
                "description": "Har kungi kiyish uchun qulay qora futbolka.",
                "price": 159000,
                "image_url": "https://images.unsplash.com/photo-1583743814966-8936f5b7be1a?auto=format&fit=crop&w=800&q=80",
                "attributes": {"color": "qora", "category": "t-shirt", "image_url": "https://images.unsplash.com/photo-1583743814966-8936f5b7be1a?auto=format&fit=crop&w=800&q=80"},
                "variants": [("size", "M"), ("size", "L"), ("size", "XL")],
            },
            {
                "name": "Oversize Basic T-Shirt",
                "description": "Keng bichimli (oversize) zamonaviy futbolka.",
                "price": 149000,
                "image_url": "https://images.unsplash.com/photo-1503342217505-b0a15ec3261c?auto=format&fit=crop&w=800&q=80",
                "attributes": {"color": "kulrang", "category": "t-shirt", "image_url": "https://images.unsplash.com/photo-1503342217505-b0a15ec3261c?auto=format&fit=crop&w=800&q=80"},
                "variants": [("size", "M"), ("size", "L")],
            },
            {
                "name": "Classic Blue Jeans",
                "description": "Sifatli ko'k jinsi shim. To'g'ri bichim (straight fit), cho'ziluvchan va chidamli.",
                "price": 279000,
                "image_url": "https://images.unsplash.com/photo-1541099649105-f69ad21f3246?auto=format&fit=crop&w=800&q=80",
                "attributes": {"color": "ko'k", "category": "jeans", "image_url": "https://images.unsplash.com/photo-1541099649105-f69ad21f3246?auto=format&fit=crop&w=800&q=80"},
                "variants": [("size", "30"), ("size", "32"), ("size", "34")],
            },
            {
                "name": "Black Straight Jeans",
                "description": "Qora klassik to'g'ri bichimli jinsi shim.",
                "price": 289000,
                "image_url": "https://images.unsplash.com/photo-1624378439575-d8705ad7ae80?auto=format&fit=crop&w=800&q=80",
                "attributes": {"color": "qora", "category": "jeans", "image_url": "https://images.unsplash.com/photo-1624378439575-d8705ad7ae80?auto=format&fit=crop&w=800&q=80"},
                "variants": [("size", "30"), ("size", "32"), ("size", "34")],
            },
            {
                "name": "White Casual Sneakers",
                "description": "Oq krossovka. Yengil, qulay va kundalik kiyishga mo'ljallangan.",
                "price": 399000,
                "image_url": "https://images.unsplash.com/photo-1595950653106-6c9ebd614d3a?auto=format&fit=crop&w=800&q=80",
                "attributes": {"color": "oq", "category": "sneakers", "image_url": "https://images.unsplash.com/photo-1595950653106-6c9ebd614d3a?auto=format&fit=crop&w=800&q=80"},
                "variants": [("size", "40"), ("size", "41"), ("size", "42"), ("size", "43")],
            },
            {
                "name": "Black Everyday Sneakers",
                "description": "Qora krossovka. Qulay taglik va zamonaviy dizayn.",
                "price": 429000,
                "image_url": "https://images.unsplash.com/photo-1491553895911-0055eca6402d?auto=format&fit=crop&w=800&q=80",
                "attributes": {"color": "qora", "category": "sneakers", "image_url": "https://images.unsplash.com/photo-1491553895911-0055eca6402d?auto=format&fit=crop&w=800&q=80"},
                "variants": [("size", "41"), ("size", "42"), ("size", "43"), ("size", "44")],
            },
            {
                "name": "Black Baseball Cap",
                "description": "Sifatli matodan tikilgan qora kepka. Bosh o'lchamiga moslanadi.",
                "price": 99000,
                "image_url": "https://images.unsplash.com/photo-1588850561407-ed78c282e89b?auto=format&fit=crop&w=800&q=80",
                "attributes": {"color": "qora", "category": "cap", "image_url": "https://images.unsplash.com/photo-1588850561407-ed78c282e89b?auto=format&fit=crop&w=800&q=80"},
                "variants": [("size", "standard")],
            },
        ]

        for p_data in products_data:
            prod = Product(
                id=uuid.uuid4(),
                business_id=user_biz.id,
                name=p_data["name"],
                description=p_data["description"],
                price=p_data["price"],
                currency="UZS",
                availability=True,
                attributes=p_data["attributes"],
                source="manual",
                created_at=now,
                updated_at=now,
            )
            db.add(prod)
            await db.flush()

            if p_data.get("image_url"):
                img = ProductImage(
                    id=uuid.uuid4(),
                    product_id=prod.id,
                    r2_key=f"external/{uuid.uuid4()}",
                    url=p_data["image_url"],
                    is_primary=True,
                )
                db.add(img)

            for var_type, var_val in p_data["variants"]:
                variant = ProductVariant(
                    id=uuid.uuid4(),
                    product_id=prod.id,
                    variant_type=var_type,
                    value=var_val,
                    availability=True,
                )
                db.add(variant)

        await db.commit()
        print("Seed finished successfully!")


if __name__ == "__main__":
    asyncio.run(run())
