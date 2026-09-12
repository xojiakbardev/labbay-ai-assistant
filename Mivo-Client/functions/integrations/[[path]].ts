// Cloudflare Pages Function: /integrations/* -> <API_UPSTREAM>/integrations/*
// — for Instagram's OAuth redirect (/integrations/instagram/callback), which
// Meta sends to the dashboard's own domain.
interface Env {
  // Backend base URL including any path prefix, e.g. https://api.example.com/mivo.
  API_UPSTREAM?: string;
}

// Used until API_UPSTREAM is set in the Pages project, so a deploy without
// the variable keeps working exactly as before.
const DEFAULT_UPSTREAM = "https://enjoy-api.nasriddinov.dev/mivo";

function jsonError(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export const onRequest: PagesFunction<Env> = async (context) => {
  const incoming = new URL(context.request.url);
  // /integrations itself is the SPA page (the OAuth flow lands there with
  // ?instagram_pending=...) — serve the static app, don't proxy it.
  if (incoming.pathname === "/integrations" || incoming.pathname === "/integrations/") {
    return context.next();
  }

  let upstream: URL;
  try {
    upstream = new URL(context.env.API_UPSTREAM || DEFAULT_UPSTREAM);
  } catch {
    return jsonError(500, "API_UPSTREAM is not a valid URL");
  }

  const target = new URL(upstream.origin);
  target.pathname = upstream.pathname.replace(/\/+$/, "") + incoming.pathname;
  target.search = incoming.search;

  // Passed through untouched, body stream included.
  try {
    return await fetch(new Request(target.toString(), context.request));
  } catch {
    return jsonError(502, "Upstream unavailable");
  }
};
