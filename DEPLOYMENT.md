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

## 3. One-off setup (migrations + ingestion)

Not baked into the backend image's `CMD`, so container restarts never
silently re-run them. Run once the `backend` container is healthy:

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend python scripts/ingest_real_docs.py
```

Re-run `ingest_real_docs.py` any time `backend/data/real_docs/*.md` changes
— it's safe to rerun (replaces existing chunks per source document).

## 4. Verify

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
- No production deployment (cloud hosting, TLS termination, secrets
  manager, etc.) is covered here — this guide is local/dev Docker Compose
  only.
