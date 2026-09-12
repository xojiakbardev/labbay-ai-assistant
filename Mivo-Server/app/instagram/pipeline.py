"""Instagram webhook events: ingest in the request (dedupe, route exactly, store),
process in the background (debounce, lock, AI turn, outbox), sweep what failed."""
import asyncio
import datetime as dt
import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import limits
from app.ai.audio import TranscriptionError, transcribe_audio_url
from app.ai.closing import decide_closing, may_be_closing, unanswered_burst, valid_reaction
from app.ai.orchestrator import detect_preferred_language, run_turn
from app.ai.provider.base import LLMProvider, LLMProviderError
from app.ai.replies import reply_text
from app.businesses.models import Business
from app.businesses.service import ai_may_reply
from app.conversations.delivery import DeliveryError, reconcile_echo, send_outbound, undelivered_reply
from app.conversations.locks import conversation_lock
from app.conversations.models import (
    AUDIO_PLACEHOLDER,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_SENT,
    MESSAGE_TYPE_REACTION,
    Conversation,
    Message,
)
from app.conversations.service import add_message, get_or_create_conversation, get_recent_messages
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.security import decrypt_secret
from app.customers.models import Customer
from app.customers.service import get_or_create_customer, profile_fetch_due
from app.instagram.client import MetaAPIError, MetaClient
from app.instagram.models import InstagramAccount
from app.leads.models import Lead
from app.leads.service import apply_qualification, capture_phone_without_ai_turn
from app.notifications.broadcaster import broadcaster
from app.notifications.owner_alerts import alert_hot_lead, alert_owner, customer_label
from app.products.media import resolve_sendable_images
from app.webhooks.models import (
    EVENT_ABANDONED,
    EVENT_FAILED,
    EVENT_PROCESSED,
    EVENT_PROCESSING,
    EVENT_RECEIVED,
    WebhookEvent,
)

logger = logging.getLogger("app.instagram.pipeline")

_settings = get_settings()
# Wait this long after a customer message so one reply covers a burst.
DEBOUNCE_SECONDS = _settings.reply_debounce_seconds
# A claimed event whose worker died is taken over after the lease.
LEASE = dt.timedelta(minutes=_settings.webhook_lease_minutes)
MAX_ATTEMPTS = _settings.webhook_max_attempts
_RETRY_BACKOFF = tuple(_settings.webhook_retry_backoff_seconds)
_ORPHAN_AFTER = dt.timedelta(seconds=_settings.webhook_orphan_after_seconds)
# Each event holds up to two pooled connections; this bounds the burst.
_PROCESS_SLOTS = asyncio.Semaphore(_settings.webhook_concurrency)

# Conversation statuses under which the AI answers. Everything else is a human
# handoff state: messages are recorded for the owner, the AI stays silent.
AI_ACTIVE_STATUSES = ("ai_active", "active")
_HANDOFF_STATUSES = ("human_needed", "human_active")

