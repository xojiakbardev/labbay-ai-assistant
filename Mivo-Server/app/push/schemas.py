from pydantic import BaseModel, Field


class VapidPublicKeyOut(BaseModel):
    public_key: str


class PushKeysIn(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeIn(BaseModel):
    endpoint: str
    keys: PushKeysIn
    user_agent: str | None = None


class PushUnsubscribeIn(BaseModel):
    endpoint: str


class PushStatusOut(BaseModel):
    subscribed: bool
    devices_count: int


class PushTestOut(BaseModel):
    sent_count: int
