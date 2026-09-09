/**
 * Thin fetch wrapper. JWT access/refresh tokens live in localStorage (header-
 * based auth, not cross-domain cookies — the SPA on Cloudflare Pages and the
 * FastAPI backend on the VPS are different domains).
 */
const ACCESS_TOKEN_KEY = "mivo_access_token";
const REFRESH_TOKEN_KEY = "mivo_refresh_token";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getAccessToken(): string | null {
  if (!import.meta.client) return null;
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function setTokens(access: string, refresh: string): void {
  if (!import.meta.client) return;
  localStorage.setItem(ACCESS_TOKEN_KEY, access);
  localStorage.setItem(REFRESH_TOKEN_KEY, refresh);
}

export function clearTokens(): void {
  if (!import.meta.client) return;
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  isForm?: boolean;
}

export function useApi() {
  const config = useRuntimeConfig();
  const apiBase = config.public.apiBase as string;

  async function refreshAccessToken(): Promise<string | null> {
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
    if (!refreshToken) return null;
    const res = await fetch(`${apiBase}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return null;
    const data = await res.json();
    setTokens(data.access_token, refreshToken);
    return data.access_token as string;
  }

  async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { method = "GET", body, isForm = false } = options;

    const doFetch = async (token: string | null): Promise<Response> => {
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      if (!isForm && body !== undefined) headers["Content-Type"] = "application/json";

      return fetch(`${apiBase}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      });
    };

    let token = getAccessToken();
    let res = await doFetch(token);

    // Only treat a 401 as "session died" when there WAS a token — i.e. we
    // were actually logged in and either it expired or got revoked. A 401
    // with no token at all is just a normal auth failure on a public
    // endpoint (wrong email/password on /auth/login itself, for instance)
    // and must surface as a regular error, not force a reload of the very
    // page the user is trying to log in from.
    if (res.status === 401 && token) {
      const newToken = await refreshAccessToken();
      if (newToken) {
        res = await doFetch(newToken);
      }

      if (res.status === 401) {
        // Refresh failed too — the session is dead. Don't leave the
        // caller's page just sitting there with a thrown error and a stale
        // "logged in" UI: wipe the dead tokens and hard-navigate to /login.
        // A real browser navigation (not router.push/navigateTo) is
        // deliberate here — this fires from deep inside a plain async
        // helper that arbitrary components call, not from a component's
        // own setup/lifecycle, so there's no guaranteed Nuxt app context to
        // safely call composables like navigateTo from at this point.
        // window.location also has the side benefit of resetting every
        // other bit of in-memory app state along with it, which is exactly
        // what a forced logout wants.
        clearTokens();
        if (import.meta.client) window.location.href = "/login";
      }
    }

    if (!res.ok) {
      let message = res.statusText;
      try {
        const data = await res.json();
        message = data.detail || message;
      } catch {
        // response body wasn't JSON
      }
      throw new ApiError(res.status, message);
    }

    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  return { apiRequest };
}