_AUDIO_PLACEHOLDER = AUDIO_PLACEHOLDER
_VOICE_NOTE_MAX_AGE = dt.timedelta(minutes=_settings.voice_note_max_age_minutes)
_AUDIO_UNREADABLE = "[Ovozli xabar — matnga o'girib bo'lmadi]"
# A message with media but no text is stored with no text: the dashboard shows
# the media itself (or, for a type it can't show, its own localized chip), and
# the model is told what it was (app/ai/context/builder.py). Shared posts and
# Reels carry their caption as the payload's "title" — the only part of them
# anyone can read without opening them — and that's kept as the text.
_CAPTIONED = ("share", "ig_post", "ig_reel", "reel")
_MAX_CAPTION = _settings.caption_max_chars


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_webhook_body(body: dict) -> list[dict[str, Any]]:
    """Meta's webhook body -> one normalized dict per message (reactions, reads and
    deleted messages skipped)."""
    events: list[dict[str, Any]] = []
    for entry in body.get("entry") or []:
        for messaging in entry.get("messaging") or []:
            message = messaging.get("message")
            if not isinstance(message, dict) or message.get("is_deleted") or message.get("is_unsupported"):
                continue
            mid = message.get("mid")
            sender = (messaging.get("sender") or {}).get("id")
            recipient = (messaging.get("recipient") or {}).get("id")
            if not mid or not sender or not recipient:
                continue

            is_echo = bool(message.get("is_echo"))
            text = (message.get("text") or "").strip()
            attachment_type = attachment_url = None
            caption = ""
            attachments = [a for a in (message.get("attachments") or []) if isinstance(a, dict)]
            # A voice note or photo is the part worth acting on; otherwise
            # the first attachment of whatever kind.
            chosen = next((a for a in attachments if a.get("type") in ("audio", "image")), None)
            if chosen is None and attachments:
                chosen = attachments[0]
            if chosen is not None:
                attachment_type = str(chosen.get("type") or "media")[:20]
                payload = chosen.get("payload") if isinstance(chosen.get("payload"), dict) else {}
                attachment_url = payload.get("url")
                if attachment_type in _CAPTIONED:
                    caption = " ".join(str(payload.get("title") or "").split())[:_MAX_CAPTION]

            needs_transcription = attachment_type == "audio" and bool(attachment_url) and not text
            if not text:
                if attachment_type == "audio":
                    text = _AUDIO_PLACEHOLDER  # replaced by the transcript
                else:
                    text = caption
            if not text and not attachment_type:
                continue

            events.append(
                {
                    "kind": "echo" if is_echo else "message",
                    "mid": str(mid),
                    # Echo: we are the sender. Inbound: we are the recipient.
                    "business_ig_id": str(sender if is_echo else recipient),
                    "customer_igsid": str(recipient if is_echo else sender),
                    "text": text,
                    "attachment_type": attachment_type,
                    "attachment_url": attachment_url,
                    "needs_transcription": needs_transcription,
                }
            )
    return events


# ---------------------------------------------------------------------------
# Request-time ingestion
# ---------------------------------------------------------------------------


async def _connected_account(db: AsyncSession, ig_id: str) -> InstagramAccount | None:
    return await db.scalar(
        select(InstagramAccount).where(
            or_(InstagramAccount.ig_business_id == ig_id, InstagramAccount.fb_page_id == ig_id),
            InstagramAccount.status == "connected",
        )
    )


async def ingest_event(db: AsyncSession, event: dict[str, Any]) -> uuid.UUID | None:
    """Persists one parsed event (and, for an inbound message, the message
    itself) and returns the event id to process — or None when there's nothing
    to do (a duplicate delivery, or a recipient we don't serve). Commits."""
    now = dt.datetime.now(dt.timezone.utc)
    event_id = (
        await db.execute(
            insert(WebhookEvent)
            .values(
                id=uuid.uuid4(),
                provider="instagram",
                external_event_id=event["mid"],
                payload=event,
                status=EVENT_RECEIVED,
                created_at=now,
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=[WebhookEvent.external_event_id])
            .returning(WebhookEvent.id)
        )
    ).scalar_one_or_none()

    if event_id is None:
        # Meta redelivered something we already have. Hand it back to a worker
        # only if it isn't finished; the claim makes that safe to do twice.
        existing = await db.scalar(select(WebhookEvent).where(WebhookEvent.external_event_id == event["mid"]))
        await db.commit()
        if existing is None or existing.status in (EVENT_PROCESSED, EVENT_ABANDONED):
            return None
        return existing.id

    webhook_event = await db.get(WebhookEvent, event_id)
    account = await _connected_account(db, event["business_ig_id"])
    if account is None:
        webhook_event.status = EVENT_PROCESSED
        webhook_event.processed_at = now
        webhook_event.last_error = "no connected Instagram account for this recipient"
        await db.commit()
        logger.warning("[pipeline] dropped event %s: no connected account %s", event["mid"], event["business_ig_id"])
        return None

    payload = dict(event, account_id=str(account.id), business_id=str(account.business_id))

    if event["kind"] == "message":
        customer = await get_or_create_customer(db, account.business_id, event["customer_igsid"])
        conversation = await get_or_create_conversation(db, account.business_id, customer.id)
        message = await add_message(
            db,
            conversation,
            sender_type="customer",
            content=event["text"],
            message_type=event["attachment_type"] or "text",
            external_message_id=event["mid"],
            attachment_url=event["attachment_url"],
            attachment_type=event["attachment_type"],
        )
        payload.update(
            customer_id=str(customer.id), conversation_id=str(conversation.id), message_id=str(message.id)
        )

    webhook_event.payload = payload
    await db.commit()

    if event["kind"] == "message":
        await broadcaster.broadcast(
            account.business_id,
            {
                "type": "conversation_updated",
                "conversation_id": payload["conversation_id"],
                "customer_id": payload["customer_id"],
                "last_message": event["text"],
                "sender_type": "customer",
            },
        )
    return event_id


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


