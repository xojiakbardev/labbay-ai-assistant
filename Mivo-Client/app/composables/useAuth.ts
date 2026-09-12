import { getAccessToken, getRefreshToken, setTokens, clearTokens, withTokenLock } from "./useApi";
import { resetNotificationsState } from "./useNotifications";
import { unsubscribeDevicePush } from "./usePushNotifications";
import { resetUserPreferencesState } from "./useUserPreferences";

// useState gives an SSR-safe, app-wide singleton ref — but since this app runs
// with ssr:false, it's effectively just a shared client-side ref (plan: no SSR
// needed for an auth-gated dashboard).
function authState() {
  return useState<boolean>("mivo_is_authenticated", () => !!getAccessToken());
}

// null = not yet known (fetchMe() hasn't resolved this session) — distinct
// from `false` (confirmed: not a superadmin) so the route guard can tell
// "still loading" from "definitely a regular business owner".
function superadminState() {
  return useState<boolean | null>("mivo_is_superadmin", () => null);
}

export function useAuth() {
  const isAuthenticated = authState();
  const isSuperadmin = superadminState();
  const { apiRequest } = useApi();
  const api = useMivoApi();

  // There's no self-serve signup — every account (business owner or
  // superadmin) is created by a superadmin. The dashboard only needs to know
  // *which* kind of account just logged in, to route it correctly.
  async function fetchMe(): Promise<void> {
    if (!isAuthenticated.value) {
      isSuperadmin.value = null;
      return;
    }
    try {
      const me = await apiRequest<{ is_superadmin: boolean }>("/auth/me");
      isSuperadmin.value = me.is_superadmin;
    } catch {
      isSuperadmin.value = null;
    }
  }

  // Throws ApiError: 401 for bad credentials, 429 (with retryAfter) when
  // throttled.
  async function signIn(loginKey: string, password: string) {
    const data = await apiRequest<{ access_token: string; refresh_token: string }>("/auth/login", {
      method: "POST",
      body: { email: loginKey, password },
    });
    setTokens(data.access_token, data.refresh_token);
    isAuthenticated.value = true;
    await fetchMe();
  }

  /**
   * Fixed order: this device's push subscription (needs the still-valid
   * session), then the refresh token is revoked server-side, then local state
   * is dropped and the page hard-reloads on /login. A failing server step is
   * logged and sign-out still completes — the user asked to leave, and the
   * local tokens are gone either way.
   *
   * Revoke-and-clear runs under the token lock: a refresh in flight (in any
   * tab) finishes first, so the token revoked is the current one, and nothing
   * can write a live session back after it's cleared.
   */
  async function signOut() {
    try {
      // No-op when this browser holds no push subscription.
      await unsubscribeDevicePush(api);
    } catch (err) {
      console.error("[useAuth] push unsubscribe on sign-out failed", err);
    }

    await withTokenLock(async () => {
      const refreshToken = getRefreshToken();
      if (refreshToken) {
        try {
          await apiRequest<void>("/auth/logout", { method: "POST", body: { refresh_token: refreshToken } });
        } catch (err) {
          console.error("[useAuth] server-side logout failed", err);
        }
      }
      clearTokens();
    });

    resetNotificationsState();
    resetUserPreferencesState();
    isAuthenticated.value = false;
    isSuperadmin.value = null;
    if (import.meta.client) window.location.href = "/login";
  }

  return { isAuthenticated, isSuperadmin, signIn, signOut, fetchMe };
}
