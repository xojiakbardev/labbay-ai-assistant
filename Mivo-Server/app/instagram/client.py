"""Instagram API (Instagram Login) client.

Error messages built here are shown to owners and written to logs, so they are
assembled from the HTTP status and Meta's error body only — never from the
exception string, which for httpx includes the request URL and, for the
endpoints that take `access_token` as a query parameter, the plaintext token.
"""
import datetime as dt
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.core.config import get_settings

_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
_LONG_LIVED_TOKEN_URL = "https://graph.instagram.com/access_token"
_REFRESH_TOKEN_URL = "https://graph.instagram.com/refresh_access_token"
_GRAPH_BASE = f"https://graph.instagram.com/{get_settings().instagram_graph_version}"
_SCOPES = "instagram_business_basic,instagram_business_manage_messages"


class RefreshedToken(BaseModel):
    access_token: str
    expires_at: dt.datetime


class ConnectedAccount(BaseModel):
    access_token: str
    # The professional account's Instagram id (/me `user_id`, 1784…) — the id
    # webhooks carry as recipient/sender, so the one messages are routed by.
    ig_business_id: str
    ig_username: str | None
    # The app-scoped id (/me `id`). Also matched when routing, never relied on.
    fb_page_id: str
    expires_at: dt.datetime


class MetaAPIError(Exception):
    """`permanent` marks failures a retry can't fix: the 24-hour messaging
    window closed, the recipient can't be messaged, the token is dead."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


# Error code/subcodes after which resending is pointless.
_PERMANENT_CODES = {10, 190, 200, 551}
_PERMANENT_SUBCODES = {2534014, 2018001, 2018278, 1545041}


def _is_permanent(exc: httpx.HTTPError) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    code, subcode, _ = _meta_error_fields(exc.response)
    return code in _PERMANENT_CODES or subcode in _PERMANENT_SUBCODES


def _meta_error_fields(response: httpx.Response) -> tuple[int | None, int | None, str]:
    try:
        body = response.json()
    except ValueError:
        return None, None, response.text[:200]
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return None, None, response.text[:200]
    return err.get("code"), err.get("error_subcode"), str(err.get("message", ""))[:300]


def _format_meta_error(exc: httpx.HTTPError, action: str = "xabar yuborish") -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code, subcode, message = _meta_error_fields(exc.response)
        if subcode == 2534014:
            return (
                "Instagram foydalanuvchisi topilmadi yoki 24 soatlik xabarlashuv oynasi tugagan. "
                "Meta qoidasiga ko'ra: mijoz sizga oxirgi 24 soat ichida yozgan bo'lishi kerak "
                "(va agar Meta App 'Development' rejimida bo'lsa, mijoz hisobi ilovaga 'Instagram Tester' sifatida qo'shilgan bo'lishi shart)."
            )
        if subcode == 2018001 or code == 10:
            return (
                "Meta 24 soatlik xabarlashuv chegarasi (Messaging Window) o'tib ketgan. "
                "Mijoz yangitdan xabar yozmaguncha unga xabar yuborib bo'lmaydi."
            )
        if code == 190:
            return (
                "Instagram ulanish tokenining amal qilish muddati tugagan. "
                "Iltimos, Sozlamalar bo'limidan Instagram hisobingizni qayta ulang."
            )
        return f"Meta xatoligi (HTTP {exc.response.status_code}, {code or 'N/A'}:{subcode or 'N/A'}): {message}"
    return f"Instagram orqali {action}da tarmoq xatoligi ({type(exc).__name__})."


def _describe(exc: httpx.HTTPError, what: str) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code, subcode, message = _meta_error_fields(exc.response)
        return f"{what} failed: HTTP {exc.response.status_code} ({code}:{subcode}) {message}"
    return f"{what} failed: {type(exc).__name__}"


class MetaClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._app_id = settings.meta_app_id
        self._app_secret = settings.meta_app_secret
        self._redirect_uri = settings.meta_redirect_uri
        self._timeout = 10.0

    def build_oauth_url(self, state: str) -> str:
        params = urlencode({
            "client_id": self._app_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": _SCOPES,
            "state": state,
        })
        return f"{_AUTHORIZE_URL}?{params}"

    async def exchange_code_for_account(self, code: str) -> ConnectedAccount:
        """code -> short-lived token -> long-lived token -> account.

        Every step must succeed. The long-lived exchange used to be
        "best-effort", falling back to the one-hour token: the account then
        showed as connected, expired an hour later, and the refresh sweep can't
        refresh a short-lived token — every reply after that failed silently.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                short_lived = await client.post(
                    _TOKEN_URL,
                    data={
                        "client_id": self._app_id,
                        "client_secret": self._app_secret,
                        "grant_type": "authorization_code",
                        "redirect_uri": self._redirect_uri,
                        "code": code,
                    },
                )
                short_lived.raise_for_status()
                short_lived_data = short_lived.json()

                long_lived = await client.get(
                    _LONG_LIVED_TOKEN_URL,
                    params={
                        "grant_type": "ig_exchange_token",
                        "client_secret": self._app_secret,
                        "access_token": short_lived_data["access_token"],
                    },
                )
                long_lived.raise_for_status()
                long_lived_data = long_lived.json()

                me = await client.get(
                    f"{_GRAPH_BASE}/me",
                    params={"fields": "id,user_id,username,name"},
                    headers={"Authorization": f"Bearer {long_lived_data['access_token']}"},
                )
                me.raise_for_status()
                me_data = me.json()
            except httpx.HTTPError as exc:
                raise MetaAPIError(_describe(exc, "Instagram connect")) from exc
            except (KeyError, ValueError) as exc:
                raise MetaAPIError(f"Instagram connect returned an unexpected response ({exc!r})") from exc

        # Two different ids: `id` is scoped to this app, `user_id` is the
        # professional account's id — and webhooks name the account by
        # `user_id`. Routing by `id` matched no incoming message at all.
        ig_user_id = str(me_data.get("user_id") or "")
        app_scoped_id = str(me_data.get("id") or "")
        if not ig_user_id or not app_scoped_id:
            raise MetaAPIError("Instagram connect: /me returned no account id")
        if not long_lived_data.get("expires_in"):
            raise MetaAPIError("Instagram connect: the long-lived token came back without an expiry")
        expires_in = int(long_lived_data["expires_in"])
        return ConnectedAccount(
            access_token=long_lived_data["access_token"],
            ig_business_id=ig_user_id,
            ig_username=me_data.get("username"),
            fb_page_id=app_scoped_id,
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=expires_in),
        )

    async def refresh_long_lived_token(self, access_token: str) -> RefreshedToken:
        """Resets a long-lived token's 60-day clock without the owner having
        to reconnect — Meta requires the token be >=24h old and not yet
        expired (app/instagram/service.py calls this from a background sweep
        well before expiry, so both hold in practice)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(
                    _REFRESH_TOKEN_URL,
                    params={"grant_type": "ig_refresh_token", "access_token": access_token},
                )
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as exc:
                raise MetaAPIError(_describe(exc, "Instagram token refresh")) from exc
            except ValueError as exc:
                raise MetaAPIError("Instagram token refresh returned a non-JSON response") from exc

        if not data.get("access_token") or not data.get("expires_in"):
            raise MetaAPIError("Instagram token refresh returned no token or expiry")
        expires_in = int(data["expires_in"])
        return RefreshedToken(
            access_token=data["access_token"],
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=expires_in),
        )

    async def _post_message(self, access_token: str, payload: dict, action: str) -> dict:
        """Returns Meta's response body. A 2xx means Meta accepted the message;
        if its body isn't JSON the send still happened, so it returns {} (no
        message id) rather than raising and getting the message sent twice —
        the echo webhook then supplies the id (app/instagram/pipeline.py)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    f"{_GRAPH_BASE}/me/messages",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=payload,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise MetaAPIError(_format_meta_error(exc, action=action), permanent=_is_permanent(exc)) from exc
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    async def send_message(self, *, ig_business_id: str, access_token: str, recipient_id: str, text: str) -> dict:
        return await self._post_message(
            access_token, {"recipient": {"id": recipient_id}, "message": {"text": text}}, "xabar yuborish"
        )

    async def send_image(self, *, ig_business_id: str, access_token: str, recipient_id: str, image_url: str) -> dict:
        """Instagram fetches the URL itself and re-hosts it, so image_url must
        be publicly reachable (an R2-uploaded product photo, or any other
        public image URL)."""
        return await self._post_message(
            access_token,
            {
                "recipient": {"id": recipient_id},
                "message": {"attachment": {"type": "image", "payload": {"url": image_url}}},
            },
            "rasm yuborish",
        )

    async def send_reaction(self, *, access_token: str, recipient_id: str, message_id: str, emoji: str) -> None:
        """Reacts to one of the customer's messages with an emoji — no new
        message in the thread."""
        await self._post_message(
            access_token,
            {
                "recipient": {"id": recipient_id},
                "sender_action": "react",
                "payload": {"message_id": message_id, "reaction": emoji},
            },
            "reaksiya qoldirish",
        )

    async def send_sender_action(self, *, access_token: str, recipient_id: str, action: str) -> None:
        """"mark_seen" or "typing_on": what a person on the other end would
        show while reading and writing."""
        await self._post_message(
            access_token, {"recipient": {"id": recipient_id}, "sender_action": action}, action
        )

    async def get_user_profile(self, user_id: str, access_token: str) -> dict:
        """Profile (username, name) for a customer's IGSID. Raises
        MetaAPIError when Meta won't return it (privacy settings, deleted
        account, missing permission) — the caller records the attempt so it
        isn't repeated on every message."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(
                    f"{_GRAPH_BASE}/{user_id}",
                    params={"fields": "username,name"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
                body = response.json()
            except httpx.HTTPError as exc:
                raise MetaAPIError(_describe(exc, "Instagram profile lookup")) from exc
            except ValueError as exc:
                raise MetaAPIError("Instagram profile lookup returned a non-JSON response") from exc
        if not isinstance(body, dict):
            raise MetaAPIError("Instagram profile lookup returned an unexpected response")
        return body

    async def deauthorize_account(self, access_token: str) -> None:
        """Revokes the app's permissions on the account. Raises MetaAPIError
        if Meta refuses (e.g. the token is already dead)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.delete(
                    f"{_GRAPH_BASE}/me/permissions",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise MetaAPIError(_describe(exc, "Instagram deauthorization")) from exc