async def _claim(db: AsyncSession, event_id: uuid.UUID) -> WebhookEvent | None:
    now = dt.datetime.now(dt.timezone.utc)
    claimed = (
        await db.execute(
            update(WebhookEvent)
            .where(
                WebhookEvent.id == event_id,
                WebhookEvent.attempts < MAX_ATTEMPTS,
                or_(
                    WebhookEvent.status == EVENT_RECEIVED,
                    (WebhookEvent.status == EVENT_FAILED)
                    & or_(WebhookEvent.next_attempt_at.is_(None), WebhookEvent.next_attempt_at <= now),
                    (WebhookEvent.status == EVENT_PROCESSING) & (WebhookEvent.claimed_at < now - LEASE),
                ),
            )
            .values(status=EVENT_PROCESSING, claimed_at=now, attempts=WebhookEvent.attempts + 1)
            .returning(WebhookEvent.id)
        )
    ).scalar_one_or_none()
    await db.commit()
    if claimed is None:
        return None
    return await db.scalar(
        select(WebhookEvent).where(WebhookEvent.id == claimed).execution_options(populate_existing=True)
    )


async def process_event(
    event_id: uuid.UUID,
    *,
    provider: LLMProvider | Callable[[], LLMProvider],
    meta_client: MetaClient,
) -> None:
    """Runs one event in its own session. Never raises: failures are retried with
    backoff, then abandoned with an owner alert."""
    async with _PROCESS_SLOTS, async_session_factory() as db:
        event = await _claim(db, event_id)
        if event is None:
            return  # already done, another worker holds it, or out of attempts
        # Read before anything can expire the object: after a rollback,
        # touching an attribute would try a lazy load, which async SQLAlchemy
        # refuses — and the failure handler itself would crash.
        attempts = event.attempts
        kind = event.payload.get("kind")
        try:
            if kind == "echo":
                await _process_echo(db, event)
            else:
                await _process_customer_message(db, event, provider, meta_client)
        except Exception as exc:  # noqa: BLE001 — recorded and retried, see docstring
            logger.exception("[pipeline] event %s failed (attempt %s)", event_id, attempts)
            try:
                await db.rollback()
                await _record_failure(db, event_id, exc)
            except Exception:  # noqa: BLE001 — never let the handler take the worker down
                logger.exception("[pipeline] could not record the failure of event %s", event_id)
            return

        await db.execute(
            update(WebhookEvent)
            .where(WebhookEvent.id == event_id)
            .values(status=EVENT_PROCESSED, processed_at=dt.datetime.now(dt.timezone.utc), last_error=None)
        )
        await db.commit()


async def _record_failure(db: AsyncSession, event_id: uuid.UUID, exc: Exception) -> None:
    event = await db.get(WebhookEvent, event_id, populate_existing=True)
    now = dt.datetime.now(dt.timezone.utc)
    event.last_error = f"{type(exc).__name__}: {exc}"[:1000]
    permanent = isinstance(exc, DeliveryError) and exc.permanent
    if permanent or event.attempts >= MAX_ATTEMPTS:
        event.status = EVENT_ABANDONED
        event.processed_at = now
        await db.commit()
        await _alert_abandoned(db, event.payload or {}, event.last_error)
    else:
        event.status = EVENT_FAILED
        backoff = _RETRY_BACKOFF[min(event.attempts, len(_RETRY_BACKOFF)) - 1]
        event.next_attempt_at = now + dt.timedelta(seconds=backoff)
        await db.commit()


