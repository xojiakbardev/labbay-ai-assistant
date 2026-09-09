"""Instagram connect + webhook processing (plan §11). All Meta-specific logic
stays in this module; other modules only ever see the neutral conversations/
messages domain model."""
import asyncio
import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestrator import run_turn
from app.ai.provider.base import LLMProvider
from app.businesses.models import Business
from app.conversations.models import Message
from app.conversations.service import add_message, get_or_create_conversation, get_recent_messages
from app.core.security import decrypt_secret, encrypt_secret
from app.customers.service import get_or_create_customer
from app.instagram.client import ConnectedAccount, MetaClient
from app.instagram.models import InstagramAccount
from app.leads.notifications import notify_hot_lead
from app.leads.scoring import extract_valid_phone
from app.leads.service import apply_qualification, capture_phone_without_ai_turn, mark_hot_notified
from app.notifications.service import create_notification
from app.products.media import resolve_sendable_images
from app.push.service import send_push_notification
from app.webhooks.models import WebhookEvent

# How long to wait, after persisting a customer's message, before actually
# generating an AI reply — a real person typing on Instagram often sends
# several short messages in a row ("Salom", "poyabzal bormi", "42 razmer
# bormi") a second or two apart; replying to each one separately reads as
# robotic and can even answer a question before the customer finishes asking
# it. Every inbound webhook request for a conversation persists its message
# immediately (so the owner sees it right away in the dashboard) and then
# waits this long before checking whether it's still the *last* message in
# the conversation — if a newer one arrived while it waited, this request
# just exits and lets the newer one (which does the same check) be the one
# that replies, so one reply ends up covering the whole burst. Tests
# monkeypatch this to 0 so they don't pay the wall-clock cost.
DEBOUNCE_SECONDS = 1.5

# Conversation statuses under which the AI should stay silent — the business
# owner (or a prior escalation) has taken this conversation off autopilot, so
# a new inbound message must still be recorded for them to see, but must NOT
# get an automatic AI reply until it's handed back (plan addendum: "AI faol
# emas" is a normal resting state for a conversation, not just an error path).
_AI_ACTIVE_STATUSES = ("ai_active", "active")


async def connect_account(
    db: AsyncSession, business_id: uuid.UUID, account: ConnectedAccount
) -> InstagramAccount:
    existing = await db.scalar(
        select(InstagramAccount).where(InstagramAccount.business_id == business_id)
    )
    encrypted = encrypt_secret(account.access_token)
    if existing is None:
        existing = InstagramAccount(
            business_id=business_id,
            ig_business_id=account.ig_business_id,
            ig_username=account.ig_username,
            fb_page_id=account.fb_page_id,
            access_token_encrypted=encrypted,
            token_expires_at=account.expires_at,
            status="connected",
            connected_at=dt.datetime.now(dt.timezone.utc),
        )
        db.add(existing)
    else:
        existing.ig_business_id = account.ig_business_id
        existing.ig_username = account.ig_username
        existing.fb_page_id = account.fb_page_id
        existing.access_token_encrypted = encrypted
        existing.token_expires_at = account.expires_at
        existing.status = "connected"
        existing.connected_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()
    await db.refresh(existing)
    return existing


async def get_account(db: AsyncSession, business_id: uuid.UUID) -> InstagramAccount | None:
    return await db.scalar(
        select(InstagramAccount).where(InstagramAccount.business_id == business_id)
    )


async def disconnect_account(db: AsyncSession, business_id: uuid.UUID, meta_client: MetaClient) -> None:
    account = await db.scalar(
        select(InstagramAccount).where(InstagramAccount.business_id == business_id)
    )
    if account is None:
        return

    if account.access_token_encrypted:
        try:
            token = decrypt_secret(account.access_token_encrypted)
            await meta_client.deauthorize_account(token, fb_page_id=account.fb_page_id)
        except Exception as e:
            print(f"[InstagramService] Deauthorization error for business {business_id}: {e}")

    await db.delete(account)
    await db.commit()


REFRESH_WINDOW = dt.timedelta(days=10)
MIN_TOKEN_AGE = dt.timedelta(hours=24)


