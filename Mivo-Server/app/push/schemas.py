from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator
from app.core.config import get_settings

# The server POSTs to whatever endpoint a subscription names, so it must be a
# real browser push service — anything else turns /push/test into a way to
# make the server call internal addresses (SSRF).
_PUSH_SERVICE_HOSTS = tuple(get_settings().push_service_hosts)


def _is_push_service(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.port not in (None, 443):
        return False
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in _PUSH_SERVICE_HOSTS)


class VapidPublicKeyOut(BaseModel):
    # Empty when push isn't configured on this server.
    public_key: str


class PushKeysIn(BaseModel):
    p256dh: str = Field(max_length=256)
    auth: str = Field(max_length=128)


class PushSubscribeIn(BaseModel):
    endpoint: str = Field(max_length=2048)
    keys: PushKeysIn
    user_agent: str | None = Field(default=None, max_length=255)

    @field_validator("endpoint")
    @classmethod
    def _push_service_only(cls, value: str) -> str:
        if not _is_push_service(value):
            raise ValueError("endpoint must be an https URL of a browser push service")
        return value


class PushUnsubscribeIn(BaseModel):
    endpoint: str = Field(max_length=2048)


class PushStatusOut(BaseModel):
    subscribed: bool
    devices_count: int


class PushTestOut(BaseModel):
    sent_count: int
