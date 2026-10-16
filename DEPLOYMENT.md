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
403 everywhere, and role changes apply on the next request. Public routes:
`/health`, `/branding` and, with `AUTH_DEV_MOCK=true` in development only,
the mock IdP under `/mock-idp/`.

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

## 7. Production topology: replicas, workers and pool sizing

[docker-compose.prod.yml](docker-compose.prod.yml) runs three containers from
the `askdiq-api:latest` image ([backend/Dockerfile.prod](backend/Dockerfile.prod)):

| Service | Container | Runs | Notes |
|---|---|---|---|
| `api-1` | `askdiq-api-1` | `uvicorn --workers ${WEB_CONCURRENCY}` | `/health` healthcheck, host port `127.0.0.1:5500` |
| `api-2` | `askdiq-api-2` | same | `/health` healthcheck, host port `127.0.0.1:5501` |
| `worker` | `askdiq-worker` | `python -m app.jobs.worker` | Postgres job queue (`SKIP LOCKED`); no HTTP |

- **Load balancing:** nginx ([askdiq.conf](askdiq.conf)) proxies to
  `upstream askdiq_api` (`least_conn`, `max_fails=3 fail_timeout=10s`,
  `keepalive 32`) and fails over on `error timeout http_502 http_503`. POSTs
  are never replayed once sent. SSE paths (`.../stream`, `.../events`) are
  unbuffered with a 1 h read timeout. No sticky sessions are needed:
  rate limits, OIDC state, jobs and live events are shared through Postgres.
- **Workers:** `WEB_CONCURRENCY` (default `2` in compose, `1` in the image)
  sets uvicorn workers per replica. Each worker holds its own DB pool, one
  `LISTEN` connection for the event bus, and its own reranker model in memory,
  so budget RAM per worker too.
- **Jobs:** the API replicas run with `JOBS_WORKER_ENABLED=false`; only the
  `worker` service claims jobs. Any number of workers is safe; to scale with
  `--scale worker=N`, remove its `container_name` first.
- **Overrides:** `WEB_CONCURRENCY`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`,
  `WORKER_DB_POOL_SIZE` and `WORKER_DB_MAX_OVERFLOW` are set in the compose
  file's `environment:` (which wins over `backend/.env`). Change them from the
  shell or a project-root `.env` read by compose interpolation.

### Pool sizing rule

```
replicas × workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW) ≤ max_connections − 20
```

Defaults: `2 × 2 × (10 + 5) = 60`. Also count the job worker's pool
(`5 + 5 = 10`) and one `LISTEN` connection per process (`2 × 2 + 1 = 5`):
**75 in total**, within the `100 − 20 = 80` budget of a stock Postgres. Check
the server with `SHOW max_connections;` and, before adding replicas or
workers, lower `DB_POOL_SIZE` or raise `max_connections`.

### Rollout

```bash
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm api-1 alembic upgrade head
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps        # api-1 / api-2 "healthy"
nginx -t && nginx -s reload                         # inside the et-platform nginx container
```

nginx resolves `askdiq-api-1` / `askdiq-api-2` when it loads the config, so
both containers must exist on the shared network before `nginx -t` or a
reload (otherwise: `host not found in upstream`). Once loaded, a stopped
replica is just marked failed and skipped.

Run migrations before starting the replicas, so several processes never race
on first-time schema setup. For a rolling update restart one replica at a
time (`restart api-1`, wait for healthy, then `api-2`); nginx routes around
the replica that is down. To see which replica served a request, log
`$upstream_addr` in the nginx access log (an `add_header` inside a location
block would drop the server-level security headers).

### Load test

```bash
# From backend/ (venv python). CI-sized smoke: 50 users for 15 s; exits 1 on a breach.
python scripts/run_admin_load_test.py --smoke --base-url https://askdiq.ethub.co
# Full admin profile (newplan §13.5): 50, then 100 users for 10 minutes.
python scripts/run_admin_load_test.py --profile admin --concurrency 100 --duration 600
# Chat profile against a fake-LLM stack; add loadtest-<n> to RATE_LIMIT_EXEMPT_ACTORS.
python scripts/run_admin_load_test.py --profile chat --concurrency 50 --duration 120
```

The API key comes from `--api-key` or `API_KEY_NAME`. The JSON report
(`backend/reports/admin_load_report.json`, or `--output`) lists p50/p70/p95/p99,
error % and 429s per route, plus every newplan §13.4 budget check: aggregation
GETs p95 < 600 ms, paginated lists p95 < 250 ms, and an error rate below 0.5 %
(429s excluded). `--report-only` always exits 0; `--profile usage` reproduces
the original `/admin/usage/*` mix.

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
