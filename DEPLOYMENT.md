# DeployIQ — Deployment Guide (Docker)

Running the full stack (Postgres/pgvector, FastAPI backend, Next.js
frontend) with Docker Compose.

## Prerequisites

- Docker and Docker Compose
- `backend/.env` and `frontend/.env.local` present (copy from their
  `.example` files and fill in values) — neither is committed, so both
  must exist on every machine that runs this stack

## 1. Configure secrets

`docker-compose.yml` loads both files via `env_file` — fill in before
`docker compose up`:

| File | Required variables |
|---|---|
| `backend/.env` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, `AZURE_DEPLOYMENT_NAME`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `API_KEY_NAME`, `SESSION_SECRET`, `ENTRA_*` (or `AUTH_DEV_MOCK=true` with `ENV=development`) |
| `frontend/.env.local` | `API_KEY` (must match `backend/.env`'s `API_KEY_NAME`) |

`DATABASE_URL` / `API_BASE_URL` in these files are only used for
non-Docker local dev — Compose overrides both to point at the `postgres`
and `backend` services respectively.

## 2. Build and start the stack

```bash
docker compose up --build
```

Services defined in [docker-compose.yml](docker-compose.yml):

- `postgres` — `pgvector/pgvector:pg18`, exposed on host port `5433`, data
  persisted in the `pgdata` volume, healthchecked via `pg_isready`.
- `backend` — built from [backend/Dockerfile](backend/Dockerfile), loads
  `backend/.env` via `env_file`, with `DATABASE_URL` overridden to target
  the `postgres` service over the compose network. Exposed on `8000`.
- `frontend` — built from [frontend/Dockerfile](frontend/Dockerfile), loads
  `frontend/.env.local` via `env_file`, with `API_BASE_URL` overridden to
  `http://backend:8000`. Exposed on `3000`.

## 3. One-off setup (migrations + backfill + ingestion)

Not baked into the backend image's `CMD`, so container restarts never
silently re-run them. Run once the `backend` container is healthy:

```bash
# 1. Apply database migrations (including d5e6f7a8b9c0 for usage outcome tracking)
docker compose exec backend alembic upgrade head

# 2. Run idempotent usage accounting backfill for legacy messages
docker compose exec backend python scripts/backfill_usage_accounting.py --dry-run
docker compose exec backend python scripts/backfill_usage_accounting.py --apply

# 3. Canonical Markdown ingestion (strictly accepts .md files; non-.md files are rejected)
docker compose exec backend python scripts/ingest_real_docs.py
```

Re-run `ingest_real_docs.py` any time `backend/data/real_docs/*.md` changes
— it's safe to rerun (replaces existing chunks per source document).

## 4. Operational Environment Variables & Proxy Configuration

Configure the following variables in `backend/.env` for production deployments:

| Variable | Default | Purpose |
|---|---|---|
| `TRUST_PROXY_HEADERS` | `true` in prod / `false` in dev | Mandatory when running behind Nginx, Traefik, or Azure App Gateway so `X-Forwarded-For` is correctly resolved for Audience Classification. |
| `TEST_IPS` | `127.0.0.1,::1,localhost` | Initial comma-separated list of test IPs / CIDR blocks (e.g. `10.0.0.0/8,192.168.1.0/24`). Can be dynamically overridden via Admin Console. |
| `AUDIENCE_CACHE_TTL_SECONDS` | `15` | In-memory cache TTL for audience classification counts. |
| `USAGE_SUMMARY_CACHE_TTL_SECONDS` | `30` | In-memory cache TTL for `/admin/usage/summary` metrics. |
| `USAGE_MAX_RANGE_DAYS` | `90` | Maximum date range allowed for usage summaries. |
| `PRICING_PRICE_EMBED_PER_MILLION` | `0.02` | Rate card for query and guardrail embedding tokens ($/1M). |
| `PRICING_PRICE_INPUT_PER_MILLION` | `0.25` | Rate card for prompt input tokens ($/1M). |
| `PRICING_PRICE_OUTPUT_PER_MILLION` | `2.00` | Rate card for visible and reasoning completion tokens ($/1M). |
| `PRICING_PRICE_CACHED_PER_MILLION` | `0.0625` | Rate card for cached prompt tokens ($/1M). |

### Proxy contract (backend side, SSO)

Since SSO, the backend only answers the Next.js proxy, and every user
request needs a signed-in session. The proxy must send:

| Header | When | Backend behaviour |
|---|---|---|
| `X-API-Key` | every request | Service key (`API_KEY_NAME`, equal to the frontend's `API_KEY`). Missing or wrong: **401**, even with a valid session. |
| `X-Session` | every user request | The raw session id from the httpOnly session cookie. Missing, unknown, expired or revoked: **401**. |
| `X-Requested-With: askdiq` and `Origin` | `POST`, `PUT`, `PATCH`, `DELETE` | CSRF defence: the marker must be present and `Origin` must equal `APP_BASE_URL`'s origin, otherwise **403**. |
| `X-Forwarded-For` | every request | Client IP for audit and rate limits; honoured only when `TRUST_PROXY_HEADERS=true`. |

Status and role are re-read from Postgres on every request: pending or
rejected users get Chat only (403 on dashboard routes), suspended users get
403 everywhere (except `GET /auth/me` and `POST /auth/logout`, so the UI can
show the suspended page), and role changes apply on the next request. Public
routes: `/health`, `/branding`, `/auth/sso/login` and `/auth/sso/callback`
(no session yet, but the service key is still required) and, with
`AUTH_DEV_MOCK=true` in development only, the mock IdP under `/mock-idp/`.

### Sign-in flow (frontend contract for the Next.js side)

The Next.js handlers are thin; the backend is the authority. Exact contract:

| Step | Next.js | Backend call (through the proxy, with `X-API-Key`) |
|---|---|---|
| 1 | "Sign in with Microsoft" calls `GET /api/proxy/auth/sso/login`. | Returns `{"authorize_url": ...}` (`Cache-Control: no-store`). Rate-limited per client IP (`AUTH_LOGIN_RATE_LIMIT_PER_MINUTE`, default 10/min, **429** with `Retry-After`). |
| 2 | Before redirecting the browser to `authorize_url`, set a short-lived (10 min) httpOnly, `SameSite=Lax` cookie `askdiq_oidc_state` holding the `state` query parameter of that URL. | `state`, `nonce` and the PKCE verifier are stored server-side, single use. |
| 3 | `GET /auth/callback?code=&state=` (route handler, server-side): reject unless `state` equals the `askdiq_oidc_state` cookie (login-CSRF defence), then delete that cookie. | |
| 4 | Server-side `POST {backend}/auth/sso/callback` with JSON `{"code", "state"}`, plus `X-Session: <old cookie value>` if a session cookie already exists (it is revoked: rotation on login). | **200** `{"session", "cookie_name", "cookie_secure", "expires_at", "me"}`. **400**: state unknown, expired or replayed (start again). **401**: invalid ID token. **403**: suspended (`detail: "Account suspended."`, body also has `access_contact_email`) or the email belongs to another Entra identity. **429**: rate limited. **502**: identity provider unreachable. Every outcome is audited (`login_success` / `login_denied`). |
| 5 | Set the cookie named `cookie_name` (`SESSION_COOKIE_NAME`, default `askdiq_session`) to `session`: `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` when `cookie_secure` (`SESSION_COOKIE_SECURE`), `Expires=expires_at`. Never expose it to client JavaScript. Redirect to `/` (active) or `/chat` (pending/rejected) based on `me.status`; on 403 suspended redirect to the suspended page. | |
| 6 | Every proxied call: `X-API-Key`, `X-Session: <cookie value>`, `X-Forwarded-For`; on `POST`/`PUT`/`PATCH`/`DELETE` also `X-Requested-With: askdiq` and forward the browser's `Origin`. | Table above. |
| 7 | Page gate: `GET /api/proxy/auth/me` returns `{user, role, status, permissions, version, access_contact_email}` (weak `ETag`, `Cache-Control: private, max-age=0`, `Vary: X-API-Key, X-Session`). 401 means go to `/login`. | |
| 8 | Sign out: `POST /api/proxy/auth/logout` (**204**), then delete the cookie. | The session is revoked server-side and `logout` is audited. |

In development with `AUTH_DEV_MOCK=true` the authorize URL points at
`APP_BASE_URL/mock-idp/...`, so the frontend must forward `/mock-idp/*` to the
backend (development only), or set `AUTH_MOCK_BASE_URL` to the backend URL
that both the browser and the backend can reach.

Other `/auth` routes: `GET /auth/access-request` (own status and latest
request) and `POST /auth/access-request` (`{"requested_role"?, "justification"?}`;
**409** when already active or a request is pending). User Management
(superadmin only, `users:manage`): `/admin/users-accounts` (list, `stats`,
detail, `PATCH` with `version`, `revoke-sessions`, `invite`) and
`/admin/access-requests` (queue, `approve`, `reject`, `bulk-approve`). Auth
audit events appear on `/governance/audit-log` and `/admin/governance/logs`
only for callers with `audit:auth:read` (superadmin).

### Full-stack sign-in smoke

`backend/scripts/smoke_journeys.py` signs in through port 3000 against the
mock IdP (a pending user gets 403 on `/api/proxy/admin/dashboard`, a
`SUPERADMIN_EMAILS` user gets 200, logout invalidates the cookie). Run it
standalone (`venv\Scripts\python.exe scripts\smoke_journeys.py --base-url
http://localhost:3000`) or call `run_signin_journeys()` from
`scripts/smoke_e2e.py` after its health checks.

## Entra ID go-live checklist (owner)

Nothing else changes in code: real Entra is switched on by `.env` alone.

1. **Register the app in Entra ID** (single tenant). Add the Web redirect URI
   `http://localhost:3000/auth/callback` (and the production HTTPS URL, e.g.
   `https://askdiq.example.com/auth/callback`). Create a client secret. Grant
   the delegated permissions `openid`, `profile` and `email`. Optionally add
   the `email` optional claim to the ID token (otherwise `preferred_username`
   is used).
2. **Fill in `backend/.env`** (copy from `backend/.env.example`; never commit
   it): `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET`,
   `ENTRA_REDIRECT_URI` (must equal `APP_BASE_URL` + `/auth/callback`),
   `SESSION_SECRET` (`python -c "import secrets; print(secrets.token_hex(32))"`),
   `SUPERADMIN_EMAILS` (your own email), `ACCESS_CONTACT_EMAIL`, and
   `AUTH_DEV_MOCK=false`. Fill in `frontend/.env.local`: `APP_BASE_URL`,
   `SESSION_COOKIE_NAME` (same as the backend) and `SESSION_COOKIE_SECURE`
   (`true` over HTTPS).
3. **Preflight:** `cd backend` then
   `venv\Scripts\python.exe scripts\verify_sso_config.py`. Every check must
   PASS (it prints check names only, never values).
4. **Sign in as a superadmin:** start the backend and `npm run dev` (port
   3000), open `http://localhost:3000`, and sign in with an email from
   `SUPERADMIN_EMAILS`. You should land on the dashboard.
5. **Approve a second account:** sign in with another account (for example in
   a private window). It should get Chat plus the Access Required page.
   Request access, approve it in User Management with a role, and the second
   account should reach the dashboard without signing in again.

## 5. API Deprecations

- **`GET /admin/usage/cost-breakdown`**: Deprecated as of release 2.0. This legacy monolithic endpoint will be removed in release 2.1.
- **Replacement Endpoints**:
  - `GET /admin/usage/summary`: High-level economics ($ USD and Token Volumes separated) with audience scoping.
  - `GET /admin/usage/users`: Paginated, audience-aware user table with top-consumer intelligence.
  - `GET /admin/usage/sessions`: Paginated session table with turn metrics.
  - `GET /admin/usage/queries`: Paginated turn list with outcome filter and single-turn rate-card inspector.
  - `GET /admin/usage/export`: Streaming CSV export with formula injection sanitization.

## 6. Verify

- Backend health: `http://localhost:8000/health`
- Frontend: `http://localhost:3000`
- `docker compose logs -f backend` — confirm no startup errors

## Notes / gotchas

- `fastembed`'s cross-encoder reranker downloads its ONNX model from
  Hugging Face on first use inside the backend container — it needs
  outbound network access at least once (or a pre-warmed model cache
  volume) for reranking to work.
- The backend's `DATABASE_URL` in `backend/.env` (`localhost:5433`) is only
  correct for non-Docker local dev; Compose always overrides it.
- `docker-compose.yml` never duplicates secrets into the compose file
  itself — both `env_file` entries load real values from the `.env` files.
- Ensure reverse proxies pass `X-Forwarded-For` with client IP so audience classification accurately separates authentic end users from internal test runners.