async def _alert_abandoned(db: AsyncSession, payload: dict, error: str | None) -> None:
    """Out of retries (or retrying can't help): the customer is waiting on an
    answer that isn't coming from the AI. Hand the conversation to a person
    and say so."""
    if not payload.get("conversation_id") or not payload.get("business_id"):
        return
    conversation = await db.get(Conversation, uuid.UUID(payload["conversation_id"]), populate_existing=True)
    business = await db.get(Business, uuid.UUID(payload["business_id"]), populate_existing=True)
    if conversation is None or business is None:
        return
    customer = await db.get(Customer, conversation.customer_id)
    if conversation.status in AI_ACTIVE_STATUSES:
        conversation.status = "human_needed"
        await db.commit()
    await alert_owner(
        db,
        business,
        type="delivery_failed",
        title="AI javob bera olmadi ⚠️",
        message=f"{customer_label(customer)} xabariga AI javobi yuborilmadi: {error or ''}"[:500],
        customer=customer,
        conversation_id=conversation.id,
    )


async def _load(db: AsyncSession, payload: dict) -> tuple[Business, Conversation, Customer, InstagramAccount | None, Message]:
    business = await db.get(Business, uuid.UUID(payload["business_id"]))
    conversation = await db.get(Conversation, uuid.UUID(payload["conversation_id"]))
    customer = await db.get(Customer, uuid.UUID(payload["customer_id"]))
    account = await db.get(InstagramAccount, uuid.UUID(payload["account_id"]))
    message = await db.get(Message, uuid.UUID(payload["message_id"]))
    if account is not None and (
        business is None or account.status != "connected" or account.business_id != business.id
    ):
        account = None
    return business, conversation, customer, account, message


async def _newer_customer_message_exists(db: AsyncSession, conversation: Conversation, message: Message) -> bool:
    newer = await db.scalar(
        select(Message.id)
        .where(
            Message.conversation_id == conversation.id,
            Message.sender_type == "customer",
            Message.created_at > message.created_at,
        )
        .limit(1)
    )
    return newer is not None


def _language(business: Business, texts: list[str]) -> str:
    return detect_preferred_language(business.language, texts)


class _Context:
    """Everything one customer-message event works with. Ids and labels are
    captured up front as plain values, so error handling never depends on ORM
    objects a rollback may have expired."""

    def __init__(self, business, conversation, customer, account, message, access_token, meta_client):
        self.business = business
        self.conversation = conversation
        self.customer = customer
        self.account = account
        self.message = message
        self.access_token = access_token
        self.meta_client = meta_client
        self.business_id = business.id
        self.conversation_id = conversation.id
        self.customer_id = customer.id
        self.message_id = message.id
        self.ig_business_id = account.ig_business_id
        self.recipient_id = customer.ig_scoped_id

    async def reload(self, db: AsyncSession) -> None:
        """Fresh copies of everything, e.g. after a rollback expired them."""
        self.business = await db.get(Business, self.business_id, populate_existing=True)
        self.conversation = await db.get(Conversation, self.conversation_id, populate_existing=True)
        self.customer = await db.get(Customer, self.customer_id, populate_existing=True)
        self.message = await db.get(Message, self.message_id, populate_existing=True)

    async def send(self, db: AsyncSession, messages: list[Message]) -> None:
        await send_outbound(
            db, self.meta_client, ig_business_id=self.ig_business_id, access_token=self.access_token,
            recipient_id=self.recipient_id, messages=messages,
        )

    async def send_new(self, db: AsyncSession, text: str) -> None:
        outbound = await add_message(
            db, self.conversation, sender_type="ai", content=text, delivery_status=DELIVERY_PENDING
        )
        await db.commit()
        await self.send(db, [outbound])


