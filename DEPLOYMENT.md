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
| `backend/.env` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, `AZURE_DEPLOYMENT_NAME`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `API_JWT_SECRET`, `API_KEY_NAME` |
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
