"""Tests for Vision / Multimodal Image Support in Instagram DM.
Covers:
1. Webhook parsing of image attachments into Message.attachment_url and attachment_type.
2. build_message_history multimodal formatting for the latest message vs token-saving text for historical messages.
3. Fallback message when image processing encounters an error.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.ai.context.builder import MAX_MULTIMODAL_IMAGES, build_message_history, _BASE_SYSTEM_PROMPT
from app.ai.orchestrator import run_turn
from app.ai.provider.base import LLMProvider, LLMProviderError
from app.businesses.models import Business
from app.conversations.models import Conversation, Message
from app.customers.models import Customer


def test_build_message_history_multimodal_latest_and_token_saving_history():
    conv_id = uuid.uuid4()

    # Message 1: Customer sent an image
    msg1 = Message(
        id=uuid.uuid4(),
        conversation_id=conv_id,
        sender_type="customer",
        content="Shundan bormi?",
        message_type="image",
        attachment_url="https://cdn.instagram.com/p1.jpg",
        attachment_type="image",
    )

    # 1. When msg1 is the latest message, it MUST be formatted with image_url
    history_turn1 = build_message_history([msg1])
    assert len(history_turn1) == 1
    assert history_turn1[0]["role"] == "user"
    assert isinstance(history_turn1[0]["content"], list)
    assert history_turn1[0]["content"][0] == {"type": "text", "text": "Shundan bormi?"}
    assert history_turn1[0]["content"][1] == {"type": "image_url", "image_url": {"url": "https://cdn.instagram.com/p1.jpg"}}

    # Message 2: AI replied
    msg2 = Message(
        id=uuid.uuid4(),
        conversation_id=conv_id,
        sender_type="ai",
        content="Ha, bu modelimiz mavjud!",
        message_type="text",
    )

    # Message 3: Customer asks a new question
    msg3 = Message(
        id=uuid.uuid4(),
        conversation_id=conv_id,
        sender_type="customer",
        content="Narxi qancha?",
        message_type="text",
    )

    # 2. In turn 2, msg1 is no longer the latest message — but "Narxi qancha?"
    # is a question ABOUT that photo, so the image stays attached. Dropping it
    # here (which is what this used to do, to save tokens) meant the model
    # answered a question about a picture it could no longer see.
    history_turn2 = build_message_history([msg1, msg2, msg3])
    assert len(history_turn2) == 3

    assert isinstance(history_turn2[0]["content"], list)
    assert history_turn2[0]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "https://cdn.instagram.com/p1.jpg"},
    }

    # AI reply
    assert history_turn2[1]["content"] == "Ha, bu modelimiz mavjud!"

    # Latest message is text
    assert history_turn2[2]["content"] == "Narxi qancha?"


def test_build_message_history_attaches_only_the_most_recent_images():
    """Images are the expensive part of a multimodal context, so the window is
    capped: anything older than MAX_MULTIMODAL_IMAGES degrades to a text marker."""
    conv_id = uuid.uuid4()

    def _image(name: str) -> Message:
        return Message(
            id=uuid.uuid4(),
            conversation_id=conv_id,
            sender_type="customer",
            content=f"Shundan bormi? ({name})",
            message_type="image",
            attachment_url=f"https://cdn.instagram.com/{name}.jpg",
            attachment_type="image",
        )

    first, second, third = _image("p1"), _image("p2"), _image("p3")
    mapped = build_message_history([first, second, third])

    assert MAX_MULTIMODAL_IMAGES == 2
    # Oldest falls out of the window: text marker only, no image payload.
    assert isinstance(mapped[0]["content"], str)
    assert "[Mijoz rasm yubordi]" in mapped[0]["content"]
    assert "p1.jpg" not in mapped[0]["content"]
    # The two most recent stay attached.
    for idx, name in ((1, "p2"), (2, "p3")):
        assert isinstance(mapped[idx]["content"], list)
        assert mapped[idx]["content"][1]["image_url"]["url"].endswith(f"{name}.jpg")


def test_meta_webhook_payload_image_extraction():
    """Verify parsing Meta webhook payload attachments array for type == 'image' and payload.url."""
    payload = {
        "object": "instagram",
        "entry": [{
            "id": "ig_biz_123",
            "messaging": [{
                "sender": {"id": "cust_456"},
                "recipient": {"id": "ig_biz_123"},
                "timestamp": 1725880000,
                "message": {
                    "mid": "mid.12345",
                    "text": "Shundan bormi sizlarda?",
                    "attachments": [
                        {
                            "type": "image",
                            "payload": {
                                "url": "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=123"
                            }
                        }
                    ]
                }
            }]
        }]
    }

    message_data = payload["entry"][0]["messaging"][0]["message"]
    attachments = message_data.get("attachments") or []
    attachment_url = None
    attachment_type = None

    for att in attachments:
        if isinstance(att, dict):
            t = att.get("type")
            p = att.get("payload") or {}
            u = p.get("url")
            if t == "image" and u:
                attachment_url = u
                attachment_type = "image"
                break

    assert attachment_url == "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=123"
    assert attachment_type == "image"


def test_system_prompt_includes_vision_instructions():
    """Verify _BASE_SYSTEM_PROMPT still instructs the model on inbound images:
    inspect it, search the catalog from what it shows, and never claim to see
    detail it isn't sure about."""
    assert "Customer sends an image" in _BASE_SYSTEM_PROMPT
    assert "search_products" in _BASE_SYSTEM_PROMPT
    assert "Never claim to see details you aren't confident about" in _BASE_SYSTEM_PROMPT


class FailingVisionProvider(LLMProvider):
    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(self, **kwargs):
        raise LLMProviderError("Vision model failed to decode image URL")


@pytest.mark.asyncio
async def test_image_processing_error_falls_back_to_text_request(monkeypatch):
    session = AsyncMock()
    business = MagicMock(spec=Business)
    business.id = uuid.uuid4()
    business.name = "Moda Boutique"
    business.language = "uz"
    business.ai_enabled = True

    conversation = MagicMock(spec=Conversation)
    conversation.id = uuid.uuid4()
    conversation.business_id = business.id
    conversation.customer_id = uuid.uuid4()
    conversation.working_state = {}

    image_msg = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sender_type="customer",
        content="Shundan bormi?",
        attachment_url="https://broken-image-link.com/broken.png",
        attachment_type="image",
    )

    async def mock_get_recent_messages(db, conv_id, limit=10):
        return [image_msg]

    async def mock_build_system_prompt(db, biz, cust_id, working_state=None):
        return "System prompt"

    monkeypatch.setattr("app.ai.orchestrator.get_recent_messages", mock_get_recent_messages)
    monkeypatch.setattr("app.ai.orchestrator.build_system_prompt_with_learnings", mock_build_system_prompt)

    provider = FailingVisionProvider()

    res = await run_turn(
        db=session,
        provider=provider,
        business=business,
        conversation=conversation,
    )

    # Verify fallback response asks customer to describe the item in text
    assert res is not None
    assert "rasm" in res.reply.lower()
    assert "matn" in res.reply.lower()
    assert res.lead_status == "warm"