async def _process_customer_message(
    db: AsyncSession,
    event: WebhookEvent,
    provider: LLMProvider | Callable[[], LLMProvider],
    meta_client: MetaClient,
) -> None:
    payload = dict(event.payload)
    business, conversation, customer, account, message = await _load(db, payload)
    if None in (business, conversation, customer, message) or account is None:
        # The conversation was deleted, or Instagram disconnected, since the
        # message arrived — there's nothing left to reply through.
        return
    ctx = _Context(business, conversation, customer, account, message,
                   decrypt_secret(account.access_token_encrypted), meta_client)

    # Who is this? Once, not on every message and page load.
    if profile_fetch_due(customer):
        try:
            profile = await meta_client.get_user_profile(customer.ig_scoped_id, ctx.access_token)
            if profile.get("username"):
                customer.username = str(profile["username"]).lstrip("@")[:255]
            if profile.get("name"):
                customer.name = str(profile["name"])[:255]
        except MetaAPIError as exc:
            logger.info("[pipeline] profile for customer %s unavailable: %s", ctx.customer_id, exc)
        customer.profile_fetched_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()

    if not ai_may_reply(business) or conversation.status not in AI_ACTIVE_STATUSES:
        await db.commit()
        async with conversation_lock(ctx.conversation_id):
            await db.refresh(conversation)
            await db.refresh(business)
            # A retry of an event whose turn handed off (or whose handoff line
            # or phone acknowledgement failed to send) lands here: the
            # conversation is no longer AI-active, but the line is still owed.
            await _resend_undelivered(db, ctx)
            await _without_ai(db, ctx)
        return

    # Debounce: if they're still typing, the newest message's event replies.
    if DEBOUNCE_SECONDS > 0:
        await asyncio.sleep(DEBOUNCE_SECONDS)
    if await _newer_customer_message_exists(db, conversation, message):
        return
    # End the transaction before waiting: a waiter should hold one pooled
    # connection (the lock's), not two.
    await db.commit()

    async with conversation_lock(ctx.conversation_id):
        await db.refresh(conversation)
        await db.refresh(business)
        if await _newer_customer_message_exists(db, conversation, message):
            return

        # A previous attempt may have written this message's reply without
        # getting it out: resend exactly that, never a new one.
        await _resend_undelivered(db, ctx)
        answered = conversation.last_answered_customer_message_at
        if answered is not None and answered >= message.created_at:
            return  # a delivered turn already covered this message

        if not ai_may_reply(business) or conversation.status not in AI_ACTIVE_STATUSES:
            # The owner took over (or switched AI off) during the debounce.
            await _without_ai(db, ctx)
            return

        unreadable = await _transcribe_pending_voice_notes(db, conversation)
        if unreadable:
            await _hand_off(
                db, ctx, alert_type="handoff", title="Ovozli xabar — operator kerak 🎙️",
                reason=f"{customer_label(customer)} ovozli xabar yubordi, uni matnga o'girib bo'lmadi.",
            )
            return

        reason = await limits.limit_reason(db, ctx.business_id, ctx.conversation_id)
        if reason is not None:
            await _hand_off(db, ctx, alert_type="ai_limit", title="AI limiti ⏸️", reason=reason)
            return

        try:
            resolved = provider if isinstance(provider, LLMProvider) else provider()
        except LLMProviderError as exc:
            await _hand_off(
                db, ctx, alert_type="ai_limit", title="AI sozlanmagan ⚠️",
                reason=f"AI provayderi ishlamayapti: {exc}",
            )
            return

        if await _closes_the_conversation(db, ctx, resolved):
            return
        await _run_ai_turn(db, ctx, resolved)


async def _closes_the_conversation(db: AsyncSession, ctx: _Context, provider: LLMProvider) -> bool:
    """The customer's burst just ends the conversation ("hop", 👍 after the
    goodbye): no reply — at most a reaction on their message, as the model
    decides (app/ai/closing.py). Returns whether that's what happened."""
    recent = await get_recent_messages(db, ctx.conversation_id, limit=12)
    burst, last_outbound = unanswered_burst(recent)
    if not may_be_closing(burst, last_outbound):
        return False
    try:
        decision = await decide_closing(provider, recent, business_id=ctx.business_id)
    except LLMProviderError as exc:
        # Can't tell whether it's over, so it's treated as not over: the
        # customer gets a normal reply.
        logger.warning("[pipeline] closing decision unavailable for conversation %s: %s", ctx.conversation_id, exc)
        return False
    if not decision.conversation_finished:
        return False

    ctx.conversation.last_answered_customer_message_at = ctx.message.created_at
    emoji = valid_reaction(decision.reaction)
    if emoji is None or not ctx.message.external_message_id:
        await db.commit()
        logger.info("[pipeline] conversation %s closed by the customer; no reaction", ctx.conversation_id)
        return True

    # Recorded before it's sent, like every outbound message, so the owner
    # sees the conversation was answered (and whether the reaction got out).
    reaction = await add_message(
        db, ctx.conversation, sender_type="ai", content=emoji,
        message_type=MESSAGE_TYPE_REACTION, delivery_status=DELIVERY_PENDING,
    )
    await db.commit()
    try:
        await ctx.meta_client.send_reaction(
            access_token=ctx.access_token, recipient_id=ctx.recipient_id,
            message_id=ctx.message.external_message_id, emoji=emoji,
        )
        reaction.delivery_status = DELIVERY_SENT
    except MetaAPIError as exc:
        # Only a courtesy: the conversation is closed either way. The row
        # shows the owner it didn't go out.
        logger.warning("[pipeline] reaction not sent in conversation %s: %s", ctx.conversation_id, exc)
        reaction.delivery_status = DELIVERY_FAILED
        reaction.delivery_error = str(exc)[:500]
    await db.commit()
    await broadcaster.broadcast(
        ctx.business_id,
        {
            "type": "conversation_updated",
            "conversation_id": str(ctx.conversation_id),
            "customer_id": str(ctx.customer_id),
            "last_message": emoji,
            "sender_type": "ai",
        },
    )
    logger.info("[pipeline] conversation %s closed by the customer; reaction %s", ctx.conversation_id, emoji)
    return True


