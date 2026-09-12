"""Hot-lead Telegram notification. Fires from Instagram's webhook pipeline
when a lead reaches HOT status."""
import html
import re
import uuid
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.core.config import get_settings
from app.customers.models import Customer
from app.leads.models import Lead
from app.telegram.client import TelegramAPIError, TelegramClient
from app.telegram.models import TelegramConnection

logger = logging.getLogger("app.leads.notifications")

_TEMPLATE = """🔥 <b>YANGI ISSIQ LID (HOT LEAD)!</b>

👤 <b>Instagram:</b> @{username}
📞 <b>Telefon:</b> <code>{phone}</code>
📊 <b>Niyat darajasi:</b> <code>{score}/100</code>

📦 <b>Qiziqqan mahsulotlari:</b>
{products}

💡 <b>AI Xulosasi:</b>
<i>{summary}</i>

👉 <a href="{frontend}/leads?id={lead_id}">Mivo Dashboard-da ochish</a>"""


def get_telegram_client() -> TelegramClient:
    """Factory indirection so tests can monkeypatch or mock it."""
    return TelegramClient()


def _sanitize_summary(raw: str | None) -> str:
    """Removes any internal system prompt tokens, <think> tags, raw tool calls,
    or stack traces to ensure clean, customer-safe text."""
    if not raw or not raw.strip():
        return "—"  # no summary exists; don't make one up
    text = raw.strip()
    # Remove internal thinking/tool XML tags if any leaked
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL)
    text = re.sub(r"<.*?>", "", text)
    # Remove prompt headers
    text = re.sub(r"^(SYSTEM|USER|ASSISTANT|HUMAN):", "", text, flags=re.IGNORECASE).strip()
    return text or "—"


def _format_notification(customer: Customer, lead: Lead) -> str:
    if lead.interested_products:
        products = "\n".join(f"• {html.escape(p.get('name', 'Mahsulot'))}" for p in lead.interested_products)
    else:
        products = "—"

    raw_username = customer.username or f"user_{customer.ig_scoped_id[:8]}"
    clean_username = html.escape(raw_username.lstrip("@"))
    clean_phone = html.escape(lead.phone or "—")
    clean_summary = html.escape(_sanitize_summary(lead.summary or lead.qualification_reason))

    return _TEMPLATE.format(
        username=clean_username,
        phone=clean_phone,
        score=lead.score,
        products=products,
        summary=clean_summary,
        lead_id=lead.id,
        frontend=html.escape(get_settings().frontend_url, quote=True),
    )


async def notify_hot_lead(
    db: AsyncSession,
    business: Business,
    lead: Lead,
    client: TelegramClient | None = None,
) -> bool:
    """Sends a formatted HOT lead notification to the connected Telegram chat.
    
    Returns True if sent successfully, False otherwise.
    Safe and resilient:
    - If Telegram is disconnected, returns False without error.
    - If Telegram API fails, records error on lead.last_notification_error and returns False
      without crashing webhook or rolling back lead updates.
    """
    connection = await db.scalar(
        select(TelegramConnection).where(
            TelegramConnection.business_id == business.id,
            TelegramConnection.telegram_chat_id.is_not(None),
        )
    )
    if connection is None or not connection.telegram_chat_id:
        return False  # Telegram not connected — skip gracefully

    customer = await db.get(Customer, lead.customer_id)
    if customer is None:
        return False

    text = _format_notification(customer, lead)
    frontend = get_settings().frontend_url
    buttons = [{"text": "👤 Lid tafsilotlari", "url": f"{frontend}/leads?id={lead.id}"}]
    if lead.conversation_id:
        buttons.insert(0, {"text": "💬 Suhbatni ochish", "url": f"{frontend}/?id={lead.conversation_id}"})
    reply_markup = {"inline_keyboard": [buttons]}

    try:
        telegram_client = client or get_telegram_client()
        await telegram_client.send_message(connection.telegram_chat_id, text, reply_markup=reply_markup)
    except TelegramAPIError as exc:
        # TelegramAPIError text is built without the request URL (which
        # carries the bot token), so it's safe to store and log.
        logger.error("[LeadNotification] Telegram notification failed for lead %s: %s", lead.id, exc)
        lead.last_notification_error = str(exc)[:500]
        return False
    lead.last_notification_error = None
    return True
