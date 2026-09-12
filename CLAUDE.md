# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Mivo AI — an autonomous AI sales assistant that answers a shop's Instagram Direct
messages, searches the shop's real product catalog, sends product photos, captures
phone numbers as hot leads, and hands off to a human when needed. Two independent
apps in one repo, communicating only over HTTP:

- `Mivo-Server/` — FastAPI + PostgreSQL backend (Python 3.12, `uv`)
- `Mivo-Client/` — Nuxt 4 SPA dashboard for the business owner (`ssr: false`)

`context.md` (root, in Uzbek) is the deepest architecture write-up — conversation
flow, prompt contents, tradeoffs. Prefer it for "why" questions, but verify against
code: it is slightly stale (e.g. it says outbound follow-up doesn't exist, while
`app/ai/follow_up.py` + the 15-minute scheduler job in `app/main.py` implement it).
Both READMEs reference a `MIVO_MVP.md` spec that is not in the repo; code comments
citing "plan §N" refer to that missing document.

## Commands

Backend (`Mivo-Server/`, on Windows use `.venv/Scripts/` instead of `.venv/bin/`):

```bash
uv sync                                   # install from pyproject.toml/uv.lock (incl. dev deps)
docker compose up db -d                   # pgvector/pgvector:pg16 — stock postgres lacks the vector extension
python backfill_embeddings.py             # after enabling semantic search, or changing EMBEDDING_MODEL
uv add <pkg>  /  uv add --dev <pkg>       # add deps — commit both pyproject.toml and uv.lock
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload   # http://localhost:8000, docs at /docs

.venv/bin/pytest                          # full suite
.venv/bin/pytest tests/test_orchestrator.py::test_name   # one test
.venv/bin/alembic revision --autogenerate -m "..."       # new migration
```

Frontend (`Mivo-Client/`):

```bash
npm install --legacy-peer-deps   # the flag works around an npm@10 arborist bug on Nuxt's peer graph
npm run dev                      # http://localhost:3000
npm run build                    # or `npm run generate` for a static export
npm run typecheck                # vue-tsc over the whole app — keep it at zero errors
```

There is no linter or formatter configured in either app.

### Evals (`Mivo-Server/evals/`) — separate from tests

`pytest` proves the pipeline works; it can't tell you whether the AI *sells*
well. `python -m evals.run` replays scripted conversations through the real
engine against a throwaway seeded business (created and deleted per run; nothing
reaches Instagram) and scores each reply two ways: deterministic checks
(`checks.py` — invented prices, leaked plan text, wrong language, invented
discounts, deflection, re-asked slots, missed phone ask) and an LLM judge
(`judge.py` — naturalness, usefulness, sales_movement, coherence).

Needs a DB and `OPENROUTER_API_KEY`; `--no-judge` skips the judge calls, and
`python -m evals.run metrics` prints read-only production numbers. Exit code is
non-zero on any deterministic failure, so it works as a CI gate. Not collected by
pytest (`testpaths = tests`), but the checks themselves are unit-tested in
`tests/test_eval_checks.py`.

Two rules that keep it honest: a check that mirrors a runtime rule imports **the
same function the runtime uses** rather than reimplementing it, and the judge
never learns which version wrote a reply — compare runs by score, never ask it
"is this better". Full workflow in `evals/README.md`.

### Tests need a real Postgres

`tests/conftest.py` points at a `mivo_test` database (`DATABASE_URL` /
`DATABASE_URL_SYNC`), runs `alembic upgrade head` once per session (a failed
migration fails the run), and TRUNCATEs every table between tests — start the
`db` service first and create `mivo_test`. conftest also sets every secret to a
test-only value; webhook tests sign payloads with `sign_webhook()`. After adding a
model or migration, `alembic check` against a freshly migrated database must
report no drift (that's how the never-migrated `product_variants`/`customers.name`
columns were found). `asyncio_mode = auto` (pytest.ini): async tests need no
marker. Because asyncpg connections are bound to the loop that created them, the
fixtures dispose the engine around each test and do teardown over plain sync
`psycopg` — keep that pattern when adding fixtures. The scheduler is off in tests
(`SCHEDULER_ENABLED=false`): a lifespan job cut off mid-transaction by a closing
TestClient left a lock that hung the next TRUNCATE. CI (`.github/workflows/ci.yml`)
runs the suite, `alembic check`, and the client's typecheck + generate.

