# Admin Console Revamp: Analysis & Implementation Plan

**Status:** analysis only. No application code has been changed.
- Revision 2 added SSO and role-based access, User Management, the Motion animation layer, and a page-by-page interactivity audit.
- **Revision 3 (2026-10-15)** re-validated the plan against `main`. It adds: OWASP Top 10:2025 controls (section 12); the async, concurrency, performance and load-balancing design (section 13); full white-labelling (section 14); a senior UI/UX gap audit with a dashboard-wide chart catalogue (section 15); and the CI and per-feature PR workflow (section 16). The phased execution checklist lives in `planchanges4.md`.

**Baseline:** the earlier working tree was merged to `main` as PR #13 (`9382178`). The old "commit 78 files" step is **done**. All new work branches from `main`, with one branch and one PR per feature, and never a direct push to `main` (section 16).
**Stack:** Next.js 14 (App Router) and Tailwind with a `brand` teal palette and `dark-*` tokens. Recharts 3 is already installed. The backend is FastAPI (fully async: asyncpg, `AsyncSession`, `AsyncAzureOpenAI`), Postgres/pgvector and Azure `gpt-5-mini`.

## Decisions already made (from your answers)

| Topic | Decision |
|---|---|
| Intent / quality / issue labels | **Hybrid.** Rules for obvious cases, plus an async LLM batch classifier, plus a backfill job. |
| Taxonomy | **DeployIQ-native and configurable.** The HTML's Exigo Tech labels (Careers, Staff lookup) are not reused as-is. |
| Old Evaluation page | **Remove from nav.** The route redirects to Evaluation (Live). Old code is kept for one release. |
| Analytics | **New sidebar page, native React port.** Same design, light and dark theme, fed by server-side aggregation endpoints. |
| Baseline commit | **Yes.** At the start of implementation, commit the current working tree as `feat(analytics): live evaluation console, interactive analytics, and enterprise theme system`. Nothing is committed now. |
| Feedback goal | **Human-feedback loop** (triage, golden tests, preference-pair export, retrieval and prompt signals), **with a fully interactive UI** (see section 5). |
| SSO provider | **Microsoft Entra ID**, OIDC, served from the frontend on **port 3000**. |
| Roles | **Superadmin, Admin, Operator**, tiered permissions (matrix in section 7.3). |
| Animation | **Motion** (formerly Framer Motion, the `motion` package) across all new and changed dashboard UI (section 8). This is a new dependency, added at your explicit request. |
| Execution order (rev 3) | **Backend first, then frontend.** Every backend API ships with tests before the UI that uses it (see `planchanges4.md`). |
| Delivery (rev 3) | **One feature equals one branch and one PR into `main`.** CI must be green and review approved before merge. Never push or merge directly to `main`. |
| Security baseline (rev 3) | **OWASP Top 10:2025** plus the OWASP LLM Top 10 for the RAG and classifier paths (section 12). |
| Performance (rev 3.1) | **Async-only pipeline.** The system must sustain **at least 50 concurrent requests**. That is a hard requirement, verified at 100 concurrent with headroom. Horizontal scaling sits behind nginx (section 13). |
| Redis (rev 3.1) | **Not used.** Shared state across workers and replicas (rate limits, login handshake state, live-update fan-out) lives in **Postgres**, which we already run (section 13.6). No scaling task is dropped. |
| Browser automation (rev 3.1) | **No Playwright, no automated accessibility scan, no Lighthouse and no ZAP for now.** End-to-end testing is done through the real HTTP stack with httpx (backend journeys plus a full-stack smoke through port 3000) and vitest UI journeys (section 16.2). |
| White-labelling (rev 3) | **Runtime-configurable branding** (name, logos, palette, favicon, contact), managed by a superadmin (section 14). |

## Open items (defaults assumed unless you object)

1. **Reference copy of the HTML:** ~~`backend/docs/analytics/`~~. `/backend/docs/` is **git-ignored**, so a copy there would never reach the repo or CI. The new default is `frontend/tests/fixtures/analytics/chatbot_insights_reference.html`, which is tracked and used by the visual check. It has hard-coded data and the 500 KB base64 favicon stripped, and is used only as a design spec. The root-level `ExigoTech_Chatbot_Insights_standalone.html` stays untouched until you say it can go.
2. **`page` / `pages` field in the HTML:** we do not capture the page URL a chat starts on. Default is to omit that column, with an optional later `X-Page-Path` header from the chat widget.
3. **Entra app registration (owner: you, later):** the config variables are already in `backend/.env.example` and `frontend/.env.local.example` (done). You will register the app and fill in the real values in your own `.env` files. Nothing in development waits on this (see "Execution guide").
4. **First superadmin (owner: you, later):** you set `SUPERADMIN_EMAILS` in your `.env` for your test. A listed email becomes an active superadmin on its first SSO login.
5. **Chat access (decided):** any signed-in user can use Chat with no approval, whatever their role or status (pending, rejected, no role). Only **suspended** users are blocked from everything, as a safety measure (assumption, say if you disagree). The **dashboard** needs an approved role. Unapproved users see a clear message with the admin contact email, which comes from `ACCESS_CONTACT_EMAIL` (you choose the address later).
6. **Existing "Users" page:** it shows visitor telemetry by client IP, not accounts. Default is to rename it "Visitors" in the sidebar so it isn't confused with the new "User Management".
7. **Shared state without Redis (decided, rev 3.1):** today the rate limiter, TTL cache and SingleFlight live in process memory. That is why `Dockerfile.prod` pins uvicorn to **1 worker**. The fix uses Postgres, with no new dependency (section 13.6):
   - **Rate limits:** an UNLOGGED `rate_limit_buckets` table with atomic `INSERT ... ON CONFLICT DO UPDATE` counters.
   - **OIDC handshake state:** an `oidc_states` table, single use.
   - **Live-update fan-out across replicas:** Postgres `LISTEN/NOTIFY`.
   - **Read caches:** stay per process (short TTL, invalidated on write), which is safe because each read is idempotent.

   This lifts the single-worker limit, so multi-worker and multi-replica load balancing stay in scope.
8. **Branch protection (owner, later):** a repo admin turns on the `main` protection rule in section 16.3. Until then, the agent follows the same rules voluntarily.
9. **GitHub CLI:** `gh` is not installed on this machine. Install it (`winget install GitHub.cli`, then `gh auth login`) to raise PRs from the terminal. The GitHub web UI works as a fallback.
10. **Ruff and pylint scope (rev 3):** `backend/pyproject.toml` selects only `E, W, F, B, C4, UP, SIM, RUF` and has **no pylint config**. CLAUDE.md asks for more (`N, PL, S, ASYNC, T20, I` and others). Following "never downgrade security", phase B0 adds `S`, `ASYNC`, `T20` and `I` and a pylint config, and fixes the findings in its own PR. `N` and `PL` are added with per-file ignores only where the existing code style needs them. Also, ruff's `exclude` lists `.venv` but **not `venv`**, the venv this repo actually uses. B0 adds it.

---

## Execution guide (for the agent that implements this, for example Claude Cowork)

This plan is written as requirements. The goal is that **nothing blocks development, linting or testing**. Read this section first.

**1. No question is needed to start.** All decisions are in "Decisions already made" and "Open items". Where something was assumed, the assumption is stated and the default is safe. Do not stop to ask the owner. Record any new assumption in the PR description.

**2. Things the owner will do later (never block on these):**
- Register the Entra app and fill in the real `ENTRA_*` values.
- Set `SUPERADMIN_EMAILS` and `ACCESS_CONTACT_EMAIL`.
- Create the real `.env` and `.env.local` files. The agent must never create, read, print or commit them.

**3. Development without Entra credentials.** Build the SSO phase against a **mock OIDC provider** selected by `AUTH_DEV_MOCK=true`:
- The mock exposes the same authorize, token and JWKS endpoints the real flow uses, signs ID tokens with a throwaway key, and lets a test choose the email, name, `oid` and `tid`. The real validation code (signature, `iss`, `aud`, `tid`, `exp`, `nonce`) runs against it unchanged.
- It is local-only. The app must refuse to start when `AUTH_DEV_MOCK=true` and `ENV` is not `development`, and a test covers that.
- Automated tests never call Microsoft and never need real secrets. Tests inject settings through environment variables or fixtures (for example `SESSION_SECRET=test-secret`), not through a `.env` file.
- **The mock must behave like real Entra**, so the same code works unchanged once the real values are in `.env` (rev 3.1):
  - **Endpoints:** v2.0 endpoints, the issuer format `https://login.microsoftonline.com/{tid}/v2.0`, and a discovery document at `/.well-known/openid-configuration` with the same shape as Microsoft's.
  - **ID token claims:** `ver: "2.0"`, `oid`, `tid`, `sub`, `name`, `preferred_username`, `nonce`, `aud = client id`, `iat`, `nbf`, `exp`.
  - **Email claim:** absent by default, because Entra often omits `email`. The app must fall back to `preferred_username`, and the test covers both cases.
  - **Signing:** an RS256 key with a `kid` in the JWKS. A key-rotation test serves a second `kid`.
  - **Code flow:** the authorization code is single use, PKCE S256 is verified, and errors return the Entra OAuth error shape (`error`, `error_description`).
- **Verification once real values exist (no code change needed):**
  - `backend\venv\Scripts\python.exe scripts\verify_sso_config.py` loads settings, fetches the real discovery document and JWKS, and checks that the issuer matches the tenant and that `ENTRA_REDIRECT_URI` equals `APP_BASE_URL + /auth/callback`. It prints PASS or FAIL per check **without printing any secret**.
  - Then sign in manually once (the go-live checklist in `planchanges4.md`, task B2.8).
  - A recorded copy of Microsoft's public discovery document is stored as a test fixture, so CI proves our parser handles the real format.

**4. Environment and tooling (follow the existing project, then CLAUDE.md):**
- The backend virtualenv in this repo is `backend\venv`, not `.venv`. Existing project convention wins, so use `backend\venv\Scripts\python.exe`, `...\ruff.exe` and `...\pylint.exe`. Do not create a second `.venv`. Never run bare `python`, `pip`, `pytest`, `alembic`, `ruff` or `pylint`.
- Backend: `python -m pytest`, `python -m alembic upgrade head`, `ruff check .`, `ruff format .`, `pylint` (all through the venv). `backend/pyproject.toml` already holds the config.
- Frontend (from `frontend/`): `npm run typecheck`, `npm run lint`, `npm test` (vitest), `npm run build`.
- Backend tests that touch the database use the existing `conftest.py` fixtures (transactional session, rolled back per test). If no database is reachable, run the pure-logic tests and say which DB tests were skipped. Do not mark a phase done on skipped tests without saying so.
- Do not add dependencies beyond these: `motion` (frontend, pinned) and one JWT/JWKS library on the backend (**PyJWT[crypto]**, pinned). Pin versions in `requirements.txt` and `package.json`.
  - **No Redis** (open item 7).
  - The background job queue is a Postgres table using `FOR UPDATE SKIP LOCKED`, so no Celery or arq.
  - Load tests use the existing `scripts/run_concurrency_test.py` and `run_admin_load_test.py` (httpx plus asyncio), extended rather than adding Locust.
  - **No Playwright, axe, Lighthouse or ZAP.** End-to-end testing uses httpx and vitest (section 16.2).
- Name new test files after the feature (`test_auth_sso.py`, `test_governance_traces.py`), not the phase. Phase numbers have been reused across plans and are already ambiguous.

**5. Definition of done for every phase:**
1. Code, Alembic migration (with a working downgrade) and tests are all in.
2. `ruff check`, `ruff format --check`, `pylint`, `pytest`, `tsc --noEmit`, `next lint`, `vitest` and `next build` all pass. Fix every issue rather than suppressing it.
3. New API routes have validation, authorisation (role guard), tests and correct OpenAPI docs.
4. Light and dark screenshots of each touched page are attached, and Motion respects reduced motion.
5. The relevant lines of "Verification checklist" (section 11) are ticked.