async def refresh_expiring_tokens(db: AsyncSession, meta_client: MetaClient) -> int:
    """Keeps connected Instagram accounts alive indefinitely without the
    owner ever clicking "Qayta ulash" again: Meta's long-lived tokens are
    hard-capped at 60 days, but refreshing one (while it's still valid)
    resets that clock to another 60 — see app/instagram/client.py. Meant to
    run on a daily schedule (app/main.py); returns the number refreshed.

    Only touches tokens inside REFRESH_WINDOW of expiring — refreshing every
    run would work too, but this keeps the steady-state case a no-op and
    only spends an extra Meta API call when a token is actually approaching
    its deadline. A token past MIN_TOKEN_AGE only wasn't reachable when it
    was refreshed *today*, not on every run — that's fine, it'll simply be
    picked up tomorrow, still comfortably inside the 60-day window.
    """
    now = dt.datetime.now(dt.timezone.utc)
    candidates = (
        await db.execute(
            select(InstagramAccount).where(
                InstagramAccount.status == "connected",
                InstagramAccount.token_expires_at.is_not(None),
                InstagramAccount.token_expires_at > now,
                InstagramAccount.token_expires_at <= now + REFRESH_WINDOW,
                InstagramAccount.connected_at <= now - MIN_TOKEN_AGE,
            )
        )
    ).scalars().all()

    refreshed = 0
    for account in candidates:
        try:
            current_token = decrypt_secret(account.access_token_encrypted)
            result = await meta_client.refresh_long_lived_token(current_token)
        except Exception as exc:  # noqa: BLE001 — one bad account must not stop the sweep
            print(f"[instagram] token refresh failed for business {account.business_id}: {exc}")
            continue

        account.access_token_encrypted = encrypt_secret(result.access_token)
        account.token_expires_at = result.expires_at
        await db.commit()
        refreshed += 1

    return refreshed