## Backend architecture

Vertical slices under `app/<domain>/` (`models.py` / `schemas.py` / `service.py` /
`router.py`). Cross-cutting pieces live in `app/core/` (config, async engine,
security/JWT/Fernet, `models_registry.py`) and `app/common/` (mixins, tenancy).

Rules that hold across the codebase:

- **Tenancy is centralized.** Every router except `/auth/*` and the webhook
  endpoints depends on `get_current_business` (`app/common/tenancy.py`) and filters
  by that business's ID — never by a client-supplied `business_id`. Superadmin
  routes use `get_current_superadmin`, which re-reads the user per request so
  revoking the flag takes effect immediately.
- **New models must be imported in `app/core/models_registry.py`**, or Alembic
  autogenerate and the test-teardown TRUNCATE will silently miss them.
- **Two DB URLs, one database:** `DATABASE_URL` (asyncpg) for the app,
  `DATABASE_URL_SYNC` (psycopg) for Alembic and test helpers.
- **Secrets at rest** (Instagram tokens, pending OAuth codes) are Fernet-encrypted
  via `app/core/security.py` (`MultiFernet`: `FERNET_KEY=<new>,<old>` +
  `rotate_fernet_key.py` to rotate); decrypt only at the point of use.
- **Config refuses insecure values** (`app/core/config.py`): `JWT_SECRET`/`FERNET_KEY`
  are required, any value that has appeared in this repo is rejected, and
  `APP_ENV=production` also requires `DEBUG=false` and the Meta/Telegram webhook
  secrets. Never add a default secret.
- `settings.debug` drives SQLAlchemy `echo`, which logs PII in bind params — must
  stay off in production.
- **Auth**: refresh tokens are persisted (`refresh_tokens`) and rotate on use;
  reuse of a rotated token revokes the user's whole family. `/auth/logout` revokes.
  Failed logins are throttled per email (`app/core/rate_limit.py`). Soft-deleting a
  business revokes its owner's refresh tokens and `get_current_business` refuses it
  immediately. The SSE stream authenticates with a 60-second `sse` ticket
  (`POST /notifications/stream-ticket`), never the access token in a URL.
- **Instagram OAuth** is session-bound: the callback only parks the code under a
  one-time completion id (`oauth_states`); the owner's logged-in dashboard finishes
  it with `POST /integrations/instagram/complete`. One Instagram account can be
  linked to one business (unique `ig_business_id`).

### The conversation pipeline

`POST /webhooks/instagram` (`app/instagram/router.py`) verifies the HMAC
`X-Hub-Signature-256` (fails closed: no `META_APP_SECRET` → every webhook is
rejected), then `app/instagram/pipeline.py` does the rest in two halves:

- **In the request** (`ingest_event`, fast, DB only): insert the `webhook_events`
  row (`ON CONFLICT DO NOTHING` on the message id — a redelivery of a finished
  event is a no-op), route to the business by **exact** recipient match
  (an unknown recipient is recorded and dropped — never handed to "some other
  connected account"), upsert customer + conversation (both `ON CONFLICT`),
  persist the customer message **whether or not the AI will reply**, commit,
  return 200. A failure here is a 5xx and Meta retries.
- **In the background** (`process_event`, a BackgroundTask after the response,
  at most 8 at once per process): claim the event (`received`/`failed`/stale
  `processing` → `processing`, with a lease, only while `attempts < MAX_ATTEMPTS`),
  fetch the profile once (`profile_fetched_at`), then:
  1. `ai_may_reply(business)` (`app/businesses/service.py`: owner's `ai_enabled`,
     not `ai_suspended`, not deleted, subscription active) and conversation status
     `ai_active`/`active`. Otherwise a phone number is still captured
     (`capture_phone_without_ai_turn`) and, during a handoff, acknowledged.
  2. Debounce `DEBOUNCE_SECONDS`; bail if a newer customer message exists; commit
     (a waiter holds one pooled connection, not two).
  3. **`conversation_lock`** (`app/conversations/locks.py`, a Postgres advisory
     lock on its own AUTOCOMMIT connection): everything that sends to a customer
     runs under it, so turns never overlap.
  4. Resend this message's undelivered AI reply (`undelivered_reply` in
     `app/conversations/delivery.py`), then skip if
     `conversation.last_answered_customer_message_at` already covers this message
     (that's how a retry knows it was answered).
  5. Transcribe this burst's voice notes (`app/ai/audio.py`, one configured
     provider; failure → human handoff), spend guards (`app/ai/limits.py`; past a
     limit → handoff + owner alert), and resolve the LLM provider — the router
     passes a factory, so a misconfigured provider hands off instead of failing
     the webhook.
  6. `run_turn(..., outbound=True)`: reply parts are persisted as `pending`
     outbox rows **and** `last_answered_customer_message_at` is set in the same
     commit, then `send_outbound` marks them `sent`/`failed`. A delivery error is
     returned in `escalation_state["delivery_error"]`; the pipeline does the lead
     bookkeeping and alerts, then fails the event (retried with backoff, abandoned
     after `MAX_ATTEMPTS` — or at once for a permanent Meta error — with an owner
     alert). The retry resends the recorded reply, never regenerates it.
  7. After delivery nothing may fail the event: product photos (also outbox rows),
     `apply_qualification` (skipped when `analysis_failed`), and owner alerts
     (`app/notifications/owner_alerts.py`: hot lead, handoff, limits) are logged on
     error, not raised. Owners are alerted *before* a handoff line is sent.