async def _resend_undelivered(db: AsyncSession, ctx: _Context) -> None:
    """Resends this message's unsent AI reply, unless AI replies aren't allowed or a
    person has taken over."""
    if not ai_may_reply(ctx.business) or ctx.conversation.status not in (*AI_ACTIVE_STATUSES, "human_needed"):
        return
    pending = await undelivered_reply(db, ctx.conversation, ctx.message)
    if pending:
        await ctx.send(db, pending)


async def _transcribe_pending_voice_notes(db: AsyncSession, conversation: Conversation) -> bool:
    """Transcribes this burst's recent untranscribed voice notes. True if any failed."""
    since = dt.datetime.now(dt.timezone.utc) - _VOICE_NOTE_MAX_AGE
    if conversation.last_answered_customer_message_at is not None:
        since = max(since, conversation.last_answered_customer_message_at)
    pending = (
        await db.execute(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.sender_type == "customer",
                Message.attachment_type == "audio",
                Message.content == _AUDIO_PLACEHOLDER,
                Message.attachment_url.is_not(None),
                Message.created_at > since,
            ).order_by(Message.created_at.asc())
        )
    ).scalars().all()
    unreadable = False
    for note in pending:
        try:
            note.content = await transcribe_audio_url(note.attachment_url, business_id=conversation.business_id)
        except TranscriptionError as exc:
            logger.warning("[pipeline] voice note %s not transcribed: %s", note.id, exc)
            note.content = _AUDIO_UNREADABLE
            unreadable = True
        await db.commit()
    return unreadable


async def _without_ai(db: AsyncSession, ctx: _Context) -> None:
    """No AI turn: still capture a phone number, alert the owner, and during a
    handoff acknowledge the number."""
    await db.refresh(ctx.conversation)
    lead, notify = await capture_phone_without_ai_turn(
        db, ctx.business_id, ctx.customer_id, ctx.conversation_id, ctx.message.content
    )
    if lead is None or not notify:
        return
    await alert_hot_lead(db, ctx.business, ctx.customer, lead)
    await ctx.reload(db)
    if ai_may_reply(ctx.business) and ctx.conversation.status in _HANDOFF_STATUSES:
        recent = await get_recent_messages(db, ctx.conversation_id, limit=4)
        lang = _language(ctx.business, [m.content for m in recent if m.content])
        await ctx.send_new(db, reply_text(ctx.business, "phone_received", lang))


async def _hand_off(db: AsyncSession, ctx: _Context, *, alert_type: str, title: str, reason: str) -> None:
    """The AI can't take this turn: move the conversation to a human, tell the
    owner why (first, so a failed send can't lose the alert), then tell the
    customer a person will follow up — no LLM involved."""
    ctx.conversation.status = "human_needed"
    await db.commit()
    await alert_owner(
        db, ctx.business, type=alert_type, title=title, message=reason,
        customer=ctx.customer, conversation_id=ctx.conversation_id,
    )
    await ctx.reload(db)
    recent = await get_recent_messages(db, ctx.conversation_id, limit=4)
    lang = _language(ctx.business, [m.content for m in recent if m.content])
    await ctx.send_new(db, reply_text(ctx.business, "handoff", lang))


