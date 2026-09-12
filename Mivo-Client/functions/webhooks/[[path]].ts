// Cloudflare Pages Function: /webhooks/* -> <API_UPSTREAM>/webhooks/* (Meta
// and Telegram webhooks pointed at the dashboard's domain).
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
  let upstream: URL;
  try {
    upstream = new URL(context.env.API_UPSTREAM || DEFAULT_UPSTREAM);
  } catch {
    return jsonError(500, "API_UPSTREAM is not a valid URL");
  }

  const incoming = new URL(context.request.url);
  const target = new URL(upstream.origin);
  target.pathname = upstream.pathname.replace(/\/+$/, "") + incoming.pathname;
  target.search = incoming.search;

  // The raw body is streamed through unchanged — Meta's X-Hub-Signature-256
  // is an HMAC of the exact bytes.
  try {
    return await fetch(new Request(target.toString(), context.request));
  } catch {
    return jsonError(502, "Upstream unavailable");
  }
};
