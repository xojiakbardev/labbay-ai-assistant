/**
 * Thin fetch wrapper. JWT access/refresh tokens live in localStorage (header-
 * based auth, not cross-domain cookies — the SPA on Cloudflare Pages and the
 * FastAPI backend on the VPS are different domains).
 */
const ACCESS_TOKEN_KEY = "mivo_access_token";
const REFRESH_TOKEN_KEY = "mivo_refresh_token";

// Credential endpoints: never sent a bearer token and never retried through a
// refresh. A 401 from /auth/login is a wrong password, not a dead session.
const CREDENTIAL_PATHS = ["/auth/login", "/auth/refresh", "/auth/logout"];

export class ApiError extends Error {
  status: number;
  // Seconds, from a 429's Retry-After header — null when absent or unreadable
  // (a cross-origin response only exposes it if CORS allows the header).
  retryAfter: number | null;
  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export function getAccessToken(): string | null {
  if (!import.meta.client) return null;
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  if (!import.meta.client) return null;
  return localStorage.getItem(REFRESH_TOKEN_KEY);
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

/** FastAPI errors: `detail` is a string, or — for 422 validation errors — an
 * array of `{loc, msg}` objects. */
function formatDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as any).msg) : null))
      .filter((m): m is string => !!m);
    return msgs.length ? msgs.join("; ") : null;
  }
  return null;
}

async function toApiError(res: Response): Promise<ApiError> {
  let message = res.statusText || `HTTP ${res.status}`;
  const text = await res.text();
  if (text) {
    try {
      message = formatDetail(JSON.parse(text)?.detail) ?? message;
    } catch {
      // Not JSON (a proxy's HTML error page, for instance) — keep the status text.
    }
  }
  const retryHeader = res.headers.get("Retry-After");
  const retryAfter = retryHeader !== null && /^\d+$/.test(retryHeader.trim()) ? Number(retryHeader) : null;
  return new ApiError(res.status, message, retryAfter);
}

// The session is dead (refresh token rejected). A real browser navigation,
// not navigateTo: this runs deep inside a plain async helper with no
// guaranteed Nuxt app context, and a full reload also drops every bit of
// in-memory state (SSE stream, polling timers, cached data) with it.
function endDeadSession(): void {
  clearTokens();
  if (import.meta.client) window.location.href = "/login";
}

// /auth/refresh rotates the refresh token, and presenting an already-rotated
// one is rejected (or, past a short grace window, revokes every session). All
// open tabs share the tokens in localStorage, so every read-rotate-write of
// them — a refresh, or sign-out's revoke-and-clear — runs under one Web Lock
// held across tabs. Within a tab, concurrent 401s also share one refresh.
const TOKEN_LOCK = "mivo-token-rotation";

export async function withTokenLock<T>(fn: () => Promise<T>): Promise<T> {
  return await navigator.locks.request(TOKEN_LOCK, fn);
}

let refreshInFlight: Promise<string> | null = null;

function refreshAccessToken(apiBase: string, rejectedAccessToken: string): Promise<string> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = withTokenLock(async () => {
    // Another tab refreshed (or signed out) while this one waited for the lock.
    const current = getAccessToken();
    if (current && current !== rejectedAccessToken) return current;
    const refreshToken = getRefreshToken();
    if (!refreshToken) {
      endDeadSession();
      throw new ApiError(401, "Session expired.");
    }
    const res = await fetch(`${apiBase}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (res.status === 401) {
      endDeadSession();
      throw await toApiError(res);
    }
    // A 5xx (or anything else) is an outage, not a logout: the tokens stay
    // and the caller gets a normal error.
    if (!res.ok) throw await toApiError(res);
    const data = (await res.json()) as { access_token: string; refresh_token: string };
    setTokens(data.access_token, data.refresh_token);
    return data.access_token;
  }).finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  isForm?: boolean;
  signal?: AbortSignal;
}

export function useApi() {
  const config = useRuntimeConfig();
  const apiBase = config.public.apiBase as string;

  async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { method = "GET", body, isForm = false, signal } = options;
    const isCredentialPath = CREDENTIAL_PATHS.some((p) => path === p || path.startsWith(`${p}?`));

    const doFetch = (token: string | null): Promise<Response> => {
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      if (!isForm && body !== undefined) headers["Content-Type"] = "application/json";
      return fetch(`${apiBase}${path}`, {
        method,
        headers,
        signal,
        body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      });
    };

    const token = isCredentialPath ? null : getAccessToken();
    let res = await doFetch(token);

    // Only a 401 on a request that carried a token means "access token
    // expired/revoked". Without a token it's an ordinary auth failure that
    // must surface as a regular error.
    if (res.status === 401 && token) {
      const current = getAccessToken();
      // Another request already rotated the tokens while this one was in
      // flight — retry with the new token instead of refreshing again.
      const nextToken = current && current !== token ? current : await refreshAccessToken(apiBase, token);
      res = await doFetch(nextToken);
    }

    if (!res.ok) throw await toApiError(res);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  return { apiRequest, apiBase };
}