async def _run_ai_turn(db: AsyncSession, ctx: _Context, provider: LLMProvider) -> None:
    turn_state: dict = {}
    try:
        result = await run_turn(
            db, provider, ctx.business, ctx.conversation,
            escalation_state_out=turn_state, deliver=lambda messages: ctx.send(db, messages), outbound=True,
        )
    except Exception:
        # If the reply (with its "an operator will contact you") was already
        # recorded, the retry will find this message answered and stop — so
        # the owner has to be told now or never.
        await db.rollback()
        await ctx.reload(db)
        answered = ctx.conversation.last_answered_customer_message_at
        if turn_state.get("escalated") and answered is not None and answered >= ctx.message.created_at:
            await alert_owner(
                db, ctx.business, type="handoff", title="Operator kerak 🙋",
                message=f"{customer_label(ctx.customer)}: {turn_state.get('reason') or 'AI suhbatni operatorga topshirdi.'}"[:500],
                customer=ctx.customer, conversation_id=ctx.conversation_id,
            )
        raise
    delivery_error = turn_state.get("delivery_error")
    raw_text = ctx.message.content

    # Bookkeeping for the turn happens whether or not the reply got out — the
    # analysis is real either way, and a retry that finds the reply recorded
    # resends it without running this again. None of it may fail the event.
    lead_id, became_hot = None, False
    if turn_state.get("analysis_failed"):
        logger.warning("[pipeline] analysis unavailable for conversation %s — lead left as it was", ctx.conversation_id)
    else:
        try:
            lead, became_hot = await apply_qualification(
                db, ctx.business_id, ctx.customer_id, ctx.conversation_id, result, raw_message_text=raw_text
            )
            lead_id = lead.id
        except Exception:  # noqa: BLE001
            logger.exception("[pipeline] lead qualification failed for conversation %s", ctx.conversation_id)
            await db.rollback()
            await ctx.reload(db)

    if turn_state.get("escalated"):
        await alert_owner(
            db,
            ctx.business,
            type="handoff",
            title="Operator kerak 🙋",
            message=f"{customer_label(ctx.customer)}: {turn_state.get('reason') or 'AI suhbatni operatorga topshirdi.'}"[:500],
            customer=ctx.customer,
            conversation_id=ctx.conversation_id,
            lead_id=lead_id,
        )
        await ctx.reload(db)
    if became_hot and lead_id is not None:
        lead = await db.get(Lead, lead_id, populate_existing=True)
        await alert_hot_lead(db, ctx.business, ctx.customer, lead)
        await ctx.reload(db)

    if delivery_error is not None:
        # The reply is recorded; this event fails and its retry resends it.
        raise delivery_error

    try:
        await _send_product_photos(db, ctx, result)
    except DeliveryError as exc:
        logger.warning("[pipeline] product photo not delivered in conversation %s: %s", ctx.conversation_id, exc)
    except Exception:  # noqa: BLE001
        logger.exception("[pipeline] product photos failed in conversation %s", ctx.conversation_id)
        await db.rollback()


async def _send_product_photos(db: AsyncSession, ctx: _Context, result) -> None:
    if not result.image_product_ids:
        return
    photos = await resolve_sendable_images(db, ctx.business_id, result.image_product_ids)
    if not photos:
        return
    outbound = [
        await add_message(
            db, ctx.conversation, sender_type="ai", content=f"📷 {name}", message_type="image",
            attachment_url=url, attachment_type="image", delivery_status=DELIVERY_PENDING,
        )
        for name, url in photos
    ]
    await db.commit()
    await ctx.send(db, outbound)


