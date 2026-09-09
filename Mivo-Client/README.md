# Mivo — Dashboard (`client/`)

Nuxt 3 SPA (`ssr: false`) — the business-owner dashboard: onboarding,
conversations, leads, products (manual + AI import), AI settings,
integrations. Fully self-contained; talks to the backend only over HTTP via
`NUXT_PUBLIC_API_BASE`, no shared code with `../server`.

## Local development

```bash
npm install --legacy-peer-deps
cp .env.example .env   # set NUXT_PUBLIC_API_BASE to your backend URL
npm run dev
```

> `--legacy-peer-deps` works around an npm@10 arborist bug
> (`Cannot read properties of null (reading 'edgesOut')`) on Nuxt's peer
> dependency graph — not Mivo-specific, safe to use.

Requires the backend (`../server`) running and reachable at that URL, with
`CORS_ALLOW_ORIGINS` in its `.env` including this app's origin
(`http://localhost:3000`, or your tunnel/deployed URL).

## Build

```bash
npm run build   # or `npm run generate` for a fully static export
```

## Deploy — Cloudflare Pages

- Connect this repo in the Cloudflare Pages dashboard.
- Root directory: `client`. Framework preset: Nuxt.js.
- Environment variable: `NUXT_PUBLIC_API_BASE=https://api.yourbusiness.com`.
- Bind your dashboard domain and add it to the backend's `CORS_ALLOW_ORIGINS`.
