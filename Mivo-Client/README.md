# Mivo — Dashboard (`Mivo-Client/`)

Nuxt 4 SPA (`ssr: false`, static output) — the business-owner dashboard:
conversations, leads, products (manual + AI import), AI settings,
integrations, plus the `/superadmin` panel. Talks to the backend only over
HTTP; no shared code with `../Mivo-Server`.

## Local development

```bash
npm install --legacy-peer-deps
cp .env.example .env   # NUXT_PUBLIC_API_BASE=http://localhost:8000
npm run dev
```

> `--legacy-peer-deps` works around an npm@10 arborist bug
> (`Cannot read properties of null (reading 'edgesOut')`) on Nuxt's peer
> dependency graph — not Mivo-specific, safe to use.

Locally the SPA calls the backend directly, so the backend must be running at
`NUXT_PUBLIC_API_BASE` with `CORS_ALLOW_ORIGINS` in its `.env` including this
app's origin (`http://localhost:3000`, or your tunnel URL).

## Build

```bash
npm run build   # or `npm run generate` for a fully static export
```

## Deploy — Cloudflare Pages

In production the browser never calls the API domain directly. The SPA's API
base defaults to `/api` (leave `NUXT_PUBLIC_API_BASE` **unset** in the Pages
project), and the Pages Functions in `functions/` proxy same-origin requests
to the backend:

| Path on the dashboard domain | Proxied to                                  | Used by                                  |
| ---------------------------- | ------------------------------------------- | ---------------------------------------- |
| `/api/*`                     | `<API_UPSTREAM>/*` (the `/api` prefix is dropped) | every API call and the SSE stream  |
| `/integrations/*`            | `<API_UPSTREAM>/integrations/*`             | Instagram OAuth callback (`/integrations` itself is the SPA page and is not proxied) |
| `/webhooks/*`                | `<API_UPSTREAM>/webhooks/*`                 | Meta / Telegram webhooks                 |

Request bodies and responses are streamed through unchanged (webhook HMAC
signatures are computed over the raw body; SSE must stream). If the upstream
can't be reached the proxy answers `502 {"detail": "Upstream unavailable"}`.

Pages project settings:

- Root directory: `Mivo-Client`. Framework preset: Nuxt.js.
- Environment variable **`API_UPSTREAM`** — the backend base URL including its
  path prefix, e.g. `https://api.yourbusiness.com/mivo`. When it is not set the
  functions fall back to the current production upstream
  (`https://enjoy-api.nasriddinov.dev/mivo`), so setting it is only needed to
  point the dashboard at a different backend.
- Do not set `NUXT_PUBLIC_API_BASE` (it must stay `/api` for the proxy to be used).
- The backend's `FRONTEND_URL` must be the dashboard's domain: Instagram's
  OAuth callback redirects the browser back to `<FRONTEND_URL>/integrations`.

`public/_headers` sets the security headers (CSP etc.) Pages serves with every
response; `public/sw.js` is the web-push service worker.
