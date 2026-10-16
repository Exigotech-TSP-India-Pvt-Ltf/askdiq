# AskDIQ technical documentation

Technical reference for AskDIQ, the retrieval-augmented chat assistant for
the DeployIQ / Exigo Tech website content: a FastAPI + async SQLAlchemy +
Postgres/pgvector backend and a Next.js 14 admin dashboard. Written for
engineers joining the project and for the person deploying and operating it.
Everything here is derived from the code on `preview/view` (commit `9115c45`)
plus the in-flight branches, which are marked **In progress (branch `X`)**.

## Read this first

- **Production is not `preview/view`.** `askdiq.ethub.co` runs `main` plus the
  hotfix branch `hotfix/query-filters-ip-ingest` (Alembic head
  `d6e7f8a9b0c1`). `preview/view` is the integration branch of the admin
  console revamp (Alembic head `254857d10a20`), still unmerged as a stack of
  pull requests.
- **The public widget contract lives on the prod line.** On `preview/view`
  every chat route requires a dashboard SSO session; see
  [Website integration](10-website-integration.md#10-upcoming-changes) and
  [Deployment](08-deployment-operations.md#2-code-in-production-vs-code-in-the-repo).
- Never ship compose files, nginx confs or `.env` files to the server. See
  [Deployment](08-deployment-operations.md#4-golden-rules).

## Documents

| # | Document | For | What it covers |
|---|---|---|---|
| 1 | [Overview](01-overview.md) | Everyone | Purpose, users, features, tech stack, glossary |
| 2 | [Architecture](02-architecture.md) | Engineers | Components, C4 views, request flows (query, dashboard proxy, SSO, jobs, ingestion), concurrency, caching, rate limiting |
| 3 | [Repository guide](03-repository-guide.md) | Engineers | Directory tree and the purpose of every folder and important file |
| 4 | [API reference](04-api-reference.md) | Engineers, integrators | Every route: method, path, guard, models, errors, pagination and filters |
| 5 | [Database](05-database.md) | Engineers, ops | Tables, indexes, triggers, pgvector, ER diagram, migration history, prod vs code head |
| 6 | [Configuration](06-configuration.md) | Engineers, ops | Every backend and frontend environment variable |
| 7 | [Security](07-security.md) | Engineers, reviewers | Auth, RBAC, CSRF, headers, errors, PII, audit, IP handling, OWASP Top 10:2025 mapping |
| 8 | [Deployment and operations](08-deployment-operations.md) | Ops | Prod layout, release procedure, migrations, backfills, backups, rollback, monitoring, incidents |
| 9 | [Development](09-development.md) | Engineers | Local setup, running, tests, lint, CI and the local CI mirror, branch workflow, troubleshooting |
| 10 | [Website integration](10-website-integration.md) | Website frontend team | `/query` contract, visitor identity, IP forwarding, rate limits, 429 handling |
| 11 | [In-flight work](11-in-flight-work.md) | Engineers, reviewers | Unmerged branches and the open PR stack #14-#38 |

## Related documents in the repo

| File | Content |
|---|---|
| `README.md` | Project summary and quick start |
| `DEPLOYMENT.md` | Docker deployment guide, proxy/SSO contract, pool sizing, load tests |
| `PREVIEW_README.md` | The local-only combined preview build (`preview/all`) |
| `newplan.md`, `planchanges4.md` | Design plan (rev 3.x) and execution plan with PR register and OWASP matrix |
| `frontend/docs/contracts/*.md` | Dashboard aggregation and entity-stats API contracts |
| `frontend/docs/interaction-audit.md`, `frontend/docs/theme-audit.md` | UI interaction and theme audits |
| `deploy/RUNBOOK.md` | Production runbook (**In progress (branch `chore/prod-deploy-fullstack`)**) |

## Conventions used in these docs

- Paths are repo-relative (`backend/app/core/config.py`).
- Environment variables are written in upper case (`DATABASE_URL`); the
  matching `Settings` field is the lower-case name.
- "Unverified:" marks a statement that could not be confirmed from the code or
  the repository (for example server state).
- No secrets appear anywhere in these documents.
