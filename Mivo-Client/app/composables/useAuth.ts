import { getAccessToken, setTokens, clearTokens } from "./useApi";

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

  async function signIn(loginKey: string, password: string) {
    const data = await apiRequest<{ access_token: string; refresh_token: string }>("/auth/login", {
      method: "POST",
      body: { login: loginKey, login_key: loginKey, email: loginKey, password },
    });
    setTokens(data.access_token, data.refresh_token);
    isAuthenticated.value = true;
    await fetchMe();
  }

  function signOut() {
    clearTokens();
    isAuthenticated.value = false;
    isSuperadmin.value = null;
    navigateTo("/login");
  }

  return { isAuthenticated, isSuperadmin, signIn, signOut, fetchMe };
}