async def _process_echo(db: AsyncSession, event: WebhookEvent) -> None:
    """A message sent from the business account: ours (known id, or matched to an
    unsent outbound row), or a person replying from the app (AI steps back)."""
    payload = event.payload
    business = await db.get(Business, uuid.UUID(payload["business_id"]))
    if business is None:
        return
    if await db.scalar(select(Message.id).where(Message.external_message_id == payload["mid"])) is not None:
        return
    customer = await get_or_create_customer(db, business.id, payload["customer_igsid"])
    conversation = await get_or_create_conversation(db, business.id, customer.id)
    await db.commit()

    if await reconcile_echo(
        db, conversation, external_message_id=payload["mid"], text=payload["text"],
        attachment_type=payload.get("attachment_type"),
    ):
        return

    await db.refresh(conversation)
    recorded = await add_message(
        db,
        conversation,
        sender_type="human",
        content=payload["text"],
        message_type=payload.get("attachment_type") or "text",
        external_message_id=payload["mid"],
        attachment_url=payload.get("attachment_url"),
        attachment_type=payload.get("attachment_type"),
        delivery_status=DELIVERY_SENT,
    )
    if recorded.sender_type != "human":
        # Our own send recorded this id between the checks above (add_message
        # is idempotent on the id and handed back that row): not a takeover.
        await db.commit()
        return
    took_over = conversation.status in (*AI_ACTIVE_STATUSES, "human_needed")
    if took_over:
        conversation.status = "human_active"
    await db.commit()

    await broadcaster.broadcast(
        business.id,
        {
            "type": "conversation_updated",
            "conversation_id": str(conversation.id),
            "customer_id": str(customer.id),
            "last_message": payload["text"],
            "sender_type": "human",
        },
    )
    if took_over:
        await alert_owner(
            db,
            business,
            type="handoff",
            title="AI to'xtatildi — Instagram'dan javob yozildi",
            message=(
                f"{customer_label(customer)} bilan suhbatga Instagram ilovasidan javob yozildi, AI bu suhbatda "
                "to'xtadi. AI'ni qaytarish uchun suhbat holatini «AI faol» qiling."
            ),
            customer=customer,
            conversation_id=conversation.id,
        )


# ---------------------------------------------------------------------------
# Sweeper
# ---------------------------------------------------------------------------


async def due_event_ids(db: AsyncSession, limit: int = 20) -> list[uuid.UUID]:
    now = dt.datetime.now(dt.timezone.utc)
    rows = await db.execute(
        select(WebhookEvent.id)
        .where(
            WebhookEvent.provider == "instagram",
            WebhookEvent.attempts < MAX_ATTEMPTS,
            or_(
                (WebhookEvent.status == EVENT_RECEIVED) & (WebhookEvent.created_at < now - _ORPHAN_AFTER),
                (WebhookEvent.status == EVENT_FAILED) & (WebhookEvent.next_attempt_at <= now),
                (WebhookEvent.status == EVENT_PROCESSING) & (WebhookEvent.claimed_at < now - LEASE),
            ),
        )
        .order_by(WebhookEvent.created_at.asc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def _abandon_dead_leases(db: AsyncSession) -> None:
    """Events whose last attempt died mid-turn (lease expired) with no
    attempts left: abandoned, and the owner told — never retried forever."""
    now = dt.datetime.now(dt.timezone.utc)
    rows = (
        await db.execute(
            update(WebhookEvent)
            .where(
                WebhookEvent.provider == "instagram",
                WebhookEvent.status == EVENT_PROCESSING,
                WebhookEvent.claimed_at < now - LEASE,
                WebhookEvent.attempts >= MAX_ATTEMPTS,
            )
            .values(status=EVENT_ABANDONED, processed_at=now, last_error="worker died on the last attempt")
            .returning(WebhookEvent.payload)
        )
    ).scalars().all()
    await db.commit()
    for payload in rows:
        try:
            await _alert_abandoned(db, payload or {}, "worker died on the last attempt")
        except Exception:  # noqa: BLE001 — one failed alert must not stop the sweep
            logger.exception("[pipeline] abandoned-event alert failed")
            await db.rollback()


async def sweep_events(
    provider: LLMProvider | Callable[[], LLMProvider], meta_client: MetaClient
) -> int:
    """Re-drives unfinished events concurrently (bounded by _PROCESS_SLOTS);
    returns how many it attempted."""
    async with async_session_factory() as db:
        await _abandon_dead_leases(db)
        ids = await due_event_ids(db)
    await asyncio.gather(*(process_event(i, provider=provider, meta_client=meta_client) for i in ids))
    return len(ids)