- `sweep_events` (scheduler, every 30s) re-drives `failed` events past their
  backoff, orphaned `received` ones, and `processing` ones whose lease expired;
  an expired lease with no attempts left is abandoned with an alert.
- An event holds one of `WEBHOOK_CONCURRENCY` slots, but gives it (and its DB
  connection) back during the debounce. On shutdown (uvicorn waits 75 s for running
  turns) events still in flight go back to `failed`, due now, attempt not counted
  (`release_interrupted`). `/health` answers 503 when the database doesn't.

Echoes (`is_echo`) run without the conversation lock. One whose id is recorded
is ours; one that matches a recent outbound row still waiting for its id is ours
too (`reconcile_echo` — the echo can beat the send response). Any other echo is
a person replying from the Instagram app — recorded as `human`, and the
conversation moves to `human_active` so the AI steps back (the owner is told
once, when the status changes). Dashboard operator replies do the same
(`POST /conversations/{id}/reply`, also via the outbox).

Instagram-specific logic stays inside `app/instagram/`; everything else sees only
the neutral conversations/messages/customers domain model. The same pipeline is
reachable from the dashboard sandbox (`/ai/sandbox/*`) for testing without Instagram.
Its customer has `is_sandbox` set and stays out of the inbox, the leads list and
"popular products" (AI cost it runs up still counts — it's real spend).

### The AI layer (`app/ai/`)

A turn runs as **grounding loop → writer → deliver → analyst**, three HTTP calls
minimum. The writer and analyst are split because writing a customer-facing
reply and filling in lead bookkeeping are different jobs: constrained JSON
decoding flattens prose, and emitting `reply` before any analysis means
committing to wording before deciding the sales move. Keep them separate.

**The reply is delivered between those two passes**, via the `on_reply` hook
(`run_sales_turn`) / `deliver` callback (`run_turn`). Nothing after the writer
changes what the customer reads, so they never wait on the analysis. Rules that
follow from that:

- The hook is where the **safety guards** run (`_apply_reply_guards`: escalation
  wording, unverified-discount interception) — they are the last word on
  customer-facing text and may rewrite it, so whatever the hook returns is final.
- Messages are **persisted before they're sent**, never the reverse.
- Once delivery has happened the turn is committed to that reply: a later
  failure sets `escalation_state["analysis_failed"]` and degrades the analysis
  rather than sending a second message. **Callers must skip
  `apply_qualification` when that flag is set** — a lead's previous assessment
  beats one derived from nothing.
- Raising from `deliver` fails the turn, which is what Meta's webhook retry is
  for. It's the one thing in the Instagram pipeline that isn't swallowed.
- `reply_parts.split_reply` breaks a long reply into the 2-3 short messages a
  person would have sent (Instagram sends them with `PART_DELAY_SECONDS`
  between). It is deliberately conservative: anything unclear returns the text
  unsplit, and a split that would lose a single character is discarded.

- `provider/` — vendor abstraction. `generate_structured` and `run_agentic_turn`
  are abstract; **`run_sales_turn` is what the conversation engine actually
  calls** and has a base-class default that falls back to a single fused
  `run_agentic_turn` (which is why every test fake still works while
  implementing only the two abstract methods). Only OpenRouter is implemented;
  swap via `LLM_PROVIDER`. It owns retry-with-backoff on 429/5xx, a multi-model
  fallback chain, self-healing JSON parsing, tool-argument recovery, and writes
  `ai_usage_logs` rows (the only place raw token/cost numbers exist).
  - Writer and analyst take separate models and temperatures (`LLM_WRITER_MODEL`,
    `LLM_ANALYST_MODEL`, `LLM_*_TEMPERATURE`), both defaulting to
    `OPENROUTER_MODEL`.
  - The writer emits `PLAN: …` then `===MESSAGE===` then the message.
    `_extract_customer_message` strips the plan and returns `""` if it can't
    cleanly separate the two — which raises, so the caller apologises and hands
    off rather than ever sending an internal note to a customer. Keep that
    parser paranoid.
  - `_needs_product_grounding` decides whether round 0 forces a tool call.
    Product hints win; a short message carrying only a conversational signal
    (greeting, thanks, objection, "let me think about it", a bare phone number)
    is answered, not searched.
- `context/builder.py` — two prompts. `_BASE_SYSTEM_PROMPT` (sales: how to sell,
  how to write, worked weak/good examples, hard correctness rules) drives the
  tool loop and the writer; `_ANALYST_SYSTEM_PROMPT` (scoring bands, trilingual
  summaries, fact extraction, photo selection) drives the analyst. Bookkeeping
  instructions belong in the analyst prompt — every line of it in the sales
  prompt competes for attention with the part that decides how replies read.
  Both get BUSINESS SETTINGS; the sales prompt also gets active `AiFeedback`
  corrections and the CUSTOMER PROFILE block. Products are **never** preloaded.
- `tools/definitions.py` — retrieval + escalation only: `search_products`,
  `browse_catalog`, `get_similar_products`, `get_product`,
  `check_product_availability`, `get_active_discounts`, `request_human`.
  **There is deliberately no create_lead/update_lead tool.** Keep the list short
  — tool-selection accuracy degrades as it grows.
- `orchestrator.py` — `TurnAnalysis` (the analyst's output) and
  `ConversationTurnResult` (`TurnAnalysis` + `reply`, what the rest of the
  backend consumes). Field order is load-bearing: `qualification_reason` first,
  `reply` last, so the single-pass fallback also reasons before it answers.
  Every field is a *proposal* — Phase 8 decides what is persisted.
- `conversation_state.py` — `conversations.working_state` (JSONB): the sale in
  progress across turns — stage, product in focus, the price/stock facts already
  told to this customer, the open question, objections raised. **Product facts
  are recorded from tool results, never from model output**, so a price carried
  forward is one the catalog actually returned; only stage/open_question/
  new_objection/slots_learned come from the analyst. `slots` tracks the
  discovery answers a salesperson needs (`DISCOVERY_SLOTS`); the prompt lists
  what's known as do-not-re-ask and names the missing `ESSENTIAL_SLOTS` to
  chase. Rendered into the system prompt as a
  CONVERSATION STATE block, which is a record of what was said and never a
  substitute for a fresh tool call. Assign a new dict to change it — mutating
  JSONB in place won't mark the attribute dirty. Anything that resets a
  conversation must clear it too (see `/ai/sandbox/reset`).
- `follow_up.py` — scheduled re-engagement of inactive warm/hot leads.
- **Nothing tunable is hardcoded.** Numbers, limits, windows and URLs are `Settings` fields
  (`app/core/config.py`, overridable in `.env`). Prompts, default reply texts
  (`replies.json`) and word lists (`lexicon.json`) are files in `app/prompts/`, loaded by
  `app.prompts.load/render/lexicon`; a business overrides reply texts from the AI settings
  page (`businesses.reply_texts`). Constants left in code are data contracts: status/event
  names, token types, regexes, security lists, protocol URLs. Settings are bounded
  (positive counts, fractions in 0..1) and cross-checked at startup
  (`_refuse_contradictory_tuning`): a bad `.env` stops the app, not a conversation.
- The price guard (`find_unverified_prices`) is also the eval's price check. It
  allows catalog/variant prices, discounted ones, the delivery fee and discount
  thresholds, the customer's stated budget — and arithmetic only when the reply
  says so (a quantity, "jami", delivery; words in `lexicon.json` `price_arithmetic`).
- `closing.py` — knowing when the conversation is over. A short, plain-text,
  question-free burst after our last message ("hop", "ok", 👍) goes to a small
  structured call (`ClosingDecision`): if the model says it's finished, nothing is
  sent — the pipeline reacts to the customer's message with the emoji the model
  picked (`MetaClient.send_reaction`) and marks the message answered. The sandbox
  does the same. A failed decision means a normal reply.
- **Media the model can't see.** Media messages are stored with no text of their
  own (a shared post/Reel keeps its caption, from the payload's `title`); the
  dashboard renders the media. `builder.build_message_history` turns them into
  `(Note: …)` lines for the model — never the old bracketed labels, which it used
  to quote back to customers. The reply guards cut any note/label that still
  reaches a reply (`strip_internal_markers`) and replace a description of a shared
  Reel/post it never saw (`claims_to_see_media`) with an honest "I can't open it —
  which product was it?". Voice notes keep the `[Ovozli xabar]` placeholder: it's
  the "not transcribed yet" state.

**Lead writes are deterministic and backend-only.** `app/leads/service.py` is the
single writer to `leads`; `app/leads/scoring.py` owns the cold/warm/hot bands
(0-34/35-69/70-100) and Uzbek-aware E.164 phone extraction. A regex-validated phone
pins the lead to hot regardless of what the model proposed, and LLM-reported product
IDs are cross-checked against real rows before being stored. Keep new lead logic here
rather than giving the model more authority.

### Product retrieval — four tiers, fused

`search_products` runs a lexical cascade and fuses a semantic tier on top:

1. full-text (tsvector), strict AND
2. full-text, OR + synonym expansion
3. trigram over `search_normalized` (Cyrillic/Latin, apostrophes, typos)
4. **embeddings** — the only tier that can match a customer who *describes* what
   they want instead of naming it ("qishga issiq narsa kerak")

The first three only match words the catalog already contains. Tiers 1-3 are
unchanged and still run first; tier 4 is fused with **weighted RRF**
(`SEMANTIC_FUSION_WEIGHT`, below 1.0 on purpose) so semantics adds recall
without overruling an exact keyword match. When the lexical tiers find nothing,
semantic results are returned outright — that's the case the tier exists for.

Rules that keep it safe:

- **It degrades to nothing, never to an error.** No `EMBEDDING_API_KEY`, a dead
  API, a missing extension — `_semantic_candidates` logs and returns `[]`, and
  search behaves exactly as it did before. A customer must never lose a reply to
  a third-party embedding call.
- `_SEMANTIC_MAX_DISTANCE` matters: vector search always returns its N nearest
  rows however unrelated, so without a threshold an unmatched query comes back
  full of confident rubbish. Tune it with `python -m evals.run`, not by intuition.
- Rows embedded by a **different model are ignored** until re-embedded — vectors
  from two models aren't comparable. `embedding_hash` (text + model + width)
  drives that, so editing a stock count costs no API call and a config change
  invalidates everything.
- `EMBEDDING_DIMENSIONS` (1536) must match the `products.embedding` column and
  stay ≤ 2000: **pgvector cannot build an HNSW index above 2000 dims**. Changing
  it means a migration plus `python backfill_embeddings.py --all`.
- Requires the `vector` extension — `docker-compose.yml` uses
  `pgvector/pgvector:pg16`, not stock postgres.

Embeddings are written in the product write path (`crud.py` →
`products/embeddings.py`), best-effort: a failed embed never fails the write.

### Browsing and lexical search

Two modules, two different questions. `search.py` answers "do you have X" and
needs a search term; `discovery.py` answers "what do you have" and needs none —
`browse_products` / `list_categories` (from `attributes["category"]`, which AI
import fills in and manual entry often doesn't, so an empty category list is
normal), `similar_products` (same category, price within `SIMILAR_PRICE_SPREAD`),
and `popular_products`, ranked from `leads.interested_products` because the
system has no orders to count. Both are tenant-scoped and capped; the model never
sees the whole catalog.

`app/products/search.py` is hybrid: Postgres full-text (`search_vector`, a generated
column with a GIN index; `plainto_tsquery` is replaced by an OR-ed, synonym-expanded
`to_tsquery` so multi-word queries don't return nothing), falling back to `pg_trgm`
similarity over `products.search_normalized`. That column is Cyrillic→Latin
transliterated and apostrophe-unified (`search_normalize.py`) and is recomputed in
`app/products/crud.py` on create/update — if you change normalization, re-run
`backfill_search_normalized.py`. Results are always capped (`DEFAULT_LIMIT = 5`);
the whole catalog is never handed to the model.

### Other backend notes

- Conversation history is capped at `HISTORY_LIMIT = 20` messages
  (`app/conversations/service.py`). Anything older is carried by the CUSTOMER
  PROFILE block (lead summary + `known_facts`) and the conversation's
  `working_state`, not by raw history. `build_message_history` attaches only the
  customer's most recent `MAX_MULTIMODAL_IMAGES` photos; older ones degrade to a
  text marker.
- Conversation states: `ai_active` → `human_needed` → `human_active` → `closed`.
- Real-time dashboard updates are SSE via an in-process, tenant-isolated
  `NotificationBroadcaster` (`/notifications/stream`) — it does not survive multiple
  API replicas.
- APScheduler jobs run in the app lifespan (`app/main.py`), each under a Postgres
  advisory lock so extra processes never double-run them: daily Instagram token
  refresh (long-lived tokens expire at 60 days; refreshing resets the clock), the
  15-minute follow-up sweep (`app/ai/follow_up.py`: only when `ai_may_reply`, only
  inside Meta's 24h window from the customer's last message, once per silence, via
  the outbox), a 30-minute customer-profile backfill, and the 30-second webhook sweeper.
- Owner alerts (`app/notifications/owner_alerts.py`) go to dashboard + push +
  Telegram, each channel independent; dashboard links use `FRONTEND_URL`.
- No self-serve signup. Businesses are created from `/superadmin/*`; the first
  superadmin is bootstrapped with `create_superadmin.py`, later ones by SQL.
  Payments are recorded manually (`Payment` model) — there is no payment gateway.
- Root-level scripts are ops helpers, not part of the app: `reset_and_seed.py`
  (dev only — refuses unless `APP_ENV=development` and `--yes-wipe-everything`),
  `sync_script.py` (profile backfill now), `rotate_fernet_key.py`,
  `backfill_embeddings.py`, `create_superadmin.py`.
- Docker: one `Dockerfile`; `docker-compose.yml` (local, project `mivo-local`) and
  `docker-compose.prod.yml` (standalone prod, project `mivo`, a one-shot `migrate`
  service before `api`, DB not published) are independent files — not an override.

## Frontend architecture

Nuxt 4, `ssr: false`, `nitro.preset: "static"` — a static SPA deployed to Cloudflare
Pages. Tailwind v4 + shadcn-vue (`app/components/ui/`, registered without a path
prefix; the `index.ts` barrels are excluded from component scanning).

- `composables/useApi.ts` — the only fetch wrapper. JWT access/refresh in
  localStorage (header auth, not cookies: SPA and API are different domains).
  Refresh is single-flight and stores the rotated refresh token; only a 401 from
  `/auth/refresh` (or no refresh token) logs out — a 5xx or network error is a
  normal error. `/auth/login|refresh|logout` never get a bearer token or a retry.
- `composables/useMivoApi.ts` — every backend endpoint, typed against
  `app/types/api.ts`. Add new endpoints here rather than calling `apiRequest`
  directly from pages. `useSuperadminApi.ts` mirrors it for `/superadmin/*`.
- `middleware/auth.global.ts` — public paths are `/login` and `/privacy`; resolves
  the superadmin flag once per load and routes superadmins to `/superadmin`,
  everyone else away from it.
- `composables/useI18n.ts` + `app/locales/{uz,ru,en}.ts` — hand-rolled i18n, default
  `uz`, persisted in localStorage. Customer-facing strings in the backend
  (`_PHONE_CAPTURED_CONFIRMATION`, fallbacks) are localized separately.
- `functions/` — Cloudflare Pages Functions that proxy `/api`, `/integrations` and
  `/webhooks` to `API_UPSTREAM` (a Pages environment variable). `NUXT_PUBLIC_API_BASE`
  defaults to `/api`, which is what makes that proxy the production path.

## Environment

Each app has its own `.env` from its `.env.example`; the backend's README documents
the full first-deploy checklist (R2, OpenRouter, Meta app + webhook, Telegram bot,
VAPID, CORS). `app/core/config.py` has no default secrets: `JWT_SECRET` and
`FERNET_KEY` are required, and any value that ever appeared in this repo (or,
by SHA-256, leaked through git history) is rejected at startup.