**6. Working rules:**
- One branch and PR per **feature** (section 16), branched from an up-to-date `main`. Never commit or push to `main` directly, and never merge your own PR before CI is green. (The baseline commit this line used to describe is already on `main` via PR #13.)
- Do not modify unrelated code. Follow existing patterns (`TableCard`, `TableToolbar`, `Pagination`, `DashboardHeader`, `useTableState`, `AudienceProvider`) before inventing new ones.
- Do not weaken security to make something pass. If a test fails because of a real defect, fix the defect.
- If an external dependency is unavailable (Azure, Entra, a database), build behind an interface, mock it in tests, and carry on with the rest of the phase.
- Append an "Implementation log" at the end of this file after each phase: what shipped, what was assumed, what is left.

---

## 0. Cross-cutting prerequisites

- ~~Commit the current working tree~~: **done** (PR #13). Instead, run the full backend and frontend checks on `main` once, so the baseline is known-good, and record the results in the B0 PR.
- Commit or discard the one pending change on `main` (`frontend/.env.local.example`) on its own branch, not on `main`.
- Harden CI and the PR workflow before any feature work (section 16).
- Add the `motion` dependency and the shared motion presets (section 8) before any UI phase.
- Pin the frontend to port 3000 (section 7.4) before the SSO phase.
- Create one shared `lib/theme-tokens.ts` / CSS-variable layer covering chart colors, status colors and surface and border tokens, used by every page below.
  - The HTML palette to match is teal `#008080`, dark teal `#00584F`, tint `#EAF6F5`, amber `#B7791F`, red `#C0362C`.
  - Dark mode comes from the existing `dark:` classes and `dark-bg`, `dark-surface`, `dark-card` and `dark-border`.
- Do not add dependencies. Recharts has `Treemap`, `Sankey`, `Bar`, `Line` and `Area`, which covers the d3 visuals in the HTML.
- Backend changes ship as Alembic migrations, in the existing style (`f6a7b8c9d0e1_add_message_feedback_table.py`).
- Verify every phase with `ruff check`, `ruff format` and `pylint` through `backend\venv\Scripts\...` (this repo's venv, see execution guide 4), pytest, and `vitest` plus a typecheck on the frontend.

---

## 1. Look and feel consistency (logo, light to dark)

### Findings: interactivity audit (validated against code)

I counted interaction hooks per page (`onClick`, `Link`, drawers, charts, stat cards, filters) and read the key ones. The counts are a heuristic, so step 1.1 confirms them visually in light and dark.

| Page | What it has today | What is missing | Where to connect it (data and components that already exist) |
|---|---|---|---|
| **Dashboard** `admin/` | Header, 8 stat cards, charts (24 chart references), 4 links. **No click handlers.** | KPI cards and chart bars don't click through. No time-range control. No live refresh. | Cards link to the page that owns the number, pre-filtered: Feedback to `admin/feedback`, Error rate to `governance?tab=Monitoring`, Cost to `usage`, Queries to `usage?tab=queries`. Chart bar click sets the filter. Reuse `useTableState` URL params. |
| **Users** (to be renamed Visitors) | Table, search, filters, row click to detail. No chart. | No KPI strip or trend. Test-IP status is read-only here. | Reuse `TestIpManager` for a "mark as test IP" row action. "View sessions" deep-links to `usage?tab=sessions&ip=...`, which `usage/page.tsx` already parses. Add new vs returning and activity-by-day charts. |
| **Visitor detail** `users/[id]` | Stat blocks, 2 links. **No `DashboardHeader`, no chart, no tabs.** | No activity timeline, sessions, queries, cost or feedback for this visitor. | Sessions table linking to `conversations/[id]`. Queries open the existing `QueryInspectorDrawer`. Feedback given by this visitor from `GET /feedback`. Cost from the usage endpoints. |
| **Conversations** | Table, search, filters. 1 stat, no charts. | No outcome or length overview. Row click navigates away and loses table state. | Row opens a side drawer with the turn timeline (keeps the list state), with "Open full view". Link to Analytics filtered by the same intent or outcome. |
| **Conversation detail** `conversations/[id]` | Back and 2 click handlers. **No `DashboardHeader`, uses un-themed `text-gray-900` (no dark mode).** | No per-turn inspection, feedback, cost or actions. | Per-turn stage latency, sources, flags and cost via `QueryInspectorDrawer`. Show the turn's feedback, "Promote to test" (existing endpoint), and the intent and quality labels (Phase 6). |
| **Documents** | Table, upload and delete handlers, 16 stat references. | No usage signal: which documents answers actually cite, and which topics have no document. | Retrieval hit counts per document from `chat_messages` sources. Knowledge-gap list from Feedback and Analytics linking back here. |
| **Document detail** `documents/[id]` | Expandable chunk cards. **No `DashboardHeader`.** | No chunk search. No signal on which chunks are used or tied to bad answers. | Chunk search and filter. Per-chunk hit count. "Low-rated answers that cited this chunk" linking to Feedback. |
| **Chat** | Session list, message bubbles, thumbs feedback. **No brand header.** Its own `<h1>`. | No role or identity display. Feedback has no reason chips. Admins can't jump from a message to its trace. | Brand header and signed-in user chip (section 7.7). Reason chips on thumbs down (section 5). Admin-only "View trace" link to `governance?tab=Traces&request_id=...`. |
| **Feedback** | Table, KPI cards, spike banner. | See section 5. | See section 5. |
| **Usage, Governance, Evaluation (Live)** | Tabs, tables, charts. | Covered in sections 3, 4 and 2. | |

**Connecting the dots.** The entities form one chain, and today most of it is not linked:

```
Visitor (IP / user) -> Sessions -> Conversations -> Turns (queries)
                                                     |-> Trace (stage latency, sources)
                                                     |-> Feedback (rating, reasons, triage)
                                                     |-> Insight labels (intent, quality, issue)
                                                     |-> Cost and tokens
                                                     |-> Evaluation record
                         Turns -> Documents / Chunks (cited sources) -> Knowledge gaps
```

- Every id or name in a table or chart renders through one shared `EntityLink` component with a defined deep-link, so any number can be traced to its rows:
  - `/admin/usage?tab=sessions&ip=`
  - `/admin/conversations/[id]?turn=`
  - `/admin/governance?tab=Traces&request_id=`
  - `/admin/feedback?message_id=` or `?triage=`
  - `/admin/analytics?intent=&quality=`
  - `/documents/[id]?chunk=`
- A shared `useDeepLink` hook reads and writes those params, so the browser back button and shared URLs restore the same view.
- Filters set on one page carry over when you click through (audience is already global through `AudienceProvider`).

### Plan

- **1.1** Take light and dark screenshots of every route and mark each against the table above. This is the acceptance checklist for the phase.
- **1.2** Put `DashboardHeader` (with a back breadcrumb, `max-w-[1600px] p-6 sm:p-8`) on the three detail pages and the chat page. Fix the un-themed classes in `conversations/[id]` (replace `text-gray-*` with the themed tokens).
- **1.3** Add KPI strips and charts to the list pages as in the table, using `StatCard` from `components/admin/insights`.
- **1.4** Build `EntityLink`, `useDeepLink` and the shared drawer shell, and wire the links in the table.
- **1.5** Make dashboard cards and chart elements clickable with the pre-filtered deep-links.
- **1.6** Audit hard-coded colors (`#f4fbfb`, `slate-*`, `gray-*`) against the shared tokens so light to dark transitions match the logo's teal.

### Logo size and visibility

**Root cause (to confirm in step 1.7):**

- `public/exigotech-logo.svg` has `viewBox="0 0 2209.88 1243.75"`, an aspect ratio of about 1.78.
- The tight PNG export (`exigotech-logo.png`) is 256x63, an aspect ratio of about 4.06.
- That strongly suggests the SVG artwork sits inside a large padded canvas.
- The sidebar renders it as `h-[64px] w-auto max-w-[191px] object-contain` inside an `h-20` header with `py-2`. That fits in the box, but the visible wordmark only fills a fraction of it. This matches the screenshots, where the logo is about 85 px wide.
- This is not a container clipping the logo. The container is already generous, and the asset itself is the limiting factor.

**Plan:**

- **1.7** Measure the real artwork bounding box with `getBBox`, or inspect the SVG, and re-crop the viewBox to the artwork. If the SVG is gradient-heavy, export a tight `exigotech-logo.svg` instead.
- **1.8** Sidebar: give the logo explicit dimensions (for example `h-10 w-auto`, `max-w-[200px]`) inside a fixed `h-16` header so it matches `DashboardHeader`'s `h-16` baseline.
- **1.9** Dark theme: `dark:brightness-110` is not enough for the dark teal gradient. Use a light or white variant via `<picture>` / a `dark:hidden` and `hidden dark:block` pair. `backend/docs/Exigo-Tech-Logo-AI.svg` and `Exigo-Tech-E-Logo-AI.svg` are candidates to check. The collapsed mark `exigotech-mark.svg` needs a dark variant too.
- **1.10** Use the same logo component on the login page and the favicon, so it is defined once.

---

## 2. Evaluation tab replacement

**Current state:**

- The sidebar has two items: "Evaluation" (`/admin/evaluation`, icon `ClipboardCheck`) and "Evaluation (Live)" (`/admin/evaluation-live`, icon `Sparkles`).
- The admin routes just re-export `(app)/evaluation/page.tsx` (706 lines) and `(app)/evaluation-live/page.tsx` (683 lines).

**Plan:**

- **2.1** Remove the "Evaluation" entry from `ADMIN_ITEMS` in `Sidebar.tsx`.
- **2.2** Keep the "Evaluation (Live)" entry with its `Sparkles` icon. Per your request it should have a proper icon next to it, so also confirm the icon renders in the collapsed rail with its tooltip.
- **2.3** `/admin/evaluation` and `/evaluation` redirect to `/admin/evaluation-live` through `redirect()` in the page file. The old page is left unmounted and removed next release.
- **2.4** Check that nothing else deep-links to the old route (`rg "/admin/evaluation\""`, dashboard cards, tests in `frontend/tests`). Update `Sidebar.test.tsx`, which asserts nav items.
- **2.5** Keep `EVALUATION_TABS` until the old page is deleted. Check that the benchmark and compare features the old page had (`EvaluationCompareMatrix`) are reachable from Live. If they are not, flag it before deleting anything.

---

## 3. Usage & Cost: replace Overview with Live Telemetry Studio

**Current state** (`admin/usage/page.tsx`): the tabs are `overview` (old `OverviewTab`, 397 lines), `live_telemetry` ("Live Telemetry Studio", `InteractiveOverviewTab`, 1003 lines, `Live` badge), `users`, `sessions` and `queries`.

**Plan:**

- **3.1** Remove the old `overview` tab and `OverviewTab` usage.
- **3.2** The `live_telemetry` tab becomes the first tab. Its content is unchanged ("as is").
  - Rename it to **Overview**.
  - Keep the `Sparkles` icon and the `Live` badge.
  - The tab id can become `overview`, with `live_telemetry` accepted as an alias in the URL parser.
- **3.3** The URL parser already handles `?tab=overall`. Make it also map the old `?tab=live_telemetry` so existing bookmarks work.
- **3.4** Delete `components/admin/usage/OverviewTab.tsx` after checking for other importers. Dashboard and tests may reference shared pieces.
- **3.5** Update any tests that reference the old tab ids.

---

## 4. Admin Governance: defects, missing data, interactivity

### 4.1 Defects found

I compared the five screenshots in `backend/docs/image for governance/` against `admin.py` and the view components.

| # | Where | Defect | Likely cause |
|---|---|---|---|
| G1 | Monitoring, Pipeline Latency | Y-axis labels clipped (`)00018`), values like 0.00018 s | Wrong unit handling (ms divided twice), and the axis `width={40}` is too narrow |
| G2 | Monitoring, Pipeline Latency | Embedding and Reranker bars are missing | `null` averages are plotted as 0 or dropped, with no "no data" state |
| G3 | Overall | Latency shows `0.00 s` while Monitoring shows `1.01 s` | The Overall tab reads `llm_latency_ms` only, and it is not the same unit or field as Monitoring's card |
| G4 | Overall | Traces `100 total` | It counts `traces.length`, and the API caps results with `limit=100`. It is not a real total. |
| G5 | Overall, Traces | Trace errors always `0` | The API hard-codes `"status": "ok"` for every trace |
| G6 | Monitoring | Error rate `0.00%` while AI Usage shows 330 failed out of 1,119 | `error_rate` uses in-memory `METRICS` counters. They reset on restart and are not audience-scoped. |
| G7 | Monitoring | Counts disagree: API Requests 138, Queries 45, AI Usage Requests 1,119 | Different sources and windows (in-memory counter, 24h latency rows, 30-day usage) with no labels saying so |
| G8 | Monitoring | Card grid is 3 + 2 with a dead slot, and card and chart widths do not align with the AI Usage panel | Layout only |
| G9 | Traces | Request ID and Trace ID columns are identical | `trace_id` is set to `request_id` |
| G10 | Traces, Logs | Client IP is always `127.0.0.1` | Local testing. Check `trust_proxy_headers` for deployed environments. |
| G11 | Logs | Message is a raw JSON string truncated at 300 chars, and service is always `query` | The API serializes `detail` as JSON and `service` is the audit action |
| G12 | Logs | The page fetches `limit=2000` and filters and paginates in the browser. The `total` is the DB count, but only 2000 rows are reachable. | No server-side pagination or filter |
| G13 | All | No auto-refresh state on Traces and Logs, and no drill-down | Monitoring polls every 10 s, but the others do not |

### 4.2 What each tab should show (key details currently missing)

**Overall** (health at a glance):

- A status banner (Healthy, Degraded or Incident), driven by thresholds on error rate, p95 latency and quota.
- KPI cards: requests, error rate, p50/p95 latency, active users, groundedness (from `online_quality_kpis`), blocked or flagged turns, cost today.
- A 24h trend sparkline per KPI.
- A recent incidents list (CRITICAL and ALERT logs) with links into Logs.
- Replace the plain `InfoRow` list with these cards. The old rows can remain as a compact secondary section.

**Monitoring:**

- Fix G1 to G3 and G6 to G8.
- Add p50, p95 and p99 per stage (embedding, retrieval, reranker, LLM, guardrails). The `LatencyRecord` data exists, and only the average is exposed today.
- A latency-over-time chart (hourly buckets, 24h) and a requests and errors timeline.
- Azure quota telemetry (already in the API payload as `azure_quota_telemetry`, but not shown as a chart or gauge).
- Process CPU and RAM gauges (already returned as `process_cpu_percent`, `process_ram_mb`).
- A guardrail flag breakdown (spam, unsafe input, low retrieval confidence, ungrounded) and an outcome mix.
- Online quality KPIs from `quality_tracker`.
- Persist error counts in the DB (derive from `audit_log` / `chat_messages.outcome = llm_failed`) instead of the in-memory counter.

**Traces:**

- Real `status` (ok, ungrounded, blocked, failed) derived from `chat_messages.outcome` and `guardrail_flags`.
- Columns: time, user, IP, query (truncated), outcome, total latency, slowest stage, tokens, cost. Drop the duplicate Trace ID column or give it real meaning.
- A trace detail drawer with a stage waterfall (embedding, retrieval, reranker, LLM, guardrails), retrieved sources and scores, flags, and a link to the conversation. `QueryInspectorDrawer` already exists in `components/admin` and should be reused.
- Filters: status, latency bucket, outcome and date range, in the single Filters popover.

**Logs:**

- Server-side pagination, search and level filter (`limit`, `offset`, `level`, `service`, `q`, date range).
- A structured message summary per action, instead of raw JSON, with an expandable JSON viewer.
- A service or category column that is meaningful (see 4.3).
- Level counts as chips (Critical, Alert, Error, Warning, Info).
- Export to CSV.

### 4.3 Missing log sources ("some logs are missing")

Today only `audit_log` rows are shown. Audited actions today: `query`, `ingest`, `document_delete`, `user_feedback`, `negative_feedback_spike`, `online_eval`, `feedback_promoted_to_eval_case`, `test_ip_added`, `test_ip_removed`, `usage_export`, plus session events in `sessions.py`.

Candidates to add (confirm each against the code as the first step of Phase 4):

- Authentication: login success and failure, register, token rejection.
- Rate-limit rejections (429) from `rate_limit.py`.
- Unhandled 5xx and exceptions from `middleware.py`.
- LLM failures and retries (`llm_call_failed`) as discrete entries, not only a flag inside a `query` event.
- Session create, rotate and expire.
- Admin actions, such as audience and test-IP changes. Some already exist.
- Document edits.

An audit-log row for each event already fits the immutability trigger migration (`d4e5f6a7b8c9`), so this is additive. Keep PII out of `detail` (there is `governance/pii.py`).

### 4.4 Standardization

- All tables use `TableCard` with `TableToolbar` (search plus the single Filters popover with only the filters that matter for that table) and `Pagination`. Governance Traces and Logs follow the same pattern as Feedback and Usage.
- Charts use a shared `ChartCard` (currently defined locally in `MonitoringView.tsx`, `evaluation/page.tsx` and elsewhere). Extract once into `components/admin/`.
- Theme: chart colors read from the shared tokens, so light and dark both work.

### 4.5 Backend work for Governance

- Add `offset`, `level`, `service`, `q`, `from` and `to` params to `GET /governance/logs`, and return the true `total` for the filter.
- Add `limit`, `offset`, `status` and `q` to `GET /governance/traces`, and add `total`, derived status, `outcome`, `tokens` and `cost`.
- Add `GET /governance/traces/{request_id}` (stage timings, sources, flags).
- Extend `GET /governance/monitoring` with percentiles, hourly series and persisted error counts.
- Add `GET /governance/overview` (health status, KPIs, incidents) so the Overall tab is one call.
- Keep the `audience` scope behavior the same as today.

---

## 5. Feedback: build it properly as an RLHF module

### 5.1 What this project is, and what RLHF means here

- This is a RAG assistant: the pipeline is chunk, embed, retrieve (pgvector), rerank, generate (Azure `gpt-5-mini`), then groundedness and guardrail checks. Every turn already logs outcome, grounded, guardrail flags, tokens, cost and stage latencies.
- The model is hosted, so "RLHF" cannot mean updating weights today. In practice it is a **human-feedback improvement loop**:
  1. Collect a signal (rating, reason, corrected answer).
  2. Triage and label it.
  3. Feed it into the system's levers: **golden regression tests** (already partly built), **retrieval and chunking fixes**, **prompt and guardrail tuning**, and **knowledge-base gaps** (missing documents).
  4. Export **preference pairs** (chosen vs rejected answer) so a later DPO or SFT run is possible.
  5. Use online evaluation scores as a reward proxy and check how well human ratings agree with them.

### 5.2 Current state

- Backend: `message_feedback` has rating 1-5, comment, actor and one row per message and actor. `GET /feedback`, `GET /feedback/analytics` and `POST /feedback/{id}/promote-to-test` exist. Promoted cases are written to `data/promoted_test_cases.json`, which `evaluation/dataset_extra.py` reads, so the loop is partly closed.
- Chat UI: thumbs up and down, optional comment. Thumbs map to rating 5 and 1.
- Admin page: 4 KPI cards, a spike banner and a table with search and a rating filter.

### 5.3 Gaps (why the page "misses data")

- **F1** The list returns only rating, comment, IP and IDs. It does not return the user's question, the assistant's answer, outcome, grounded flag, flags, sources, latency or cost. A reviewer cannot judge a rating without opening another page.
- **F2** `GET /feedback/analytics` already computes `feedback_by_outcome`, but the page never shows it.
- **F3** The "Total Feedback" card uses the filtered list `total`, so it changes when you search or filter. It should use the analytics total.
- **F4** "Closed-Loop RLHF: N Active" counts a client-side `promotedMap`. It resets on reload. Promotion state needs to be stored in the DB.
- **F5** The promoted-cases file path is relative (`data/`). Check that the folder is a persisted volume in `docker-compose*.yml`, otherwise promotions are lost on redeploy.
- **F6** No triage workflow (new, reviewed, promoted, dismissed), no reviewer, no notes.
- **F7** No reason tags and no "better answer" capture, and so nothing to build preference pairs from.
- **F8** No trend, no distribution, no breakdown by topic or intent, no knowledge-gap view.
- **F9** The actor (who rated) is stored but not returned or shown.

### 5.4 Plan

**Data model** (one Alembic migration):

- `message_feedback`: add `reason_tags` (JSON, for example `wrong`, `incomplete`, `off_topic`, `unsafe`, `too_slow`, `unclear`), `corrected_answer` (Text, nullable), `triage_status` (enum: new, reviewed, promoted, dismissed), `reviewed_by`, `reviewed_at`, `review_note`, `promoted_case_id`.
- Move promoted cases from the JSON file to a DB table (`eval_promoted_cases`) so promotion state is durable. Keep reading the JSON file as a fallback so existing promotions are not lost, and keep `dataset_extra.py` working.

**API:**

- Enrich `GET /feedback` with: `user_query`, `answer`, `outcome`, `grounded`, `guardrail_flags`, `sources`, `latency_ms`, `cost_usd`, `actor`, `triage_status` and `promoted_case_id`. Add filters for `triage_status`, `outcome`, `grounded`, `has_comment`, `date_from` and `date_to`.
- `GET /feedback/analytics`: add a daily trend, the rating distribution, `by_outcome`, `by_intent` (after Phase 6), top reason tags, **disagreement** counts (high rating but ungrounded, and low rating but grounded), and knowledge-gap candidates (low rating with low retrieval confidence).
- `PATCH /feedback/{id}` (triage, note), `POST /feedback/{id}/dismiss`, and `GET /feedback/export?format=jsonl` (preference pairs: prompt, chosen, rejected, rating, reason tags, source ids).
- Audit-log each admin action. `promote-to-test` already does.

**Chat UI:** on a thumbs down, show quick reason chips and an optional "what should it have said?" box. Thumbs up stays one click.

**Admin page layout** (tabs, same shell as Usage and Governance):

1. **Overview**: KPI cards (fix F3), satisfaction trend, rating distribution, feedback by outcome, top reasons, coverage, and the spike alert.
2. **Review Queue**: the table with filters in the single Filters popover. Click a row to open a detail drawer: question, answer, sources and scores, flags, rating, comment, reasons, a link to the conversation, and actions (Promote to test, Mark reviewed, Dismiss, Add note).
3. **Golden Cases**: promoted cases, with status, last evaluation result and a link to Evaluation (Live).
4. **Preference Data**: the pairs available for export, counts by quality, and a JSONL download.
5. **Knowledge Gaps**: low-rated and low-confidence topics, with a suggested document or chunk to add.

**Interactive UI for the feedback loop** (you asked for this explicitly):

- **Review Queue is a triage workspace, not just a table.** Keyboard shortcuts: `j` and `k` to move through rows, `p` promote to test, `d` dismiss, `r` mark reviewed, `Enter` to open the drawer. Multi-select with bulk actions (mark reviewed, dismiss, promote).
- **Side drawer** per feedback item with the full exchange (question, answer, cited sources with scores, flags, latency, cost), the rating, comment and reason tags, the intent and quality labels, previous and next navigation, and a "Write the correct answer" field that becomes the `chosen` side of a preference pair.
- **Clickable analytics:** clicking a rating bar, an outcome slice, a reason tag or a trend point filters the queue below. Active filters show as removable chips and sync to the URL.
- **Optimistic updates** with an undo toast. A promoted item animates out of the "new" queue and appears in Golden Cases.
- **Golden Cases tab** shows each promoted case's last evaluation result, with "Run now" for admins, and a link to Evaluation (Live).
- **Preference Data tab** previews pairs, flags incomplete ones (no corrected answer), and offers the JSONL download.
- **Knowledge Gaps tab** ranks topics by negative feedback and low retrieval confidence, with "Open document" and "Add document" links into Documents.
- Role gating: operators can view. Admins and superadmins can triage, promote and export (section 7.3).
- All of the above use the Motion presets in section 8.

---

## 6. Analytics (Chatbot Insights) integration

### 6.1 What the HTML is

`ExigoTech_Chatbot_Insights_standalone.html` is a 510 KB single file. Most of that is a base64 favicon and fonts. The real content is two scripts: an embedded dataset (`window.__EMBED__`) and a d3-based renderer. It shows:

- **Tabs:** Guide, Overview, Trend, Flow, Topics, Drop-off, Conversations.
- **Filters:** period (full, 7 days, 30 days, custom), user type, outcome, rating, search.
- **Charts:** daily volume, questions by hour of day, user type to outcome flow, question topics (treemap, sized by volume and shaded by handled %), how questions were answered (donut), where conversations dropped (lollipop), conversations per person (bubble).
- **KPIs:** conversations, substantive questions, questions handled %, conversations served %, probable people, average questions per conversation.
- **Table:** conversations, with expandable per-turn detail.
- **Data:** 49 conversations and 117 exchanges. This is Exigo Tech website-bot data from May to July 2026, not DeployIQ data.

### 6.2 Field mapping and gaps

The renderer needs two record types. Today the app has the raw material (messages, outcome, grounded, flags, session, IP) but not the labels.

**Conversation record:**

| HTML field | Source in our app | Gap |
|---|---|---|
| `conv_id`, `start`, `end`, `minutes`, `exchanges` | `chat_sessions` and `chat_messages` | None. Compute. |
| `person` ("probable person") | Hash of `user_email` plus `created_ip` | Define the rule. |
| `substantive_exchanges` | Turns that are not greeting or ack | Needs intent label |
| `user_type` | Dominant intent group of the conversation | **Missing**, needs classifier |
| `outcome` (Served, Partly served, Not served, Greeting only) | Derived from turn outcomes | **Rule needed** (see 6.3) |
| `experience_rating` (Great, Good, Partly served, Dropped) | Derived. User ratings can override. | **Rule needed** |
| `issue`, `failing_question` | Last unhandled turn plus its issue label | **Missing**, needs classifier |
| `questions_asked` | `chat_messages` user rows | None |
| `pages` | Not captured | **Gap**, omit for now (open item 3) |
| `friction` | Hostile closing or abandonment | **Missing**, optional (LLM flag) |
| `policy_flag` | Guardrail flags | Partial (`guardrail_flags`) |
| `touched_careers` | Domain flag | Drop, or make it a configurable "touched topic" |

**Exchange (turn) record:**

| HTML field | Source in our app | Gap |
|---|---|---|
| `turn`, `timestamp`, `user_message`, `conv_id` | `chat_messages` | None |
| `intent` | None | **Missing**, classifier |
| `bot_quality` (OK, PART, OFF, ACK, CORR, CLAR, FB, SCOPE, FALSE) | Partly derivable from `outcome` and `grounded`. Nuance needs the classifier. | **Partial** |
| `substantive` | From `intent` | Derived |
| `handled` (Yes or No) | `outcome == answered` and `grounded` | Derivable |
| `issue` | None | **Missing**, classifier |

**Outcome mapping (deterministic part):**

| Our `outcome` | `bot_quality` | `handled` |
|---|---|---|
| `answered` and grounded | OK | Yes |
| `answered` or `ungrounded` with weak evidence | PART or FB | No |
| `blocked` | CORR (policy) or SCOPE (off-topic) | Yes if it was a correct refusal |
| `conversational` | ACK | Yes (not substantive) |
| `llm_failed` | OFF | No |

### 6.3 Plan

**Storage** (one Alembic migration):

- `turn_insights`: `message_id` (FK to the assistant message), `intent`, `bot_quality`, `issue`, `substantive`, `handled`, `confidence`, `classified_by` (rule or llm), `model`, `classified_at`. Indexed on `intent`, `bot_quality` and `classified_at`.
- `insight_taxonomy`: configurable lists for intent codes, labels and colors, bot-quality values and issue values, so the DeployIQ list can change without a deploy. Seeded in the migration.
- Conversation-level fields (`user_type`, `outcome`, `experience_rating`, `issue`, `failing_question`) are **computed at query time** from `turn_insights` plus `chat_messages`, not stored. This avoids a second source of truth.

**Taxonomy codes (rev 3.1):** keep the HTML's code style and its non-substantive codes exactly. **`GRE` = Greeting** and **`ACK` = Closing**: the renderer's `isSub()` treats every other code as substantive. Keep the bot-quality codes unchanged (`OK, CORR, SCOPE, PART, OFF, FB, CLAR, FALSE, ACK`), since they are domain-neutral. Replace only the website-specific intents (`CAR` Careers, `STAFF` Staff lookup, `PRIV` Private person) and issues (`No live job openings`) with DeployIQ ones.

**Taxonomy (DeployIQ-native, editable):** Platform and Services, Security and Compliance (Zero Trust, Essential Eight), Responsible AI and Trust, Company Facts, Contact and Pricing, Partner or Quote request, Support, Off-scope, Prompt injection, Ambiguous, Other, Greeting, and Closing (ack). The final list is confirmed with you before the seed migration is written.

**Classifier (hybrid):**

- Rules: greetings and acks (reuse `guardrails/conversational.py`), spam (`pre_checks.check_spam`), prompt injection and unsafe input (existing flags), blocked, `llm_failed`.
- LLM batch classifier for the rest: small `gpt-5-mini` call, JSON schema output, constrained to the taxonomy values, several turns per call. It runs async (background task, not in the request path) and the cost is recorded through the existing costing module so Usage & Cost sees it.
- Backfill script (like `scripts/run_evaluation.py`) to label existing history, with `--limit` and `--dry-run`.
- PII: send only the user message and a truncated answer, and mask through `governance/pii.py` first.
- Unit tests with the LLM mocked (the repo's tests are mocked and run in about 2 seconds).

**Endpoints** (`/admin/insights/*`, same audience scoping and auth as other admin routes, with server-side aggregation instead of the HTML's in-browser computation):

- `summary` (KPIs), `trend` (daily volume), `hourly`, `flow` (user type to outcome), `topics` (volume and handled % per intent), `quality` (how questions were answered), `dropoff`, `people` (conversations per person), `conversations` (paginated, searchable, with per-turn detail on expand).
- Shared query params: `period`, `from`, `to`, `user_type`, `outcome`, `rating`, `intent`, `quality`, `q`, `audience`.
- Add the Guide tab content as static text explaining each metric, adapted to our definitions.

**Frontend** (new route `admin/analytics`, sidebar item "Analytics" with a chart icon):

- Components in `components/admin/analytics/`: KPI strip, `TrendChart`, `HourOfDayChart`, `FlowSankey`, `TopicsTreemap`, `QualityDonut`, `DropoffChart`, `PeopleBubble`, `ConversationsTable` (with `TableCard`, `TableToolbar` and `Pagination`), and a filter bar.
- Same layout and chart types as the HTML. Recharts replaces d3 (`Treemap`, `Sankey`, `PieChart`, `BarChart`, `ScatterChart`). Colors map to the shared tokens so dark mode works.
- Tabs through the existing `TabGroup`. Add `ANALYTICS_TABS` there.
- Cross-links: clicking a topic or drop-off filters the conversations tab. A conversation row links to `admin/conversations/[id]`.
- An empty state for unlabeled history ("N turns pending classification", with a progress indicator).

**Reference copy:** save the sanitized HTML to the folder in open item 1, with the embedded sample data and favicon stripped. It is the design spec and the visual regression reference.

### 6.4 Connecting the HTML to our data (rev 3.1, read from the HTML source)

I read the renderer script line by line (`metrics()`, `scope()`, `passConv()`, `isSub()`, `trend()`, `radial()`, `flow()`, `treemap()`, `donut()`, `lollipop()`, `bubble()`, `table()`, `detailBox()`). Its embedded data holds **49 conversations and 117 exchanges**. Every number in the Analytics page must reproduce these formulas from our tables.

**Unit mapping:**
- HTML **conversation** = one `chat_sessions` row. A session already ends after `session_timeout_minutes` (60) of inactivity, which is the same idea as the HTML's conversation.
- HTML **exchange** = one `chat_messages` row with `role='user'`, paired with the next `role='assistant'` row in the same session (`turn_index`). Labels live on the assistant row's `turn_insights`.

**Field mapping:**

| HTML field | Our source (exact) |
|---|---|
| `conv_id` | `chat_sessions.id` (shown shortened, linked to `/admin/conversations/{id}`) |
| `person` | Stable pseudonym. After SSO it is `sha256(user_email)`. For older rows it is `sha256(created_ip + user_agent bucket)`. Shown as `P01…` per result set, so no PII is displayed. |
| `start`, `end`, `minutes` | `min(created_at)` and `max(created_at)` of the session's messages, and their difference |
| `exchanges` | Count of user messages |
| `substantive_exchanges` | Count of user messages whose `intent ∉ {GRE, ACK}` |
| `questions_asked` | `string_agg(content, ' \| ' ORDER BY turn_index)` over user messages, PII-masked |
| `timestamp`, `turn`, `user_message` | `created_at`, `turn_index`, `content` (PII-masked) |
| `intent`, `bot_quality`, `issue`, `handled`, `substantive` | `turn_insights` (rule plus LLM). `handled` = `bot_quality ∈ {OK, CORR}` and, for `OK`, `grounded = true`. |
| `user_type` | Mode of the session's intent **groups** (the taxonomy `group` column, for example Customer or prospect). "Mixed" if no group holds at least 60 % of the turns. "Greeting only" if there are no substantive turns. |
| `outcome` | "Greeting only" if there are 0 substantive turns. "Served" if every substantive turn is handled. "Not served" if none are. "Partly served" otherwise. |
| `experience_rating` | Feedback override first: any rating ≤ 2 gives "Dropped", and a rating of 5 on a served session gives "Great". Otherwise derived: Served gives "Good"; Served with `friction` gives "Served with friction"; Partly served gives "Partly served"; Not served gives "Dropped"; and no question gives "No question asked". |
| `issue`, `failing_question` | The `issue` and `user_message` of the **last** unhandled substantive turn |
| `friction` | An LLM flag on the closing turn (hostile or abandoned), stored in `turn_insights.friction` |
| `policy_flag` | Readable text from `guardrail_flags` (for example `pii_detected`, `blocked_unsafe`) |
| `pages` | Not captured. Hidden (open item 2). |
| `touched_careers` | Generalised to "touched topic X" via a taxonomy flag. Hidden by default. |

**KPI formulas (copied from `metrics()`):**
- **Conversations** = sessions in scope.
- **Substantive questions** = exchanges in scope with `isSub`.
- **Questions handled %** = handled ÷ substantive.
- **Conversations served %** = Served ÷ conversations that are not "Greeting only".
- **Probable people** = distinct `person`.
- **Average questions per conversation** = Σ `substantive_exchanges` ÷ active conversations.

**Charts:**

| Chart | Formula | Endpoint |
|---|---|---|
| Daily trend | Substantive questions per day, plus conversations per day. Clicking a day sets from = to = that day, and clicking again clears it. | `/admin/insights/trend` |
| Questions by hour (radial) | Substantive exchanges grouped by hour of `created_at`, **in the viewer's timezone** (`tz` parameter, default `Australia/Sydney`) | `/hourly` |
| Flow | `user_type` to `outcome`, excluding "Greeting only" | `/flow` |
| Topics treemap | Per intent: size = substantive count, colour = handled % on a red `#C0362C` to teal `#008080` scale | `/topics` |
| How questions were answered (donut) | `bot_quality` counts in the order `OK, CORR, SCOPE, PART, FB, CLAR, OFF, FALSE` | `/quality` |
| Where conversations dropped (lollipop) | `issue` counts over sessions with outcome Partly served or Not served | `/dropoff` |
| Conversations per person (bubble) | Conversations per `person`, coloured by that person's most common outcome | `/people` |
| Conversations table | Columns Conv, Person, Started, Type, Outcome, Rating, Issue, Qs. Sortable, 8 rows per page in the HTML (we use 25 with `Pagination`). The expand view lists the turns with time, intent, handled, quality and issue. | `/conversations` |

**Filters (from `passConv()` and `scope()`):** period (all, 7d, 30d, custom), user type, outcome, rating, **issue**, **intent**, **quality**, **person**, and free text over id, person, type, issue, failing question and questions asked. The intent and quality filters keep a conversation if **any** of its turns match, and they also narrow the exchange set. The server reproduces exactly these semantics, and a parity test checks them (`planchanges4.md` B6.4).

**Guide tab:** the HTML has a step-through guide (`STEPS`, `gShow`) that highlights each section. We port it as a dismissible "Tour" on the Guide tab, rewritten for our definitions.

**Parity fixture:** `backend/tests/fixtures/insights_reference.json` holds the HTML's 49 conversations and 117 exchanges, with names already masked in the source. It is loaded into `chat_sessions`, `chat_messages` and `turn_insights` (labels taken from the fixture, not the classifier), and every endpoint must return the HTML's numbers:
- Outcomes: Served 11, Partly served 14, Not served 22, Greeting only 2.
- Exchanges: handled 62 out of 117.
- Top drop issue: "No live job openings", 13 conversations.

Only for this fixture, the taxonomy seed includes the HTML's original codes, so parity is exact.

---

## 7. SSO login, roles and User Management

### 7.1 Current state (why this is a security fix, not just a feature)

- There is **no real authentication**. `frontend/lib/auth-context.tsx` returns a hard-coded Guest user and token (`default@local`, `no-auth`), and its comment says login and registration were removed.
- The Next.js proxy (`app/api/proxy/[...path]/route.ts`) injects one shared `X-API-Key` into every backend call. Anyone who can reach port 3000 therefore has full admin access, including documents, users, usage and governance data.
- `get_current_actor` (in `governance/auth.py`) only checks that shared key. The `X-Client-Id` header is client-controlled and is documented as "not a security boundary".
- `(app)/layout.tsx` redirects to `/login` when there is no token, but the token is always set, and there is no `/login` route in `app/`.
- A `users` table exists (`full_name`, `email`, `password_hash`) but nothing uses it for sign-in. `api_jwt_secret` is still in config, as a leftover.
- About 44 route dependencies use `get_current_actor`, so the guard can be swapped in one place.

### 7.2 Target behaviour

1. A visitor opens `http://localhost:3000`. With no valid session they are sent to `/login` and see **nothing** (no dashboard, no chat).
2. They click "Sign in with Microsoft". Entra ID authenticates them (OIDC authorization code flow with PKCE).
3. The backend verifies the token and looks the user up in Postgres:
   - **Known, active, with a role:** a session is created and they reach the dashboard (and Chat).
   - **New or no role (status `pending`) or rejected:** a user row is created, and they land on **Chat**, which works normally. Any dashboard URL shows the **Access Required** page instead: "Dashboard access needs approval", a **Request access** button (with optional justification), their request status, and the admin contact email from `ACCESS_CONTACT_EMAIL`. They never see dashboard data, and the API returns 403 for every dashboard route.
   - **Suspended:** a clear "account suspended" screen with the same contact email. No Chat, no dashboard.
4. A **superadmin** reviews requests in **User Management**, approves with a role (or rejects with a note), and the database is updated. The user's next request is authorised, and the Access Required page refreshes to the dashboard on its own.
5. Access is checked **server-side against the database on every request**, not only in the browser. Role or status changes take effect immediately, including suspension.

### 7.3 Roles and permissions (tiered)

| Capability | Operator | Admin | Superadmin |
|---|---|---|---|
| Dashboard, Usage & Cost, Governance, Analytics, Conversations, Visitors (view) | yes | yes | yes |
| Chat (also open to signed-in users with no role) | yes | yes | yes |
| Documents: view | yes | yes | yes |
| Documents: upload, delete | no | yes | yes |
| Feedback: view | yes | yes | yes |
| Feedback: triage, promote to test, export | no | yes | yes |
| Evaluation (Live): view | yes | yes | yes |
| Evaluation: run, promote cases | no | yes | yes |
| Test-IP configuration, usage export | no | yes | yes |
| **User Management**: users, roles, access requests, suspend | no | no | **yes** |
| View the auth audit trail | no | no | yes |

This is the "Tiered" option you chose. Permissions are defined once in a single backend table (`permissions.py`), and the frontend reads the same list from `GET /auth/me`, so the UI hides what the API would reject. Hiding in the UI is a convenience. The API enforces it.

### 7.4 Architecture

**Port 3000.** The SSO flow runs through the frontend origin:

- Login page: `http://localhost:3000/login`.
- Redirect URI registered in Entra: `http://localhost:3000/auth/callback` (dev), plus the production HTTPS URL.
- Pin the port so it can't drift: `next dev -p 3000` and `next start -p 3000` in `package.json`. `docker-compose.yml` already maps `3000:3000`, and `cors_allowed_origins` already includes `http://localhost:3000`. Add `APP_BASE_URL` to config so redirects aren't hard-coded.

**Flow (the backend is the authority, the frontend is a thin client):**

1. `GET /login` renders the sign-in page. The button calls `GET /api/proxy/auth/sso/login`.
2. The backend creates `state`, `nonce` and a PKCE verifier (stored short-lived server-side), and returns the Entra authorize URL. The browser is redirected there.
3. Entra redirects to `http://localhost:3000/auth/callback?code=...&state=...`. This Next route handler forwards `code` and `state` to `POST /auth/sso/callback` on the backend.
4. The backend exchanges the code **using the client secret, which stays on the server**, then validates the ID token: signature through Entra's JWKS, `iss`, `aud`, `tid` (our tenant only), `exp` and `nonce`.
5. The backend upserts the user in Postgres (keyed on the Entra object id `oid`, with email, name and tenant), applies the status rules from 7.2, and creates a row in `auth_sessions`.
6. The session id is returned to the Next handler, which sets it as an **httpOnly, Secure (in prod), SameSite=Lax** cookie. The browser never sees an access token or the client secret.
7. The proxy forwards the session to the backend together with the shared API key, so the backend can tell "request from our proxy" apart from "request from a stranger". The backend resolves the session, loads the user and enforces role and status each time.
8. Next `middleware.ts` redirects page requests with no session cookie to `/login`. That is a UX redirect, not the security boundary. The security boundary is step 7.

**The shared `X-API-Key` stays only as proxy-to-backend service authentication.** It no longer grants user-level access by itself. Unauthenticated calls to every dashboard route return 401, and calls from an active user without the right role return 403.

### 7.5 Postgres data model (one Alembic migration)

- **`users`** (extend the existing table): `entra_oid` (unique), `tenant_id`, `role` (enum `superadmin | admin | operator`, nullable until approved), `status` (enum `pending | active | rejected | suspended`), `last_login_at`, `approved_by`, `approved_at`. `password_hash` becomes nullable (legacy local accounts are not used for sign-in).
- **`access_requests`**: `id`, `user_id`, `requested_role`, `justification`, `status` (pending, approved, rejected), `decided_by`, `decided_at`, `decision_note`, `created_at`. One open request per user.
- **`auth_sessions`**: `id`, `user_id`, `created_at`, `expires_at`, `last_seen_at`, `revoked_at`, `ip_address`, `user_agent`. Lets a superadmin force sign-out and lets sign-out invalidate the session on the server.
- **Audit events** (inserts into the existing immutable `audit_log`): `login_success`, `login_denied`, `access_requested`, `access_approved`, `access_rejected`, `role_changed`, `user_suspended`, `user_reactivated`, `session_revoked`, `logout`. These also appear on the Governance Logs tab (section 4.3).
- `chat_sessions.user_email` and feedback `actor` currently hold a client-generated id. After this change they hold the authenticated email, so per-user history, rate limits and feedback attribution become real. Older rows keep their old values.

### 7.6 Backend work

- New `app/api/routes/auth.py`: `GET /auth/sso/login`, `POST /auth/sso/callback`, `GET /auth/me`, `POST /auth/logout`, `POST /auth/access-request`, `GET /auth/access-request` (own status).
- New `app/api/routes/user_management.py` (superadmin only): `GET /admin/users-accounts` (paginated, search, filter by role and status), `GET /admin/access-requests`, `POST /admin/access-requests/{id}/approve` (with role), `POST /admin/access-requests/{id}/reject`, `PATCH /admin/users-accounts/{id}` (role and status), `POST /admin/users-accounts/{id}/revoke-sessions`, and an invite or pre-provision endpoint by email.
- Replace `get_current_actor` with a dependency family: `current_user`, `require_active_user`, and `require_permission("documents:write")`. Apply per router, with read routes at operator level and write routes at admin level. A test walks every route and fails if one has no guard.
- Safeguards: you can't demote or suspend yourself, and you can't remove the **last active superadmin**. Role changes and approvals are transactional and audited.
- Config: add the variables below to `core/config.py` (`pydantic-settings`) **and to `backend/.env.example` and `frontend/.env.local.example` as part of the SSO phase**. Nothing has been added to any `.env*` file yet. Never create, read or print a real `.env`. The owner fills in real values later.

| Variable | Where | Example placeholder | Default in code | Notes |
|---|---|---|---|---|
| `ENTRA_TENANT_ID` | backend | `your-entra-tenant-id` | none (required unless `AUTH_DEV_MOCK=true`) | Single-tenant check on the `tid` claim |
| `ENTRA_CLIENT_ID` | backend | `your-entra-app-client-id` | none (same rule) | |
| `ENTRA_CLIENT_SECRET` | backend | `your-entra-app-client-secret` | none (same rule) | Server-side only, never sent to the browser |
| `ENTRA_REDIRECT_URI` | backend | `http://localhost:3000/auth/callback` | none | Must match the Entra registration exactly; port 3000 |
| `APP_BASE_URL` | backend and frontend | `http://localhost:3000` | `http://localhost:3000` | Post-login redirects |
| `SESSION_SECRET` | backend | `your-random-session-signing-secret` | none (required) | Generate with `secrets.token_hex(32)` |
| `SESSION_TTL_MINUTES` | backend | `480` | `480` | |
| `SESSION_COOKIE_NAME` | backend and frontend | `askdiq_session` | `askdiq_session` | Must match on both sides |
| `SESSION_COOKIE_SECURE` | backend and frontend | `false` | `false` | `true` over HTTPS |
| `SUPERADMIN_EMAILS` | backend | empty (comma-separated list) | empty | Owner sets this for their test. These emails become active superadmins on first login. |
| `ACCESS_CONTACT_EMAIL` | backend | empty | empty | Admin contact shown on the Access Required page. Owner chooses later. If empty, the page says "contact your administrator". |
| `AUTH_DEV_MOCK` | backend | `false` | `false` | Local-only mock identity provider (see "Execution guide"). The app refuses to start if it is `true` and `ENV` is not `development`. |
- Dependencies: one new backend library for JWT and JWKS validation (PyJWT with its crypto extra, or Authlib). `httpx` is probably already present for the code exchange, and I will check. No frontend auth library is needed, because the Next side is just route handlers and a cookie.
- `trust_proxy_headers` must be on behind the Next proxy so audit logs get the real client IP, not 127.0.0.1.

### 7.7 Frontend work

- **Login page design (rev 3.2, from your reference screenshot):** a two-panel split screen. It keeps the reference's structure but uses **our logo and the teal palette, not purple**.
  - **Left brand panel (about 53 % width, hidden below `md`):**
    - Background: a teal gradient from `brand-600` `#008080` to `brand-800` `#00584F`, with a soft radial highlight.
    - Decoration: thin translucent **hexagon outlines** echoing the logo's geometry, plus 2 large faint concentric rings.
    - Logo: the white (dark-variant) logo, top left.
    - Text: a small uppercase eyebrow (`login_eyebrow`, for example "ASKDIQ"), a large headline (`login_headline`), supporting copy (`login_subtext`), and a `© {year} {company_name}` footer.
  - **Right panel:**
    - Card content: the heading "Welcome to {product_name}" and a one-paragraph description.
    - A full-width **"Sign in with Microsoft"** button: Microsoft four-square logo, 1 px border, rounded-xl, hover lift and brand-tinted border, focus ring, and a loading spinner after the click.
    - A "SINGLE SIGN-ON" divider.
    - The text "Access is provisioned by your {company_name} administrator. If you can't sign in, contact {ACCESS_CONTACT_EMAIL}" (a `mailto:` link in brand colour).
    - A footer with Privacy Policy, Terms of Service and Help links (`privacy_url`, `terms_url`, `help_url` from branding, each hidden when empty).
    - In dark mode the right panel uses `dark-bg` and the card text tokens, and the left panel keeps the teal gradient. Both pass AA contrast.
  - **Motion (`motion` presets):**
    - The headline, copy and card enter with a staggered `fadeUp`.
    - The hexagons drift and rotate very slowly (20 to 40 s loops, `transform` only) and follow the pointer with a subtle parallax.
    - The button gets a sheen sweep on hover, and the error banner uses `pop`.
    - With reduced motion, everything is static.
  - **White-label:** every string, URL and logo comes from `/branding` (section 14). Nothing is hard-coded to Exigo Tech.
  - **Mobile:** a single column, with the teal header strip and logo above the card.
- **Pages:** `/login` (brand, sign-in button), `/auth/callback` (handler), the **Access Required** page (shown in place of any dashboard route for users without an approved role: message, request form, status, `ACCESS_CONTACT_EMAIL`), and an account-suspended page. All use the shared logo and theme.
- **`auth-context.tsx`:** replace the Guest stub with real state from `GET /auth/me` (`user`, `role`, `status`, `permissions`). `useAuth` keeps the same shape (`token`, `user`, `isLoading`, `logout`) so existing pages keep working.
- **Route gate** in `(app)/layout.tsx`: unauthenticated goes to `/login`. Signed-in users without an approved role can use `/chat`, and every dashboard route renders the **Access Required** page (message, request button, status, `ACCESS_CONTACT_EMAIL`). Suspended users get the suspended page. Only active users with a role see the dashboard shell and full sidebar. Users without a role see a minimal sidebar (Chat only). `GET /auth/me` returns the contact email so the UI never hard-codes it.
- **`<RequirePermission>`** wrapper and `usePermission()` hook for buttons and sections (upload, delete, triage, run evaluation).
- **Sidebar:** items are filtered by role. The profile area shows real name, email and a role badge. Sign out calls `POST /auth/logout`.
- **Rename** the existing "Users" item to **Visitors** (open item 6).

### 7.8 User Management (new sidebar item, superadmin only)

Icon `UserCog` with a **pending-requests badge** showing the open count. Same shell as the other pages (`DashboardHeader`, `TabGroup`, `TableCard`, `TableToolbar`, single Filters popover, `Pagination`).

| Tab | Contents |
|---|---|
| **Users** | Table: name, email, role, status, last login, approved by. Search plus Filters (role, status). Row drawer: profile, active sessions, recent activity, audit entries. Actions: change role, suspend or reactivate, revoke sessions, invite by email. |
| **Access Requests** | Queue of pending requests (justification, requested role, time waiting). Approve (choose role) or Reject (with note) in the drawer or inline. Bulk approve. History of decisions. |
| **Roles & Permissions** | Read-only matrix from 7.3, generated from the same permissions table the API uses. |
| **Audit Trail** | Filtered view of the auth audit events: who approved whom, role changes, denied logins. |

KPI strip above the tabs: total users, active, pending (highlighted), suspended, and logins in the last 24h.

### 7.9 Tests

- Backend: role guard for every route (401 with no session, 403 with the wrong role, 200 with the right one), access-request state machine, last-superadmin protection, session revocation, token validation failures (wrong tenant, expired, bad nonce, bad signature), and bootstrap through `SUPERADMIN_EMAILS`. OIDC calls are mocked, like the existing tests.
- Frontend: gate redirects for each status, sidebar filtering by role, and permission wrappers (vitest).
- End to end: sign in as a new user, request access, superadmin approves, the user reaches the dashboard. Run against a mock OIDC provider until the Entra registration exists.

---

## 8. Motion: interactive animation layer

You asked for Motion (the `motion` package, formerly Framer Motion) on all dashboard changes. It is a new dependency, added at your explicit request. The goal is that animation explains the interface (what changed, where it came from), and does not slow down dense admin screens.

**Setup:**

- Add `motion`, pinned to an exact version (CLAUDE.md asks for pinned dependencies). Use `LazyMotion` with `domAnimation` and the `m` components to keep the bundle small. Everything is a client component (Next.js App Router).
- `<MotionConfig reducedMotion="user">` at the app root, so users with "reduce motion" get no movement. Tests cover this.
- A single `lib/motion.ts` holds presets: durations (fast 120 ms, base 200 ms, slow 320 ms), one spring, one ease, and `fadeUp`, `stagger` and `drawer` variants. Pages import presets and never hard-code timings.

**Where it is used:**

| Area | Motion |
|---|---|
| Page and tab changes | Short fade and 8 px rise on tab panels. A shared-layout `layoutId` pill that slides under the active tab in `TabGroup` and the audience tabs. |
| KPI and stat cards | Staggered entrance. Numbers count up on first load and when filters change. Delta arrows and the spike banner animate in. |
| Charts | Animated entrance and filter transitions (Recharts animation props, wrapped in motion containers). Hover and focus states on bars, slices and treemap cells. |
| Tables | Row hover lift. Expand and collapse height animation for row detail. Smooth removal with `AnimatePresence` on triage actions (a promoted or dismissed feedback row slides out). Skeleton rows that crossfade into data. |
| Drawers and modals | Slide-in drawers (conversation preview, trace inspector, feedback review, user detail), with a backdrop fade and focus trap. |
| Access flow | Request Access status steps (requested, under review, approved) animate. On approval the screen transitions into the dashboard. |
| Sidebar | Collapse and expand width, the active-item indicator, and the pending-requests badge pulse. |
| Feedback and toasts | Reason chips pop in on thumbs down. Success and error toasts for triage actions. |
| Governance | Live status banner color transition. New incident rows highlight briefly. |

**Rules:**

- Keep motion under about 300 ms. No animation in the middle of dense table scrolling or on every row of a large table.
- Animate `transform` and `opacity`, not layout, except for the deliberate expand and collapse.
- Every animated control stays keyboard and screen-reader accessible. Never hide state behind motion alone.
- Light and dark themes share the same motion. Colors come from tokens.

---

## 9. Suggested order and effort

> **Superseded in revision 3.** You asked for backend first and frontend later, so the order is now the B and F phases in `planchanges4.md` (section 16.5 summarises it). The table below is kept for history: it explains why SSO comes before everything else.

| Phase | Scope | Depends on | Size |
|---|---|---|---|
| 0 | Baseline commit, port 3000 pin, `motion` setup and presets, shared tokens, shared `ChartCard` | n/a | S |
| 1 | **SSO, roles, access requests, User Management** (section 7). Closes the open-access hole, and every later page then gets role gating. | 0, Entra app registration (open item 3) | XL |
| 2 | Evaluation nav replace and redirect | 1 for sidebar role filtering | XS |
| 3 | Usage & Cost tab swap | n/a | XS |
| 4 | Logo fix, header, theme and interactivity on all pages, `EntityLink` and deep-links (section 1) | 0 | M-L |
| 5 | Governance: defects (G1-G13), new data, server pagination, missing log sources (section 4) | 0, 1 | L |
| 6 | Feedback loop module, with the interactive triage UI (section 5) | 0, 1 | L |
| 7 | Analytics: migration, classifier, backfill, endpoints, UI (section 6) | 0, 1, 6 helps (feedback by intent) | XL |

Phase 1 comes first because it is a security fix: today anyone who reaches port 3000 has full admin access. Phases 2 and 3 are quick and independent. Phases 5, 6 and 7 each need their own migration and can go as separate PRs. Motion (section 8) is applied inside every UI phase, not as a separate pass.

## 10. Risks

- **Entra registration dependency:** the real SSO login can't be tested end to end until the owner registers the app and fills in the `ENTRA_*` values. Mitigation: all development and automated tests run against the mock OIDC provider (`AUTH_DEV_MOCK`, see "Execution guide"), so nothing is blocked. A final manual check against real Entra happens once the owner has the credentials.
- **Lock-out:** if the first superadmin is not bootstrapped correctly, nobody can approve anyone. Mitigation: `SUPERADMIN_EMAILS`, plus a rule that the last active superadmin can't be demoted or suspended.
- **Security boundary:** the Next middleware redirect is not enough on its own. All authorisation must be enforced in the API and tested route by route (section 7.9).
- **Proxy and cookies:** the session cookie is set by Next and the backend sits behind the proxy. Check `Secure`, `SameSite` and the real client IP (`trust_proxy_headers`) in the deployed setup (`askdiq.conf`, `docker-compose.prod.yml`), not only on localhost.
- **Existing identities:** chat history and feedback created before SSO are keyed on a client-generated id and will not map to a user. They stay visible to admins but are not attributed to a person.
- **Motion cost:** a new dependency, and animation on data-heavy pages can hurt performance. Mitigation: `LazyMotion`, a 300 ms cap, reduced-motion support, and no per-row animation on large tables.

- **Classifier cost and quality:** LLM labeling spends tokens, and labels can be wrong. Mitigations: store `confidence` and `classified_by`, show an "unclassified" bucket, and sample-review before trusting the charts.
- **Small data:** the HTML shows 49 conversations. Our current local data is small too (45 queries in the last 24h in the screenshots), so the charts must have good empty and low-volume states.
- **Audit-log immutability:** a DB trigger blocks updates and deletes on `audit_log`. New log sources must be inserts only.
- **Feedback uniqueness:** one rating per message and actor. A corrected answer or reason update must be an upsert, not a second row.
- **Promoted-case file:** until promotions move to the DB, redeploys can lose the JSON file (F5).
- **Dirty working tree:** 78 modified files on the branch. Isolate this work in its own branch.

## 11. Verification checklist (per phase)

- Access: with no session, every dashboard page redirects to `/login` and every API route returns 401. A user with no approved role can use Chat, sees the Access Required page (with `ACCESS_CONTACT_EMAIL`) on every dashboard route, and gets 403 from every dashboard API route. Approval by a superadmin updates Postgres and unlocks the dashboard without re-login. A suspended user loses all access on the next request.
- Port: the app serves on 3000 and the Entra redirect URI matches exactly.
- Motion: with reduced-motion turned on, nothing animates. No animated page drops below 60 fps on a 500-row table.
- Interactivity: each row in the section 1 audit table has a working deep-link in both directions (click through and back with the browser back button).
- Backend: `ruff check .`, `ruff format .`, `pylint`, `pytest`, and Alembic upgrade and downgrade on a scratch DB.
- Frontend: `tsc --noEmit`, `vitest`, and light and dark screenshots of every touched page at laptop and wide widths.
- Governance: each defect G1-G13 has a before and after screenshot.
- Analytics: seed fixture data that mirrors the HTML's 49 conversations, and check each chart against the original HTML for the same dataset.
- Security (rev 3): every row of the section 12 OWASP matrix has a passing test. `bandit`, `pip-audit` and `npm audit --audit-level=high` are clean in CI. The security headers in 12.2 appear on every response.
- Performance (rev 3): the budgets in section 13.4 hold under the load profile in 13.5, with results attached to the release PR.
- White-label (rev 3): changing the brand in the Branding settings updates name, logos, palette and favicon without a rebuild, and the brand palette passes WCAG AA contrast in light and dark.
- CI and PR (rev 3): every feature merged through its own PR with all required checks green (section 16). Nothing was pushed directly to `main`.

---

## 12. Security: OWASP Top 10:2025 controls (rev 3)

### 12.1 Matrix

Each row is enforced in code and proven by a test. Existing controls are kept and not re-implemented.

| OWASP 2025 | Risk in this app today | Control | Phase |
|---|---|---|---|
| **A01 Broken Access Control** | Any visitor to port 3000 has full admin access through the proxy's shared key (7.1). IDOR on `/conversations/{id}`, `/feedback/{id}`, `/admin/users-accounts/{id}`. | Session-based authentication. A `require_permission()` guard on every route. A route-walk test fails the build if a route has no guard. Deny by default. Object-level checks on detail routes. CSRF protection: SameSite=Lax plus an `Origin` / `X-Requested-With` check on state-changing proxy calls. | B2 |
| **A02 Security Misconfiguration** | No CSP, HSTS, `X-Frame-Options`, `nosniff` or `Referrer-Policy` headers anywhere (app, Next or nginx). CORS allows all headers. Docs (`/docs`, `/openapi.json`) are public. | `SecurityHeadersMiddleware` (backend), `headers()` in `next.config.js`, and the same headers in `askdiq.conf`. A strict CSP with nonces for Next. Narrow CORS `allow_headers`. Docs disabled when `ENV=production`. A startup guard rejects `AUTH_DEV_MOCK` outside development. | B1 |
| **A03 Software Supply Chain Failures** | `pip-audit` is installed but never run. No `npm audit`. Caret ranges in `package.json`. | CI runs `pip-audit` and `npm audit --audit-level=high`. Exact pins for new dependencies. Dependabot (already active) is grouped weekly. Actions pinned to a major version, with `permissions: contents: read`. | B0 |
| **A04 Cryptographic Failures** | `api_jwt_secret` is a leftover. Session ids must not be guessable. | `secrets.token_urlsafe(32)` session ids, stored **hashed** (SHA-256) in `auth_sessions`. HMAC-signed cookie. `Secure` cookie and HSTS in production. TLS only at nginx. The unused JWT secret is removed. | B2 |
| **A05 Injection** (includes XSS and prompt injection) | Search, sort and filter params. LLM classifier input. Brand SVG upload. | Already in place: parameterized SQLAlchemy and sort allow-lists (`query_builders.py`). New: allow-lists for every new filter and sort, `LIKE` escaping, and Pydantic `Literal` / `Enum` for every enum param. Classifier output constrained by JSON schema and validated against the taxonomy, with the user text placed in a delimited data block (prompt injection). No `dangerouslySetInnerHTML`. Brand logos accept PNG or WebP, or SVG after sanitizing (strip `<script>`, `on*`, external `href`), and are served with `Content-Type` set and `nosniff`. CSV export neutralizes formula prefixes. | B1, B3, B4, B6 |
| **A06 Insecure Design** | Unbounded exports and backfills. Lock-out risk. | Hard caps (`limit <= 200`, `offset <= 10k`, export 10k rows, backfill `--limit`). Rate limits on auth endpoints. The last-superadmin rule. A threat model note in each feature PR. | All |
| **A07 Authentication Failures** | No authentication at all. | OIDC code flow with PKCE, plus `state`, `nonce`, `iss`, `aud`, `tid` and `exp` checks. Session rotation on login. Idle and absolute timeouts. Server-side revocation. Rate limit plus audit on `login_denied`. | B2 |
| **A08 Software or Data Integrity Failures** | Promoted cases in a mutable JSON file (F5). Untrusted label writes. | Promotions moved to the DB with an audit row. The immutable `audit_log` trigger is kept. Jobs are idempotent (unique key per `message_id` and job type). | B5, B6 |
| **A09 Security Logging and Alerting Failures** | Auth, 429 and 5xx events are not logged (4.3). | All of the events in 4.3 and 7.5 go to `audit_log`, PII masked through `governance/pii.py`. Alert rules feed the Governance status banner. `request_id` is correlated end to end. | B4 |
| **A10 Mishandling of Exceptional Conditions** | Unhandled exceptions could leak stack traces. Fire-and-forget `asyncio.create_task` can drop errors silently. | A global exception handler returns a problem+json body with the `request_id` and no internals. Background tasks run in a supervised `TaskGroup` or job runner that logs and records failures. External calls fail closed (an auth error means deny). Timeouts on every outbound call. | B1 |

**OWASP LLM Top 10 (RAG and classifier):**
- LLM01 prompt injection: existing flags, plus delimiting in the classifier.
- LLM02 sensitive disclosure: PII masking before every LLM call.
- LLM06 excessive agency: the classifier can only return labels.
- LLM10 unbounded consumption: classifier spend is capped per run and per day (`INSIGHTS_DAILY_TOKEN_BUDGET`), and the cost is recorded through `costing`.

### 12.2 Required response headers

`Content-Security-Policy` (nonce-based for Next; `frame-ancestors 'none'`), `Strict-Transport-Security: max-age=63072000; includeSubDomains` (production only), `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `Cross-Origin-Opener-Policy: same-origin`. Remove `X-Powered-By` (`poweredByHeader: false`).

### 12.3 Coding standards applied in every PR

- **Layering** (CLAUDE.md): routers validate input and call a service. Business logic lives in `services/`. A repository layer is added only where the CLAUDE.md criteria apply (users and RBAC, feedback, insights). Simple read aggregations stay as query builders.
- **Typing:** Pydantic v2 schemas for every request and response, using `UserRead` / `UserCreate` naming. `Enum` for statuses and roles, with no magic strings. Type hints on every function.
- **Config:** every new knob lives in `core/config.py` and `.env.example`. Secrets have no default.
- **Tests:** unit tests for the service and pure logic, API tests through `client`, and negative security tests for every new route (401, 403, 422, injection).
- **Frontend:** strict TypeScript with no `any` in new code. Server data fetched through one typed `api.ts` client. Accessible components (labels, focus, contrast AA). No inline hex colors, only tokens.

---

## 13. Async pipeline, concurrency and performance (rev 3)

### 13.1 Current state (validated)

**Good, and to be kept:**
- Fully async DB (asyncpg) and LLM clients.
- CPU work offloaded with `asyncio.to_thread`.
- Batched embeddings.
- `SingleFlight` plus a 15 s TTL cache.
- Semaphores on reranking and usage aggregation.
- Pool size 20 plus overflow 10.

**Gaps:**
1. A single uvicorn worker, because the rate limiter and caches are per process. No horizontal scale and no load balancing (nginx has one upstream).
2. Fire-and-forget `asyncio.create_task` with no supervision or durability.
3. Hard-coded `Semaphore(4)` in `admin_usage.py` and `online_eval.py`, instead of reading `usage_agg_max_concurrency`.
4. No response compression, `ETag` or `Cache-Control`.
5. `pool_pre_ping` is off, which risks stale connections behind nginx or the cloud LB.
6. `/query` does not stream.
7. Dashboard pages poll (Monitoring every 10 s) instead of pushing.
8. The frontend has no request cache, dedupe or prefetch. Every tab switch refetches.
9. Unused sync helpers (`embed_texts_sync`) invite misuse.

### 13.2 Backend techniques

| Technique | Where | How |
|---|---|---|
| **Async only** | Everywhere | Ruff `ASYNC` rules on. Remove the unused sync embedding helpers. A test fails if any route is `def` rather than `async def`. Every outbound call has a timeout. |
| **Parallel task execution** | Dashboard, governance overview, insights summary, feedback analytics | `asyncio.TaskGroup` with **one short-lived `AsyncSession` per task** (a session is not concurrency-safe), bounded by a configurable semaphore (`AGG_MAX_CONCURRENCY`). Where possible, one CTE query instead of N queries. |
| **Batching** | Classifier (N turns per LLM call), embeddings (already batched), bulk triage (`POST /feedback/bulk`), bulk access approval, audit inserts | Batch size and parallelism come from config. Use `insert().values([...])` for bulk writes. |
| **Background job queue** | Classifier, backfill, rollup refresh, online eval | A `jobs` table (`type`, `payload`, `status`, `attempts`, `run_after`, `locked_by`). Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`. Retries use exponential backoff with jitter, and a dead-letter status. The worker runs in-process under a lifespan `TaskGroup`, or as a separate `worker` service in compose. It is safe with multiple replicas. |
| **Caching** | Branding, `/auth/me` permissions, aggregations | Per-process TTL caches with key versioning. Invalidation is broadcast to every worker and replica through Postgres `NOTIFY cache_invalidate` (test-IP, branding, role change). `SingleFlight` against stampedes. Data that must be shared (rate limits, OIDC state) lives in Postgres (13.6). |
| **Pre-aggregation** | Analytics and dashboard trends | Hourly and daily rollup tables (`insight_daily_rollup`, `usage_hourly_rollup`) refreshed incrementally by a job. Charts read the rollups, and only today's partial bucket is computed live. |
| **Pagination** | Logs, traces, conversations | Keyset (cursor) pagination on `(created_at, id)` for the large tables. Offset pagination is kept for small tables. Exact `total` is computed only where it is cheap, otherwise an estimate with a flag. |
| **Indexes** | New filter columns | A composite index for every new filter and sort pair (the migration includes `EXPLAIN` evidence in the PR). Indexes are created `CONCURRENTLY` where the table is large. |
| **Streaming** | CSV and JSONL exports, `/query` (optional) | `StreamingResponse` over `stream()` / `yield_per(1000)`, so memory stays flat. SSE for live governance and monitoring updates, replacing 10 s polling. |
| **HTTP efficiency** | All JSON | `GZipMiddleware(minimum_size=1024)` (or gzip at nginx). Weak `ETag` plus `Cache-Control: private, max-age=15` on aggregation GETs, with 304 on `If-None-Match`. `Cache-Control: public, max-age=3600` on `/branding` and brand assets. |
| **Connection pool** | `database.py` | `pool_pre_ping=True`, pool size from config, and a statement timeout per session (`SET LOCAL statement_timeout`) on dashboard reads. |
| **Load balancing** | `askdiq.conf`, `docker-compose.prod.yml` | `upstream askdiq_api { least_conn; server api1:8000 max_fails=3 fail_timeout=10s; server api2:8000 ...; keepalive 32; }`, plus health checks on `/health`. Uvicorn runs `--workers ${WEB_CONCURRENCY}` (default 2 per replica) once the Postgres-backed shared state (13.6) is in. Sessions live in Postgres, so **no sticky sessions** are needed. The DB pool is sized so that `replicas × workers × (pool_size + max_overflow)` stays below Postgres `max_connections` minus 20. Graceful shutdown drains in-flight requests and jobs. |
| **Back-pressure** | LLM path | The existing Azure quota monitor plus a global LLM semaphore (`LLM_MAX_CONCURRENCY`). Return 429 with `Retry-After` when saturated. A circuit breaker on repeated Azure failures. |

### 13.3 Frontend techniques

| Technique | How (no new dependency) |
|---|---|
| **Request cache and dedupe** | A small `useApiQuery(key, fetcher, {staleTime})` hook in `lib/query-cache.ts`. It holds an in-memory map of promises (deduping identical in-flight requests), uses stale-while-revalidate, uses `AbortController` on unmount or param change, and revalidates on window focus. |
| **Prefetching** | `next/link` prefetch for nav. On hover or focus of a KPI card, tab or table row (after a 120 ms intent delay), call `prefetch(key)` to warm the target page's first query. Prefetch the next pagination page when idle (`requestIdleCallback`). |
| **Parallel fetch** | `Promise.all` for independent widgets. Each widget streams in with its own skeleton (Suspense-style), so one slow chart never blocks the page. |
| **Code splitting** | `next/dynamic` for heavy charts (Sankey, Treemap) and drawers. `LazyMotion` with `domAnimation`. `optimizePackageImports: ['lucide-react','recharts']`. |
| **Rendering** | Memoized chart data (`useMemo`). Debounced search (300 ms). No per-row animation above 50 rows. `content-visibility: auto` for below-the-fold sections. |
| **Live data** | An SSE `EventSource` for governance and monitoring, with backoff reconnect. Pause it when the tab is hidden (`visibilitychange`). |

### 13.4 Performance budgets (to be verified in the final phase)

| Metric | Budget |
|---|---|
| Cached admin aggregation GET | p95 < 150 ms |
| Uncached admin aggregation GET | p95 < 600 ms |
| `/auth/me` | p95 < 50 ms (cached permissions) |
| Paginated list (logs, traces, 50 rows) | p95 < 250 ms at 1M audit rows |
| `/query` time-to-first-byte (excluding the LLM) | within `latency_budget_ms` (200 ms) |
| Error rate under target load | < 0.5 % (excluding deliberate 429s) |
| Frontend LCP on dashboard (production build, laptop) | < 2.5 s. CLS < 0.1. INP < 200 ms |
| JS added per route by this work | ≤ 60 KB gzipped (Motion ~ 15 KB via `LazyMotion`) |

### 13.5 Load and concurrency test profile

**Requirement: at least 50 concurrent requests, all succeeding within budget.** Every profile is run at 50 (requirement) and at 100 (headroom).

- **Tooling:** extend `scripts/run_admin_load_test.py` and `run_concurrency_test.py` (httpx with asyncio).
- **Admin profile:** 50, then 100, concurrent admin users for 10 minutes, mixing dashboard, analytics, governance logs (deep pages) and feedback triage.
- **Chat profile:** 50, then 100, concurrent `/query` requests, plus 200 open chat sessions, with the LLM mocked by a latency-injected fake (2 to 6 s). This proves the event loop never blocks while waiting on the LLM. The per-user rate limit is raised for the test users only, through test settings.
- **CI gate:** `tests/test_concurrency_50.py` fires 60 simultaneous requests at the ASGI app (fake LLM, real Postgres). All must succeed, with p95 inside budget. It runs on every PR.
- **Concurrency correctness:**
  - Two superadmins approving the same request, where exactly one wins.
  - Concurrent triage on one item, with no lost update (optimistic `version` column or `SELECT ... FOR UPDATE`).
  - Job queue with 4 workers, where no job runs twice.
  - Rate limiter exact across 2 workers and 2 replicas (Postgres buckets).
  - Concurrent same-key cache misses, where SingleFlight issues one query.

### 13.6 Postgres-backed shared state (replaces Redis)

| Need | Mechanism | Notes |
|---|---|---|
| Rate limiting across workers and replicas | UNLOGGED table `rate_limit_buckets(key, window_start, count)`, primary key `(key, window_start)`. A fixed 10 s sub-window approximates a sliding window. One atomic `INSERT ... ON CONFLICT DO UPDATE SET count = count + 1 RETURNING count` per check. A periodic job deletes old windows. | About 1 ms per check on a local DB. UNLOGGED means no WAL cost, and losing it on a crash only resets counters. |
| OIDC `state`, `nonce` and PKCE verifier | Table `oidc_states(state_hash, nonce, verifier, expires_at)`, consumed with `DELETE ... RETURNING` (single use, atomic). | Works on any replica that receives the callback. |
| Live updates (SSE) and cache invalidation | `LISTEN askdiq_events` on one dedicated asyncpg connection per worker. Writers call `NOTIFY` (payload is at most 8 KB, so we send ids rather than data). Each worker fans out to its SSE clients in memory. | No polling per client. |
| Job queue | `jobs` table with `FOR UPDATE SKIP LOCKED` (13.2). | Already planned. |
| Single-flight and read caches | Per process. | Duplicates across workers are bounded by the worker count, which is acceptable. |
| Async-only audit | Ruff `ASYNC` rules plus `tests/test_async_only.py`. Known items to fix: sync `open()` of `promoted_test_cases.json` in `evaluation/dataset_extra.py` (moved to the DB in B5), sync `read_text` in `metrics.py` (wrap in `asyncio.to_thread`), `psutil.Process().memory_info()` in `admin.py` (cheap, but moved into the same `to_thread` call), unused `requests` and `embed_*_sync` helpers removed. | CPU-heavy work (reranker, docx, chunking) already uses `to_thread` and stays that way. |
- **Soak:** 30 minutes, with RSS and the DB pool watched for leaks.

---

## 14. White-labelling (rev 3)

**Today:**
- Three config fields (`brand_name`, `brand_assistant_name`, `brand_support_email`) and a public `GET /branding`. The frontend has `api.getBranding()` but never calls it.
- Hard-coded in the frontend: "DeployIQ" in `app/layout.tsx` metadata and in the chat sample questions, the `/exigotech-logo.svg` and `/exigotech-mark.svg` paths in `Sidebar.tsx`, and the teal palette in `tailwind.config.ts`.

**Target:**
- **`brand_settings` table** (a singleton row, with config values as the fallback): `product_name`, `assistant_name`, `company_name`, `support_email`, `access_contact_email` (supersedes the env default), `primary_hex`, `accent_hex`, `logo_light_asset_id`, `logo_dark_asset_id`, `mark_light_asset_id`, `mark_dark_asset_id`, `favicon_asset_id`, `login_eyebrow`, `login_headline`, `login_subtext`, `login_description`, `privacy_url`, `terms_url`, `help_url` (validated `https://` or relative URLs only), `chat_sample_questions` (JSON), `updated_by`, `updated_at`, `version`.
- **`brand_assets` table:** content-addressed bytes (sha256), MIME type and dimensions. Upload is validated for MIME sniffing, a 512 KB cap, PNG, WebP or sanitized SVG only, and maximum dimensions. Assets are served at `GET /branding/assets/{sha}` with immutable caching.
- **API:**
  - `GET /branding` (public, cached, ETag) returns resolved values plus a derived 50 to 950 palette.
  - `PUT /admin/branding` and `POST /admin/branding/assets` are superadmin only and audited.
  - Server-side palette derivation (OKLCH steps from `primary_hex`), with a **WCAG AA contrast check** that rejects unreadable choices with a 422 explaining why.
- **Frontend:**
  - A `BrandProvider` fetches `/branding` at the server layout (`generateMetadata` for title and favicon) and writes CSS variables (`--brand-50` to `--brand-950`, `--brand-gradient`).
  - `tailwind.config.ts` switches the brand colors to `rgb(var(--brand-600) / <alpha-value>)`. Every existing `brand-*` class keeps working.
  - One `<BrandLogo variant="full|mark" />` component handles the light and dark swap. The sidebar, login, access pages and favicon all use it.
  - The Exigo Tech teal (`#008080`, `#00584F`, `#EAF6F5`) is the seeded default.
- **Admin UI:** a new superadmin-only **Settings** page with tabs **Branding** and **Insights Taxonomy**. Branding has a live preview of the light and dark sidebar, login and chat bubble, and a contrast badge. The taxonomy editor was promised in 6.3 as "configurable" but had no UI.

---

## 15. UI/UX gap audit and dashboard-wide chart catalogue (rev 3)

Reviewed as a senior product designer against the logo theme, sections 1 to 8, and the current code.

### 15.1 Missing global interactions (not in revision 2)

| Gap | Why it matters | Plan |
|---|---|---|
| No **global date-range control** | Every page invents its own window, and G7 (counts disagree) is partly a window problem | A `DateRangePicker` in `DashboardHeader` (24h, 7d, 30d, 90d, custom), synced to the URL (`?from=&to=`) and shared like `AudienceProvider`. Every KPI shows its window ("last 7 days"). |
| No **toast and undo system** | Section 5 needs undo toasts, and none exists | `ToastProvider` with Motion enter and exit, `role="status"`, an undo action, and auto-dismiss that pauses on hover. |
| No **confirm dialog** | Delete document, suspend user and revoke sessions need explicit confirmation | `ConfirmDialog` with a typed confirmation for destructive superadmin actions. |
| No **command palette** | Many pages and entities. Power users triage by keyboard. | `Ctrl/Cmd+K`: jump to a page, or search a conversation id, request id, user email or document. Uses the existing search endpoints. |
| No **keyboard-shortcut help** | Section 5 adds j, k, p, d and r | Press `?` to open a shortcuts sheet. |
| No **notification bell** | Access requests and incidents need attention | A bell in the header: pending access requests (superadmin), CRITICAL incidents, and negative-feedback spikes. Fed by SSE. |
| No **profile menu** | Sign out, theme and role are scattered | An avatar menu: name, email, role badge, theme toggle (light, dark, system), sign out. |
| No **branded 404, 403 and error pages** | Raw Next errors break the brand | `not-found.tsx` and `error.tsx` with `BrandLogo`, a short message and a way back. |
| No **empty, loading and error state standard** | Small data (risk 10.10) | `EmptyState`, `ErrorState` (with retry and `request_id`) and skeletons for every card, chart and table. |
| No **copy-to-clipboard on ids** | Request and trace ids are pasted into tickets | A copy icon button with a "Copied" tooltip on every id cell (inside `EntityLink`). |
| No **table column visibility or density** | Wide tables on laptops | Column toggle and compact/comfortable density in `TableToolbar`, remembered in `localStorage`. |
| No **export everywhere** | Only usage has export | A CSV export action on every `TableCard` that has a server export. |
| **Mobile and narrow widths** | The sidebar does not collapse below `md` | An off-canvas drawer sidebar under `md`. Charts reflow to one column. |
| **Taxonomy and branding have no UI** | Promised as configurable | Settings page (section 14). |
| **Golden Case "Run now"** has no progress UI | Long-running action | A job progress chip that polls the job id, with a toast on finish. |

### 15.2 Visual system: look, hover and icons

- **Surfaces:**
  - Cards: `rounded-2xl`, a 1 px token border, `shadow-sm`.
  - On hover: lift `translateY(-2px)`, `shadow-md`, border tinted to `brand-300` in light or `brand-700/60` in dark, over 150 ms. Clickable cards also show a trailing `ArrowUpRight` icon that slides 2 px.
  - Focus-visible: a 2 px `brand-500` ring with an offset, never removed.
- **Brand gradient:** used only for the primary action, the active tab pill and the hero KPI accent bar. It is not used on large surfaces, so data stays readable.
- **Charts:**
  - A single `chartTheme` comes from the tokens: categorical palette (teal, dark teal, amber, slate, red, indigo), sequential teal ramp, and a diverging teal to red scale.
  - Gridlines at 8 % opacity. Tooltips are cards with the same border and shadow. Hover dims the other series to 35 %. Every chart has an accessible table fallback (a "View as table" toggle).
- **Icons:** one Lucide icon per nav item, page header and tab, 16 px in tabs and 18 px in nav, `stroke-width 1.75`. Proposed set:

| Area | Icon |
|---|---|
| Dashboard | `LayoutDashboard` |
| Chat | `MessageSquare` |
| Documents | `FileText` |
| Visitors | `Users` |
| Usage & Cost | `Coins` |
| Conversations | `MessagesSquare` |
| Feedback | `ThumbsUp` |
| Evaluation (Live) | `Sparkles` |
| Analytics | `ChartColumnBig` |
| Governance | `ShieldCheck` |
| User Management | `UserCog` |
| Settings | `Settings2` |
| Notifications | `Bell` |
| Command | `Command` |

- **Typography:** keep the `text-sm font-bold` titles from planchanges3. Numbers use `tabular-nums`, so count-up animations don't jitter.
- **Logo:** `BrandLogo` (section 14) plus the 1.7 to 1.10 fixes. The dark variant is generated from the artwork with a white wordmark and lifted teal (`brand-400`), with contrast checked against `dark-bg`.

### 15.2a Light and dark theme parity audit (rev 3.2)

**Reported bug:** after you switch between light and dark, parts of the dashboard don't follow the new theme. Causes found in the code:

| # | Cause | Evidence | Fix |
|---|---|---|---|
| T1 | Charts use fixed hex colours | 50 `stroke=`/`fill=`/`color:` hex values in chart props, across 21 `.tsx` files with hex colours | Charts read colours from `chartTheme` through a `useChartTheme()` hook that re-renders when the theme changes (it observes the `dark` class) |
| T2 | Surfaces without a dark variant | `bg-white` with no `dark:` class in `admin/conversations/[id]/page.tsx`, `governance/monitoring/MonitoringView.tsx`, `app/page.tsx` and `components/admin/insights/OutcomePillBar.tsx`, plus `text-gray-*` in `conversations/[id]` | Replace with surface and text tokens (`bg-surface`, `text-fg` and so on, defined in light and dark) |
| T3 | Wrong theme flashes on load | `ThemeProvider` applies the `dark` class in a `useEffect`, after the first paint | Add an inline pre-paint script in `app/layout.tsx` `<head>` (with a CSP nonce) that sets the class from storage or the OS setting before paint, plus `suppressHydrationWarning` |
| T4 | Tabs drift apart | No `storage` event listener | Sync the theme across tabs, and add a **System** mode that follows the OS setting live (`matchMedia` change event) |
| T5 | The logo doesn't adapt | Single logo asset, `dark:brightness-110` only | `BrandLogo` light and dark variants (F0.3.4) |
| T6 | Status colours lack contrast in dark mode | Amber `#B7791F` and red `#C0362C` on dark surfaces | Separate dark tokens (lifted amber and red), with AA contrast checked |

**Acceptance (every page and component, in both themes):**
- Text and icon contrast meets WCAG AA (4.5:1 for body text, 3:1 for large text and icons).
- Brand colours match the logo: teal `#008080`, dark teal `#00584F`, tint `#EAF6F5` in light mode, and the lifted teal ramp in dark mode.
- Every chart series, axis, grid, tooltip and legend changes **instantly** on toggle, with no reload.
- Light to dark to light leaves no stuck styles.
- No flash of the wrong theme on load.
- A lint guard blocks new hex colours and new `bg-white`/`text-gray-*` classes without a `dark:` variant outside `lib/theme-tokens.ts`.

### 15.3 Motion

"Claude motion" in your request is read as the **Motion** library already chosen in section 8. Additions:
- `layoutId` for the KPI card to drawer expansion (a shared-element transition).
- `AnimatePresence` for toasts, the command palette and the notification list.
- A spring-number count-up in `StatCard`.
- A chart crossfade on filter change.
- A sidebar width spring.
- A subtle 1.02 scale on hover for icon badges.

All of these use the `lib/motion.ts` presets, run under 300 ms, and respect reduced motion.

### 15.4 Chart catalogue (distinct per page, nothing duplicated without purpose)

Shared chart components live in `components/charts/`: `ChartCard`, `Sparkline`, `KpiTrendCard`, `StackedArea`, `PercentileBand`, `Waterfall`, `CalendarHeatmap`, `RadialGauge`, `BulletChart`, `Funnel`, `QuadrantScatter`, `Histogram`, `Radar`, plus the existing Recharts `Treemap`, `Sankey`, `Pie` and `Bar`.

| Page | New charts (each answers one question) | Interaction |
|---|---|---|
| **Dashboard** | KPI cards with 7-day **sparklines**. **Stacked area** of queries by outcome (replaces the plain area). **Percentile band** p50/p95 latency. **Bullet chart** for cost month-to-date vs budget and forecast. **Radial gauge** for satisfaction (thumbs-up %). **Mini treemap** of the top intents (links to Analytics). A **live activity feed**. | Every element deep-links pre-filtered. Range control. |
| **Visitors** | **Stacked bar** of new vs returning per day. **Calendar heatmap** (weekday x hour). | Cell click filters the table. |
| **Visitor detail** | An **activity timeline** (sessions as bars on a time axis) and a per-visitor cost **sparkline**. | Bar to conversation. |
| **Conversations** | A **histogram** of turns per conversation and an **outcome 100 % bar** by day. | Bin or segment click filters. |
| **Conversation detail** | A per-turn **stage waterfall** and token **stacked bar** per turn. | Turn click opens the inspector. |
| **Documents** | **Horizontal bar** of the top cited documents. A **scatter** of document age vs citations (stale content). A **coverage gauge** for the % of questions with a cited source. | Bar to document detail. |
| **Document detail** | A chunk **hit heatmap** (chunk index x week) and the low-rated citation list. | Cell to chunk. |
| **Usage & Cost** | **Cost by stage stacked area**, **month-end forecast line** (linear from MTD), and **cache-hit ratio gauge** (cached tokens). | Range control. |
| **Governance** | A **status timeline** (incident markers). **Percentile band** per stage. **Radial gauges** for CPU, RAM and Azure TPM/RPM quota. A **guardrail flag bar**. A trace **waterfall** in the drawer. | Marker to Logs filtered. |
| **Feedback** | **Satisfaction trend**, **rating distribution**, **reason tags bar**, **triage funnel** (new, reviewed, promoted), and a **disagreement quadrant** (human rating vs groundedness). | Every mark filters the queue. |
| **Analytics** | As in section 6 (trend, hour-of-day, Sankey, treemap, donut, lollipop, bubble). | Section 6. |
| **Evaluation (Live)** | A quality-dimension **radar** (current vs previous run) and a pass-rate **trend with run markers**. | Marker to run. |
| **User Management** | **Logins per day** bar, **access-request funnel**, **role distribution donut**, and time-to-approve **histogram**. | Segment filters Users. |

**Backend support** for these charts is a set of small aggregation endpoints (sections 4.5, 5.4 and 6.3, plus `GET /admin/dashboard?from=&to=` with sparklines, `GET /admin/visitors/stats`, `GET /admin/conversations/stats`, `GET /documents/stats/citations` and `GET /admin/users-accounts/stats`). They are built in the backend phases, before any of these charts.

---

## 16. CI and pull-request workflow (rev 3)

### 16.1 CI gaps found in `.github/workflows/ci.yml`

1. Runs on `push` to **every** branch **and** on `pull_request`, so every PR runs twice. There is no `concurrency` group to cancel stale runs.
2. The backend runs `ruff check` but not **`ruff format --check`** or **`pylint`**. **`pip-audit`** is never run.
3. The frontend runs lint, tsc and build, but **never runs `npm test` (vitest)**. No `npm audit`. No Playwright.
4. No `alembic heads` single-head check and no **downgrade round-trip**.
5. No coverage report or threshold.
6. The env has `API_JWT_SECRET`. The SSO work needs `SESSION_SECRET`, `AUTH_DEV_MOCK=true` and `ENV=development` (test values only).

### 16.2 Target CI (one workflow, required checks)

| Job | Steps |
|---|---|
| `backend-lint` | `ruff check .`, `ruff format --check .`, `pylint app`, `bandit -r app -ll`, `pip-audit -r requirements.txt` |
| `backend-tests` | Postgres pgvector service. `alembic upgrade head`, then `alembic heads` must print exactly 1, then `alembic downgrade -1 && alembic upgrade head`. `pytest -q --cov=app --cov-fail-under=80` |
| `frontend-checks` | `npm ci`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npm audit --audit-level=high --omit=dev` |
| `fullstack-smoke` | Needs the two above. Starts Postgres, the backend (`AUTH_DEV_MOCK=true`, `ENV=development`) and `next start -p 3000`. Then runs `backend\scripts\smoke_e2e.py`: an **httpx client** that signs in through port 3000 (Next callback, then the mock IdP, then the cookie), and calls dashboard APIs through the Next proxy as each role. No browser and no Playwright. |
| `perf-smoke` (PR label `perf` or nightly) | `run_admin_load_test.py --smoke --concurrency 50` asserts the 13.4 budgets at the required concurrency. |

**End-to-end testing without a browser:** backend journey tests (`backend/tests/e2e/`, real Postgres, mock IdP, full router stack), frontend journey tests (`frontend/tests/journeys/`, vitest and Testing Library with a mocked `fetch`), and the full-stack smoke above. Visual checks (light and dark screenshots) stay **manual** and are attached to the PR.

Triggers: `pull_request` to `main`, `push` to `main` only, and `workflow_dispatch`. `concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }`. Keep `permissions: contents: read`.

### 16.3 Branch protection on `main` (repo admin, open item 8)

- Require a pull request with 1 approval, and dismiss stale approvals.
- Required status checks: `backend-lint`, `backend-tests`, `frontend-checks` and `fullstack-smoke`. Branches must be up to date before merging.
- **Before turning protection on,** push the two plan files and `.gitignore` change in one PR (`chore/plan-admin-console-rev3`), or push them first if you prefer a direct push. Once protection is on, every change, plan files included, goes through a PR. `newplan.md` is still git-ignored (`/newplan.md` in `.gitignore`). Remove that line if you want it committed.
- Require conversation resolution. Block force-push and deletion. Linear history is optional: the team's earlier PRs used merge commits.
- CodeQL default setup stays on (it already raised alerts fixed in `50f777a`).

### 16.4 Per-feature PR routine (every feature in `planchanges4.md` ends with this task)

1. Branch from a fresh `main`: `git switch main && git pull && git switch -c feat/<scope>-<short-name>`. Use the `fix/` or `chore/` prefix as appropriate.
2. Commit using Conventional Commits (`feat(auth): ...`), ending with the attribution trailer.
3. Before pushing, run locally: the backend lint and tests through `backend\venv\Scripts\...`, and the frontend lint, typecheck, test and build. Do not push red.
4. `git push -u origin <branch>`, then `gh pr create --base main --fill-first`, using `.github/pull_request_template.md`.
5. Fill in the PR template:
   - Summary and linked plan task ids.
   - Screenshots, light and dark, for UI work.
   - Migration notes, with downgrade tested.
   - Security notes (which OWASP rows are touched).
   - Performance notes (budgets measured).
   - Assumptions (from the execution guide).
   - Test evidence.
   - A rollback plan.
6. Wait for green CI. Fix failures by pushing new commits, never by suppressing a check. Request review and resolve comments.
7. Merge through GitHub only, after approval and green checks. Delete the branch, then `git switch main && git pull` before the next feature.
8. Do **not** stack PRs by default. Work in the **merge waves** in `planchanges4.md` section 8: every PR in a wave branches from `main` after the previous wave has merged. Stack only if a wave would otherwise stall, and then follow `pr_raising_plan.md`.
9. Merge with **squash** (one commit per feature on `main`, titled with the PR title). Before merging, rebase on `main`, rerun CI, and apply the migration re-link rule (`planchanges4.md` 8.3).

### 16.5 Execution order (backend first)

| Phase | Scope |
|---|---|
| **B0** | Repo, CI and tooling hardening (section 16). Lint scope (open item 10). |
| **B1** | Platform: security headers, exception handling, async job queue, `StateBackend`, compression and ETag, pool settings, configurable semaphores, load-balancing config (sections 12 and 13). |
| **B2** | SSO, RBAC, sessions and the User Management API (section 7). |
| **B3** | White-label branding API and assets (section 14). |
| **B4** | Governance backend: G1 to G13, new endpoints, log sources, SSE (section 4). |
| **B5** | Feedback and RLHF backend (section 5). |
| **B6** | Insights and Analytics backend: taxonomy, classifier, backfill, rollups and endpoints (section 6). |
| **B7** | Cross-page aggregation endpoints for the chart catalogue and deep-links (sections 1 and 15). |
| **F0** | Frontend foundations: port 3000, Motion, tokens, `BrandProvider`, `BrandLogo`, chart kit, `EntityLink`, `useDeepLink`, query cache and prefetch, toasts, dialogs, command palette, state components. |
| **F1** | Auth UI, route gate, sidebar role filtering, User Management page. |
| **F2** | Navigation cleanup: Evaluation redirect, Usage tab swap, Visitors rename. |
| **F3** | Dashboard and list or detail pages: interactivity and charts (sections 1 and 15). |
| **F4** | Governance UI. |
| **F5** | Feedback UI and chat reason chips. |
| **F6** | Analytics UI. |
| **F7** | Settings UI (Branding and Taxonomy). |
| **V** | End-to-end, performance and concurrency, OWASP verification, and release. |

---

## 17. Revision 3.1 change log (2026-10-15)

- **Redis removed.** Shared state moved to Postgres (13.6). Multi-worker and multi-replica load balancing are kept.
- **Hard target of at least 50 concurrent requests.** It is tested at 50 and 100, with a CI gate (`test_concurrency_50.py`). An async-only audit lists the known sync hotspots.
- **Playwright, axe, Lighthouse and ZAP removed.** E2E now runs on httpx (backend journeys plus a full-stack smoke through port 3000) and vitest journeys. The CI check is `fullstack-smoke` instead of `e2e`.
- **SSO.** The mock IdP now behaves like real Entra (claims, issuer, JWKS rotation, no `email` claim). Added the `verify_sso_config.py` preflight and the go-live checklist, so filling in `.env` is the only step left.
- **Analytics.** The HTML renderer was read, and section 6.4 maps every field, KPI, chart and filter to our tables, with a 49-conversation parity fixture. The `GRE` and `ACK` codes are kept.
- **Merges.** Merge waves, squash merges and the migration re-link rule are in `planchanges4.md` sections 8 and 9, along with the parallel execution guide.

## 18. Revision 3 change log

- The baseline commit is marked done (PR #13). Branching moves to one feature, one PR, and no direct push to `main`.
- The reference HTML folder moves out of the git-ignored `/backend/docs/`.
- The venv is corrected to `backend\venv`. The missing `venv` ruff exclude and the missing pylint config are flagged.
- Added sections 12 (OWASP 2025), 13 (async, concurrency, performance, load balancing), 14 (white-label), 15 (UI/UX gaps, chart catalogue, icons, hover, Motion), 16 (CI and PR workflow) and open items 7 to 10.
- Order changed to backend first (16.5). The section 9 table is kept for history.
- Found while validating:
  - `QueryInspectorDrawer` is duplicated (`components/admin/` and `components/admin/usage/`). Consolidate in F0.
  - `ChartCard` is duplicated in 3 files, plus `AuroraChartCard`.
  - `frontend/middleware.ts` does not exist yet.
  - Dev runs with `--turbo` and no port pin.
  - `/branding` is defined inline in `main.py`. Move it to a router in B3.
