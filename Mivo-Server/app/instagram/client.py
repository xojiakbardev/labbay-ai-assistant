import datetime as dt
import httpx
from pydantic import BaseModel
from app.core.config import get_settings

_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
_LONG_LIVED_TOKEN_URL = "https://graph.instagram.com/access_token"
_REFRESH_TOKEN_URL = "https://graph.instagram.com/refresh_access_token"
_GRAPH_BASE = "https://graph.instagram.com/v21.0"
_SCOPES = "instagram_business_basic,instagram_business_manage_messages"


class RefreshedToken(BaseModel):
    access_token: str
    expires_at: dt.datetime


class ConnectedAccount(BaseModel):
    access_token: str
    ig_business_id: str
    ig_username: str | None
    fb_page_id: str
    expires_at: dt.datetime


class MetaAPIError(Exception):
    pass


def _format_meta_error(exc: httpx.HTTPError, action: str = "xabar yuborish") -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = None
        subcode = None
        message = ""
        try:
            body = exc.response.json()
            if isinstance(body, dict) and "error" in body:
                err = body["error"]
                code = err.get("code")
                subcode = err.get("error_subcode")
                message = err.get("message", "")
        except Exception:
            pass

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
        if message:
            return f"Meta xatoligi ({code or 'N/A'}:{subcode or 'N/A'}): {message}"

    return f"Instagram orqali {action}da xatolik yuz berdi: {exc}"


class MetaClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._app_id = settings.meta_app_id
        self._app_secret = settings.meta_app_secret
        self._redirect_uri = settings.meta_redirect_uri
        self._timeout = 10.0

    def build_oauth_url(self, state: str) -> str:
        from urllib.parse import urlencode
        params = urlencode({
            "client_id": self._app_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": _SCOPES,
            "state": state,
        })
        return f"{_AUTHORIZE_URL}?{params}"

    async def exchange_code_for_account(self, code: str) -> ConnectedAccount:
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

            except httpx.HTTPStatusError as exc:
                raise MetaAPIError(
                    f"Instagram connect failed (short-lived token): {exc} | body={exc.response.text}"
                ) from exc
            except httpx.HTTPError as exc:
                raise MetaAPIError(f"Instagram connect failed (short-lived token): {exc}") from exc

            access_token = short_lived_data["access_token"]
            expires_in = 3600

            try:
                long_lived = await client.get(
                    _LONG_LIVED_TOKEN_URL,
                    params={
                        "grant_type": "ig_exchange_token",
                        "client_secret": self._app_secret,
                        "access_token": access_token,
                    },
                )
                long_lived.raise_for_status()
                long_lived_data = long_lived.json()
                if "access_token" in long_lived_data:
                    access_token = long_lived_data["access_token"]
                    expires_in = long_lived_data.get("expires_in", 60 * 60 * 24 * 60)
            except Exception as exc:
                # Long-lived token exchange is best-effort — fall back to the
                # short-lived token rather than failing the whole connect flow.
                print(f"[MetaClient] Long-lived token exchange failed, using short-lived token: {exc}")

            try:
                me = await client.get(
                    f"{_GRAPH_BASE}/me",
                    params={
                        "fields": "id,username,name",
                        "access_token": access_token,
                    },
                )
                me.raise_for_status()
                me_data = me.json()
            except httpx.HTTPStatusError as exc:
                raise MetaAPIError(
                    f"Instagram connect failed (/me): {exc} | body={exc.response.text}"
                ) from exc

        return ConnectedAccount(
            access_token=access_token,
            ig_business_id=str(me_data.get("id") or short_lived_data.get("user_id", "")),
            ig_username=me_data.get("username"),
            fb_page_id=str(short_lived_data.get("user_id", "")),
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=expires_in),
        )

    async def refresh_long_lived_token(self, access_token: str) -> RefreshedToken:
        """Resets a long-lived token's 60-day clock without the owner having
        to reconnect — Meta requires the token be >=24h old and not yet
        expired (plan addendum: app/instagram/service.py calls this from a
        background sweep well before expiry, so both hold in practice)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(
                    _REFRESH_TOKEN_URL,
                    params={"grant_type": "ig_refresh_token", "access_token": access_token},
                )
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as exc:
                raise MetaAPIError(
                    f"Instagram token refresh failed: {exc} | body={exc.response.text}"
                ) from exc
            except httpx.HTTPError as exc:
                raise MetaAPIError(f"Instagram token refresh failed: {exc}") from exc

        expires_in = data.get("expires_in", 60 * 60 * 24 * 60)
        return RefreshedToken(
            access_token=data["access_token"],
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=expires_in),
        )

    async def send_message(self, *, ig_business_id: str, access_token: str, recipient_id: str, text: str) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    f"{_GRAPH_BASE}/me/messages",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"recipient": {"id": recipient_id}, "message": {"text": text}},
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                err_msg = _format_meta_error(exc, action="xabar yuborish")
                raise MetaAPIError(err_msg) from exc

    async def send_image(self, *, ig_business_id: str, access_token: str, recipient_id: str, image_url: str) -> dict:
        """Same send endpoint as send_message, just a URL attachment instead
        of text — Instagram fetches the URL itself and re-hosts it, so
        image_url must be publicly reachable (an R2-uploaded product photo,
        or any other public image URL). Errors are caught by the caller the
        same way a text-send failure is — a photo that doesn't go through
        must never break the customer-facing reply that already sent."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    f"{_GRAPH_BASE}/me/messages",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "recipient": {"id": recipient_id},
                        "message": {"attachment": {"type": "image", "payload": {"url": image_url}}},
                    },
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                err_msg = _format_meta_error(exc, action="rasm yuborish")
                raise MetaAPIError(err_msg) from exc

    async def get_user_profile(self, user_id: str, access_token: str) -> dict | None:
        """Tries graph.instagram.com and graph.facebook.com to fetch user profile for IGSID."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for base_url in [_GRAPH_BASE, "https://graph.facebook.com/v21.0"]:
                try:
                    res = await client.get(
                        f"{base_url}/{user_id}",
                        params={
                            "fields": "username,name,profile_pic",
                            "access_token": access_token,
                        },
                    )
                    if res.status_code == 200:
                        return res.json()
                    print(f"[MetaClient] {base_url}/{user_id} -> status {res.status_code}: {res.text}")
                except Exception as exc:
                    print(f"[MetaClient] {base_url}/{user_id} exception: {exc}")
        return None

    async def deauthorize_account(self, access_token: str, fb_page_id: str | None = None) -> None:
        """Revokes app permissions from the user's account and unsubscribes from the page."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            if fb_page_id:
                try:
                    await client.delete(
                        f"https://graph.facebook.com/v21.0/{fb_page_id}/subscribed_apps",
                        params={"access_token": access_token},
                    )
                except Exception as e:
                    print(f"[MetaClient] Failed to unsubscribe from page {fb_page_id}: {e}")

            for base_url in ["https://graph.facebook.com/v21.0", _GRAPH_BASE]:
                try:
                    await client.delete(
                        f"{base_url}/me/permissions",
                        params={"access_token": access_token},
                    )
                except Exception as e:
                    print(f"[MetaClient] {base_url}/me/permissions delete: {e}")
