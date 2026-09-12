"""Import every model module so Base.metadata is fully populated for Alembic autogenerate."""
from app.ai.models import AiFeedback, AiUsageLog, Payment  # noqa: F401
from app.auth.models import RefreshToken, User  # noqa: F401
from app.billing.models import AiReplyUsage, Plan  # noqa: F401
from app.businesses.models import Business  # noqa: F401
from app.instagram.models import InstagramAccount, OAuthState  # noqa: F401
from app.telegram.models import TelegramConnection  # noqa: F401
from app.products.models import Product, ProductImage, ProductVariant  # noqa: F401
from app.customers.models import Customer  # noqa: F401
from app.conversations.models import Conversation, Message  # noqa: F401
from app.leads.models import Lead  # noqa: F401
from app.notifications.models import Notification  # noqa: F401
from app.push.models import PushSubscription  # noqa: F401
from app.webhooks.models import WebhookEvent  # noqa: F401
from app.discounts.models import Discount  # noqa: F401