async def _claim_webhook_event(db: AsyncSession, external_event_id: str, payload: dict) -> WebhookEvent | None:
    """Returns the event row to process, or None if it's a true duplicate or already processing.

    Using 'processing' prevents Meta's aggressive 5-second webhook retries from
    concurrently executing the AI reply pipeline multiple times.
    """
    from sqlalchemy.exc import IntegrityError

    existing = await db.scalar(
        select(WebhookEvent)
        .where(WebhookEvent.external_event_id == external_event_id)
        .with_for_update()
    )
    if existing is not None:
        if existing.status in ("processed", "processing"):
            return None
        existing.status = "processing"
        await db.commit()
        return existing

    event = WebhookEvent(
        provider="instagram",
        external_event_id=external_event_id,
        payload=payload,
        status="processing",
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    db.add(event)
    try:
        await db.commit()
        return event
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(
            select(WebhookEvent).where(WebhookEvent.external_event_id == external_event_id)
        )
        if existing is not None and existing.status in ("processed", "processing"):
            return None
        return existing


async def process_incoming_message(
    db: AsyncSession,
    provider: LLMProvider,
    meta_client: MetaClient,
    *,
    ig_recipient_id: str,  # the business's own IG account ID (webhook's "recipient")
    customer_ig_scoped_id: str,  # the customer's IGSID (webhook's "sender")
    message_text: str,
    external_message_id: str,
    attachment_url: str | None = None,
    attachment_type: str | None = None,
) -> None:
    """The full webhook -> AI -> lead -> reply pipeline for one inbound DM.

    Raises on failure (after marking the event "failed") so FastAPI returns a
    5xx and Meta's own webhook retry has a chance to complete the delivery —
    see _claim_webhook_event for why "failed" isn't treated as terminal.

    Note on retry idempotency: the inbound customer message is guaranteed not
    to be double-persisted (add_message dedupes on external_message_id). A
    retry that re-runs after the AI reply was already generated but before
    Instagram send succeeded *can* produce a second AI reply/LLM call — full
    exactly-once delivery would need an outbox pattern, which is more
    infrastructure than an MVP at ~10k msgs/month needs (plan §32: prefer
    simple reprocessing over Celery/Redis-backed retry queues). The tradeoff is
    deliberate: an occasional duplicate reply is far better than silently
    losing the customer's message, which was the previous behavior.
    """
    event = await _claim_webhook_event(
        db, external_message_id, {"recipient": ig_recipient_id, "sender": customer_ig_scoped_id, "text": message_text}
    )
    if event is None:
        return  # already fully processed — true duplicate delivery, do nothing

    try:
        account = await db.scalar(
            select(InstagramAccount).where(
                (InstagramAccount.ig_business_id == ig_recipient_id) |
                (InstagramAccount.fb_page_id == ig_recipient_id)
            )
        )
        if account is None:
            # Fallback to active connected account if Meta passed a scoped page ID
            account = await db.scalar(
                select(InstagramAccount).where(InstagramAccount.status == "connected")
            )

        if account is None:
            event.status = "processed"  # nothing to retry — no connected account
            event.processed_at = dt.datetime.now(dt.timezone.utc)
            await db.commit()
            return

        business = await db.get(Business, account.business_id)
        customer = await get_or_create_customer(db, business.id, customer_ig_scoped_id)
        conversation = await get_or_create_conversation(db, business.id, customer.id)

        # Immediately fetch Instagram profile (username and full name)
        if (not customer.username or not customer.name) and account.access_token_encrypted:
            try:
                acc_token = decrypt_secret(account.access_token_encrypted)
                prof = await meta_client.get_user_profile(customer_ig_scoped_id, acc_token)
                if prof:
                    if prof.get("username"):
                        customer.username = prof["username"].lstrip("@")
                    if prof.get("name"):
                        customer.name = prof["name"]
            except Exception as e:
                print(f"[InstagramService] Initial profile fetch error for {customer_ig_scoped_id}: {e}")

        await db.commit()

        def _ai_should_reply(biz: Business, conv) -> bool:
            expired = (
                biz.subscription_expires_at is not None
                and biz.subscription_expires_at <= dt.datetime.now(dt.timezone.utc)
            )
            return biz.ai_enabled and not expired and conv.status in _AI_ACTIVE_STATUSES

        # The customer's message is always recorded immediately, regardless of
        # whether the AI will reply — the owner must see it either way (plan
        # §11 / addendum: a paused-AI conversation still shows every message).
        my_message = await add_message(
            db, conversation, sender_type="customer", content=message_text,
            message_type=attachment_type if attachment_type else "text",
            external_message_id=external_message_id,
            attachment_url=attachment_url,
            attachment_type=attachment_type,
        )
        await db.commit()

        if not _ai_should_reply(business, conversation):
            # AI is off, the subscription lapsed, or this conversation was
            # handed to a human (status not in _AI_ACTIVE_STATUSES) — leave it
            # for the owner to answer by hand, no LLM call, no auto-reply.
            # A phone number is still worth capturing even here — often this
            # is exactly the customer replying to "leave your number" right
            # before the AI went quiet (see capture_phone_without_ai_turn).
            phone_found = extract_valid_phone(message_text)
            lead, became_hot = await capture_phone_without_ai_turn(
                db, business.id, customer.id, conversation.id, message_text
            )

            # If customer provided a phone number, ALWAYS send a polite acknowledgment
            # even when the conversation is handed off / waiting for a human!
            if phone_found and account and account.access_token_encrypted:
                try:
                    access_token = decrypt_secret(account.access_token_encrypted)
                    if access_token:
                        from app.ai.orchestrator import detect_preferred_language, _PHONE_CAPTURED_CONFIRMATION
                        recent_msgs = await get_recent_messages(db, conversation.id, limit=4)
                        sample_texts = [m.content for m in recent_msgs if m.content] + [message_text]
                        lang = detect_preferred_language(business.language, sample_texts)
                        confirm_text = _PHONE_CAPTURED_CONFIRMATION.get(lang, _PHONE_CAPTURED_CONFIRMATION["uz"])

                        ai_msg = await add_message(
                            db,
                            conversation,
                            sender_type="ai",
                            content=confirm_text,
                            message_type="text",
                        )
                        await db.commit()

                        ai_resp = await meta_client.send_message(
                            ig_business_id=account.ig_business_id,
                            access_token=access_token,
                            recipient_id=customer_ig_scoped_id,
                            text=confirm_text,
                        )
                        if isinstance(ai_resp, dict) and ai_resp.get("message_id"):
                            ai_msg.external_message_id = ai_resp["message_id"]
                            await db.commit()
                except Exception as e:
                    print(f"[InstagramService] Failed to send phone capture confirmation to IG: {e}")

            if became_hot and lead is not None:
                try:
                    sent = await notify_hot_lead(db, business, lead)
                    if sent is not False:
                        await mark_hot_notified(db, lead)
                except Exception as e:
                    print(f"[InstagramService] Telegram hot lead notification error: {e}")

                try:
                    cust_name = f"@{customer.username.lstrip('@')}" if customer.username else (customer.name or "Instagram foydalanuvchisi")
                    phone_str = f" • {lead.phone}" if lead.phone else ""
                    summary_snippet = f": {lead.summary}" if lead.summary else ""
                    await create_notification(
                        db,
                        business_id=business.id,
                        type="lead_hot",
                        title="Yangi Issiq Lid 🔥",
                        message=f"{cust_name}{phone_str}{summary_snippet}",
                        lead_id=lead.id,
                        customer_id=lead.customer_id,
                        extra_metadata={
                            "score": lead.score,
                            "phone": lead.phone,
                            "username": customer.username,
                        },
                    )
                except Exception as e:
                    print(f"[InstagramService] Dashboard in-app notification error: {e}")

                try:
                    cust_name = f"@{customer.username.lstrip('@')}" if customer.username else (customer.name or "Instagram foydalanuvchisi")
                    phone_str = f" • {lead.phone}" if lead.phone else ""
                    await send_push_notification(
                        db,
                        business_id=business.id,
                        title="Yangi Issiq Lid 🔥",
                        body=f"{cust_name}{phone_str}",
                        url=f"/leads?id={lead.id}",
                        tag=f"lead-hot-{lead.id}",
                        data={"lead_id": str(lead.id)},
                    )
                except Exception as e:
                    print(f"[InstagramService] Web Push notification error: {e}")
            event.status = "processed"
            event.processed_at = dt.datetime.now(dt.timezone.utc)
            await db.commit()
            return

        if DEBOUNCE_SECONDS > 0:
            await asyncio.sleep(DEBOUNCE_SECONDS)

        # Did a newer customer message land in this conversation while we
        # waited? If so, that request is doing (or will do) this same check
        # and will be the one that replies — covering this message too, since
        # run_turn reads the full persisted history. Bail out here rather
        # than risk two overlapping replies to one burst (or to a backlog of
        # messages that piled up while AI was paused/the server was down —
        # same mechanism, no separate handling needed).
        #
        # Note: this deliberately only looks at customer messages, not AI
        # ones. An AI message can exist here without ever having reached the
        # customer (send_message can fail after the reply was persisted —
        # see test_failed_delivery_is_marked_failed_and_can_be_retried), and
        # a Meta retry replaying the same external_message_id must still be
        # allowed to actually resend it, not stand down thinking it's already
        # answered.
        newer_message_id = await db.scalar(
            select(Message.id).where(
                Message.conversation_id == conversation.id,
                Message.sender_type == "customer",
                Message.created_at > my_message.created_at,
            ).limit(1)
        )
        if newer_message_id is not None:
            event.status = "processed"
            event.processed_at = dt.datetime.now(dt.timezone.utc)
            await db.commit()
            return

        # Re-check right before replying — several seconds may have passed,
        # and the owner may have toggled AI off or taken over the conversation
        # during the wait.
        await db.refresh(business)
        await db.refresh(conversation)
        if not _ai_should_reply(business, conversation):
            event.status = "processed"
            event.processed_at = dt.datetime.now(dt.timezone.utc)
            await db.commit()
            return

        result = await run_turn(db, provider, business, conversation)
        await db.commit()

        lead, became_hot = await apply_qualification(
            db, business.id, customer.id, conversation.id, result, raw_message_text=message_text
        )

        access_token = decrypt_secret(account.access_token_encrypted)

        # Fetch / update customer Instagram profile (username/name) if not set
        if (not customer.username or not customer.name) and access_token:
            try:
                prof = await meta_client.get_user_profile(customer_ig_scoped_id, access_token)
                if prof:
                    if prof.get("username"):
                        customer.username = prof["username"].lstrip("@")
                    if prof.get("name"):
                        customer.name = prof["name"]
                    await db.commit()
                    print(f"[InstagramService] Updated customer {customer.id} with IG @{customer.username} / {customer.name}")
            except Exception as e:
                print(f"[InstagramService] Failed to fetch customer IG profile: {e}")

        try:
            ai_resp = await meta_client.send_message(
                ig_business_id=account.ig_business_id,
                access_token=access_token,
                recipient_id=customer_ig_scoped_id,
                text=result.reply,
            )
            if isinstance(ai_resp, dict) and ai_resp.get("message_id"):
                latest_ai_msg = await db.scalar(
                    select(Message).where(
                        Message.conversation_id == conversation.id,
                        Message.sender_type == "ai",
                    ).order_by(Message.created_at.desc()).limit(1)
                )
                if latest_ai_msg is not None:
                    latest_ai_msg.external_message_id = ai_resp["message_id"]
                    await db.commit()
        except Exception as e:
            print(f"[InstagramService] Failed to send AI reply to {customer_ig_scoped_id}: {e}")
            raise

        if result.image_product_ids:
            image_urls = await resolve_sendable_images(db, business.id, result.image_product_ids)
            for image_url in image_urls:
                try:
                    await meta_client.send_image(
                        ig_business_id=account.ig_business_id,
                        access_token=access_token,
                        recipient_id=customer_ig_scoped_id,
                        image_url=image_url,
                    )
                except Exception as e:
                    # A photo that fails to send must never take the whole
                    # customer-facing turn down with it — the text reply
                    # already went out.
                    print(f"[InstagramService] Failed to send product image: {e}")

        if became_hot and lead is not None:
            try:
                sent = await notify_hot_lead(db, business, lead)
                if sent is not False:
                    await mark_hot_notified(db, lead)
            except Exception as e:
                print(f"[InstagramService] Telegram hot lead notification error: {e}")

            try:
                cust_name = f"@{customer.username.lstrip('@')}" if customer.username else (customer.name or "Instagram foydalanuvchisi")
                phone_str = f" • {lead.phone}" if lead.phone else ""
                summary_snippet = f": {lead.summary}" if lead.summary else ""
                await create_notification(
                    db,
                    business_id=business.id,
                    type="lead_hot",
                    title="Yangi Issiq Lid 🔥",
                    message=f"{cust_name}{phone_str}{summary_snippet}",
                    lead_id=lead.id,
                    customer_id=lead.customer_id,
                    extra_metadata={
                        "score": lead.score,
                        "phone": lead.phone,
                        "username": customer.username,
                        "products": [p.get("name") for p in (lead.interested_products or []) if isinstance(p, dict)],
                    },
                )
            except Exception as e:
                print(f"[InstagramService] Dashboard in-app notification error: {e}")

            try:
                cust_name = f"@{customer.username.lstrip('@')}" if customer.username else (customer.name or "Instagram foydalanuvchisi")
                phone_str = f" • {lead.phone}" if lead.phone else ""
                await send_push_notification(
                    db,
                    business_id=business.id,
                    title="Yangi Issiq Lid 🔥",
                    body=f"{cust_name}{phone_str}",
                    url=f"/leads?id={lead.id}",
                    tag=f"lead-hot-{lead.id}",
                    data={"lead_id": str(lead.id)},
                )
            except Exception as e:
                print(f"[InstagramService] Web Push notification error: {e}")

        event.status = "processed"
        event.processed_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()
    except Exception:
        event.status = "failed"
        await db.commit()
        raise


async def process_echo_message(
    db: AsyncSession,
    *,
    ig_sender_id: str,
    customer_ig_scoped_id: str,
    message_text: str,
    external_message_id: str,
    meta_client: MetaClient | None = None,
    attachment_url: str | None = None,
    attachment_type: str | None = None,
) -> None:
    """Processes an outgoing message echo (sent by human operator on Instagram app)."""
    # 1. Skip if message already recorded (e.g. sent by AI or previous turn)
    if external_message_id:
        existing_msg = await db.scalar(
            select(Message).where(Message.external_message_id == external_message_id)
        )
        if existing_msg is not None:
            return

    # 2. Find Instagram Account
    account = await db.scalar(
        select(InstagramAccount).where(
            InstagramAccount.ig_business_id == ig_sender_id,
            InstagramAccount.status == "connected",
        )
    )
    if account is None:
        account = await db.scalar(
            select(InstagramAccount).where(InstagramAccount.status == "connected")
        )
    if account is None:
        return

    business = await db.get(Business, account.business_id)
    if business is None:
        return

    customer = await get_or_create_customer(db, business.id, customer_ig_scoped_id)
    conversation = await get_or_create_conversation(db, business.id, customer.id)

    # Immediately fetch Instagram profile (username and full name) if missing
    if (not customer.username or not customer.name) and account.access_token_encrypted:
        try:
            acc_token = decrypt_secret(account.access_token_encrypted)
            client = meta_client or MetaClient()
            prof = await client.get_user_profile(customer_ig_scoped_id, acc_token)
            if prof:
                if prof.get("username"):
                    customer.username = prof["username"].lstrip("@")
                if prof.get("name"):
                    customer.name = prof["name"]
        except Exception as e:
            print(f"[InstagramService] Echo profile fetch error for {customer_ig_scoped_id}: {e}")

    # 3. Add message with sender_type="human"
    now = dt.datetime.now(dt.timezone.utc)
    await add_message(
        db,
        conversation,
        sender_type="human",
        content=message_text,
        message_type=attachment_type if attachment_type else "text",
        external_message_id=external_message_id or None,
        attachment_url=attachment_url,
        attachment_type=attachment_type,
    )
    conversation.last_message_at = now
    await db.commit()

    # 4. Broadcast notification to dashboard listeners so UI updates in real-time
    try:
        from app.notifications.broadcaster import broadcaster
        await broadcaster.broadcast(
            business.id,
            {
                "type": "conversation_updated",
                "conversation_id": str(conversation.id),
                "customer_id": str(customer.id),
                "last_message": message_text,
                "sender_type": "human",
            },
        )
    except Exception as e:
        print(f"[InstagramService] Broadcast echo message error: {e}")

