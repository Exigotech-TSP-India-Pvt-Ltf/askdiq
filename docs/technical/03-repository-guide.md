# Repository guide

Scope: the layout of the AskDIQ repository as of `preview/view` (9115c45): an annotated directory tree and the
purpose of every top-level folder and important file, backend module, script, test group, frontend route,
component folder and library file. Files that exist only on in-flight branches are marked
**In progress (branch `X`)**; current status of every work item is in [the plan](../plan.md).

Contents

- [Directory tree](#directory-tree)
- [Root files](#root-files)
- [Backend](#backend)
- [Frontend](#frontend)
- [Repository tooling](#repository-tooling)
- [Files added by in-flight branches](#files-added-by-in-flight-branches)

## Directory tree

Excludes `node_modules/`, `.git/`, `venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.next/`.

```text
askdiq/
├── .github/
│   ├── audit/                    reviewed vulnerability allow-lists (pip-audit, npm audit)
│   ├── workflows/ci.yml          CI: backend-lint, backend-tests, frontend-checks, fullstack-smoke, perf-smoke
│   ├── dependabot.yml            weekly pip / npm / actions updates (minor+patch grouped)
│   └── pull_request_template.md  PR checklist
├── .vscode/settings.json         interpreter = backend/venv, Ruff formatter, Pylint
├── backend/                      FastAPI + async SQLAlchemy + Postgres/pgvector API
│   ├── main.py                   FastAPI app: lifespan, middleware, routers
│   ├── app/
│   │   ├── api/routes/           HTTP route handlers (thin; call services)
│   │   ├── auth/                 Entra OIDC, sign-in sessions, RBAC, mock IdP
│   │   ├── branding/             white-label branding (palette, assets, cache)
│   │   ├── chunking/             chunking strategies + factory
│   │   ├── core/                 config, DB engine, middleware, cache, rate limit, SSE, events
│   │   ├── dashboard/            dashboard / usage / evaluation chart aggregates
│   │   ├── db/                   safe SQL query builders
│   │   ├── documents/            KB inventory and citation analytics
│   │   ├── entities/             visitors and conversations entity pages
│   │   ├── evaluation/           offline RAG evaluation harness
│   │   ├── feedback/             feedback / RLHF triage loop
│   │   ├── governance/           audit, logs, traces, monitoring, PII, costing, audience
│   │   ├── guardrails/           pre- and post-generation checks
│   │   ├── harness/              RAG orchestrator and LLM prompt wrapper
│   │   ├── ingestion/            document ingestion pipeline
│   │   ├── insights/             turn classifier, taxonomy, analytics rollups
│   │   ├── jobs/                 Postgres SKIP LOCKED job queue and worker
│   │   ├── models/               SQLAlchemy ORM models
│   │   ├── retrieval/            embeddings, pgvector store, reranker, retriever
│   │   └── schemas/              Pydantic request/response models
│   ├── alembic/                  migrations (env.py, versions/ - 23 revisions)
│   ├── data/                     KB corpus (real_docs/*.md) + promoted test cases
│   ├── scripts/                  CLI tools: ingest, seed, backfill, eval, load, smoke
│   ├── tests/                    pytest suite (+ helpers, fixtures/)
│   ├── Dockerfile, Dockerfile.prod, .dockerignore
│   ├── .env.example
│   ├── alembic.ini, pyproject.toml (ruff + pylint), pytest.ini, requirements.txt
├── frontend/                     Next.js 14 admin dashboard (App Router)
│   ├── app/                      routes, layouts, route handlers (proxy, auth)
│   ├── components/               UI components by area
│   ├── lib/                      API clients, contexts, hooks, pure helpers; lib/server/ = server-only
│   ├── tests/                    vitest + Testing Library (components, lib, journeys)
│   ├── scripts/audit-gate.mjs    npm audit gate
│   ├── public/                   static assets
│   ├── middleware.ts             CSP nonce + signed-out redirect
│   ├── Dockerfile, .dockerignore, .env.local.example, .eslintrc.json, .npmrc
│   ├── next.config.js, tailwind.config.ts, postcss.config.js, tsconfig.json, vitest.config.ts
│   └── package.json, package-lock.json
├── docs/                         all documentation (plan.md, deployment/, technical/, walkthroughs/, architecture/)
├── infra/                        deploy kit, server scripts, compose files, nginx confs, prod env templates
│   ├── docker-compose.yml        local full stack (postgres:5433, backend:8000, frontend:3000)
│   ├── docker-compose.prod.example.yml  reference for the server-owned prod compose
│   ├── scale-out/                optional 2-replica compose + nginx reference (not shipped)
│   ├── nginx/, compose/, server/ dashboard nginx blocks, compose block, deploy.sh / rollback.sh
│   └── build_kit.ps1, askdiq-autoheal.sh, backend.env.prod.example, frontend.env.prod.example
└── README.md                     project overview and quick start
```

## Root files

| File | Purpose |
|---|---|
| `README.md` | System overview, architecture, features, local development, testing/CI and links to all docs. |
| `docs/deployment/docker-and-auth-contract.md` | Docker deployment guide and the frontend/backend SSO "frontend contract" (steps referenced by the auth route handlers). |
| `docs/plan.md` | The single current plan: status of every work item, open items, risks. |
| `infra/scale-out/askdiq-api.conf` | Reference nginx config for a two-replica API behind `et-platform-nginx-1` (askdiq.ethub.co). Not the live conf. See [Deployment](08-deployment-operations.md). |
| `infra/docker-compose.yml` | Local stack (`docker compose -f infra/docker-compose.yml up --build`): `pgvector/pgvector:pg18` on host port 5433 (db `ragdb`), backend on 8000 (`backend/.env`, `DATABASE_URL` overridden to the compose network), frontend on 3000 (`frontend/.env.local`, `API_BASE_URL=http://backend:8000`). |
| `infra/scale-out/docker-compose.yml` | Optional scale-out compose (api-1, api-2, worker) with a placeholder network name. Never ship it in a deploy package (cause of the 2026-10-16 incident); see [Deployment](08-deployment-operations.md). |
| `.gitignore` | Ignores env files, venvs, caches, build output. |

## Backend

### Entry point and config files

| File | Purpose |
|---|---|
| `backend/main.py` | FastAPI application entrypoint: lifespan (reranker preload, event bus and job worker via `app/jobs/runtime.py`), middleware (CORS, `LatencyMiddleware`, `SSEAwareGZipMiddleware`, `SecurityHeadersMiddleware`), RFC 9457 exception handlers, router registration. |
| `backend/pyproject.toml` | Project metadata, Ruff rules/ignores/per-file-ignores, Pylint config (`fail-under = 9.34`). |
| `backend/pytest.ini` | `pythonpath = .`, `asyncio_mode = auto`, `testpaths = tests`, markers `real_auth`, `perf`. |
| `backend/requirements.txt` | Pinned backend dependencies (incl. ruff, pylint, bandit, pip-audit, pytest). |
| `backend/alembic.ini`, `backend/alembic/` | Alembic config and migrations (`env.py`, `script.py.mako`, `versions/`). Revision list and chain: [Database](05-database.md#migration-history). |
| `backend/Dockerfile` | Dev/compose image. |
| `backend/Dockerfile.prod` | Production image `askdiq-api` (see [Deployment](08-deployment-operations.md)). |
| `backend/.env.example` | Every backend env var with placeholder values. See [Configuration](06-configuration.md). |
| `infra/backend.env.prod.example` | Production env template. |

### `backend/app/api/routes/`

Full route list: [API reference](04-api-reference.md).

| Module | Purpose |
|---|---|
| `admin.py` | Admin endpoints: users, conversations, dashboard (`GET /admin/dashboard`); includes the entity, feedback, governance and usage sub-routers. |
| `admin_entities.py` | Visitors and conversations entity-page stats under `/admin`. |
| `admin_feedback.py` | Feedback / RLHF loop admin API. |
| `admin_governance.py` | Governance endpoints under `/admin/governance` (overview, monitoring, logs, traces, stream). |
| `admin_usage.py` | SQL usage and cost aggregation API. |
| `auth.py` | `/auth/*`: SSO sign-in (`/auth/sso/login`, callback exchange), current user, logout, access requests. |
| `branding.py` | Public `GET /branding`, brand assets, admin branding updates. |
| `documents.py` | KB documents: inventory, detail, canonical Markdown, chunks, citations. |
| `governance.py` | Read access to the audit trail written by `app/governance/audit.py`. |
| `health.py` | `GET /health` (DB probe, reranker state). |
| `ingest.py` | Document ingestion (incremental: only new/changed chunks are embedded). |
| `insights.py` | `/admin/insights/*`: Chatbot Insights analytics. |
| `metrics.py` | `/metrics/latency`, `/metrics/evaluation`, `/metrics/evaluation/live`, `/metrics/evaluation/record/{id}`. |
| `query.py` | `POST /query`: the RAG chat endpoint used by the website and the admin chat. |
| `sessions.py` | Chat session management: list, create, get, rename, delete sessions, message feedback. |
| `user_management.py` | User Management API (superadmin only, `users:manage`). |

### `backend/app/auth/`

| Module | Purpose |
|---|---|
| `admin_service.py` | User Management: users, roles, access requests. |
| `audit.py` | Auth audit events written to the immutable `audit_log`. |
| `deps.py` | Authorization dependencies (`require_permission`, `require_service_key`, ...) and the proxy contract. |
| `mock_idp.py` | Entra-faithful mock OIDC provider (development and tests only). |
| `oidc.py` | Entra ID OIDC client: authorization code flow with PKCE. |
| `permissions.py` | Roles, statuses and the single permission table. |
| `principal.py` | `AuthUser`: the authenticated user resolved for one request. |
| `repository.py` | Persistence for sign-in sessions (`auth_sessions` joined to `users`). |
| `service.py` | Sign-in, self-service access requests and logout. |
| `sessions.py` | Sign-in session lifecycle (TTL, idle timeout, rotation). |
| `user_repository.py` | Persistence for users, access requests and User Management stats. |

### `backend/app/core/`

| Module | Purpose |
|---|---|
| `cache.py` | TTL-bounded in-memory cache with single-flight protection. |
| `circuit.py` | Circuit breaker for outbound dependencies (fail closed). |
| `compression.py` | GZip middleware that never buffers Server-Sent Events. |
| `concurrency.py` | Event-loop thread pool sizing, `single_flight`, `gather_scoped` / `ScopedRunner` (parallel aggregation, one session per task). |
| `config.py` | Centralized `Settings` (pydantic-settings, reads `.env`). |
| `database.py` | Async engine and pool, `get_db`, `get_session_factory`, statement-timeout handling. |
| `events.py` | Cross-worker event bus over Postgres LISTEN/NOTIFY. |
| `exceptions.py` | Global exception handlers returning RFC 9457 `problem+json`. |
| `http_cache.py` | Conditional GET helpers: weak ETag, 304, Cache-Control (`cached_response`). |
| `logging_config.py` | structlog logging with a background QueueListener. |
| `middleware.py` | Per-request latency tracking (`METRICS`). |
| `quota.py` | Rolling 60 s in-memory Azure OpenAI TPM/RPM telemetry. |
| `rate_limit.py` | Sliding-window rate limiter for `/query`, feedback and heavy admin routes. |
| `request_meta.py` | Client IP extraction for audit and rate limiting. |
| `security_headers.py` | Response security headers (CSP, HSTS, ...). |
| `serialization.py` | Streaming serialization and CSV formula-injection neutralization. |
| `sessions.py` | Server-side chat session lifecycle: creation, expiry, history loading. |
| `sse.py` | Server-Sent Events helper. |
| `state.py` | Shared state backends (Postgres instead of Redis; memory in tests). |
| `stats.py` | Percentile helpers for `/metrics/latency` and dashboards. |
| `tasks.py` | Supervised fire-and-forget background tasks. |

### Other `backend/app/` packages

| Module | Purpose |
|---|---|
| `branding/assets.py` | Brand asset validation: magic-byte sniffing, SVG sanitizing. |
| `branding/defaults.py` | Built-in brand defaults (Exigo Tech teal theme, DeployIQ copy). |
| `branding/palette.py` | Palette derivation and WCAG contrast checks (pure). |
| `branding/service.py` | Resolve, cache, update and store branding and assets. |
| `chunking/base.py` | Interface every chunking strategy implements. |
| `chunking/cleaning.py` | Strips repeated header/footer/page-count boilerplate. |
| `chunking/factory.py` | Selects a chunking strategy by name. |
| `chunking/fixed_size.py` | Baseline fixed-size chunking with token overlap. |
| `chunking/metadata_aware.py` | Metadata-aware chunking wrapper. |
| `chunking/semantic.py` | Sentence-boundary semantic chunking. |
| `chunking/structure_aware.py` | Structure-aware chunking for Markdown (default for the corpus). |
| `dashboard/activity.py` | Live activity feed (newest audit events). |
| `dashboard/eval_series.py` | Evaluation (Live) chart series. |
| `dashboard/queries.py` | Single-statement, audience-scoped aggregates behind `GET /admin/dashboard`. |
| `dashboard/service.py` | Windowed KPIs, sparklines, deltas, chart series. |
| `dashboard/usage_series.py` | Usage and Cost chart series (cost by stage, month-end projection). |
| `dashboard/window.py` | Time windows, buckets and pure chart arithmetic. |
| `db/query_builders.py` | Parameterized SQL builders and allow-list sorting. |
| `documents/citations.py` | Citation analytics for documents and chunks. |
| `documents/inventory.py` | Document inventory with ingestion metadata. |
| `entities/conversations.py` | Conversation stats, list filters, per-turn detail. |
| `entities/visitors.py` | Visitor stats and per-visitor timeline. |
| `evaluation/dataset.py`, `dataset_extra.py` | Evaluation cases (incl. abstention, safety, robustness). |
| `evaluation/evaluator.py` | Runs the dataset through the real pipeline. |
| `evaluation/judge.py`, `judge_extra.py` | Opt-in LLM-as-judge scoring (`--judge`). |
| `evaluation/kb_eval.py` | Knowledge-base and ingestion evaluation. |
| `evaluation/metrics.py`, `quality.py` | Pure scoring and quality checks. |
| `feedback/analytics.py` | Feedback analytics (parallel SQL aggregates). |
| `feedback/jobs.py` | Job: re-run a golden (promoted) case through the pipeline. |
| `feedback/repository.py` | Feedback SQL: list, upsert, triage updates, export. |
| `feedback/service.py` | Triage state machine, promotion, bulk actions, export. |
| `governance/audience.py` | Audience scope engine (real users vs test IPs). |
| `governance/audit.py` | Writes audit entries for query/ingest/guardrail events. |
| `governance/auth.py` | Client-id sanitizing helper. |
| `governance/costing.py` | Configuration-driven token costing. |
| `governance/log_summary.py` | Human-readable summaries and categories for audit rows. |
| `governance/logs.py` | Governance Logs: filters, keyset pages, level chips, CSV. |
| `governance/monitoring.py` | Monitoring and trace helpers. |
| `governance/online_eval.py` | Online async quality sampler. |
| `governance/outcomes.py` | Central outcome resolution for chat exchanges. |
| `governance/overview.py` | Governance Overall tab in one call. |
| `governance/pagination.py` | Keyset pagination and bounded totals. |
| `governance/pii.py` | Australian and enterprise PII detection/redaction. |
| `governance/quota_series.py` | Per-process quota time series for Monitoring. |
| `governance/stream.py` | Live Governance SSE stream: one shared producer per worker and audience. |
| `governance/test_ips.py` | Test/dev IPs excluded from user analytics. |
| `governance/timeseries.py` | Persisted, audience-scoped governance aggregates. |
| `governance/traces.py` | Traces: filtered keyset list and per-request detail. |
| `governance/usage_queries.py` | Shared SQL builders for Usage and Cost analytics. |
| `guardrails/conversational.py` | Small-talk detection (greetings, thanks, capability asks). |
| `guardrails/pre_checks.py` | Pre-query checks (unsafe patterns, gibberish, length). |
| `guardrails/post_checks.py` | Post-generation checks (negative answers, groundedness). |
| `harness/orchestrator.py` | The RAG harness: retrieve, guardrails, LLM, validation, retries. |
| `harness/prompts.py` | Azure OpenAI generation wrapper and prompt assembly. |
| `ingestion/docx_converter.py` | Server-side DOCX to Markdown conversion. |
| `ingestion/naming.py` | Canonical document identity and naming validation. |
| `ingestion/normalizer.py` | Deterministic Markdown normalization. |
| `ingestion/service.py` | Atomic, concurrency-safe ingestion service. |
| `insights/analytics_repository.py`, `analytics_service.py` | Insights dashboard SQL and formulas. |
| `insights/classifier.py` | Hybrid classifier: rule stage then batched LLM under a budget. |
| `insights/jobs.py`, `triggers.py` | Insights job handlers; lightweight write-path enqueue trigger. |
| `insights/llm.py`, `rules.py` | LLM batch stage; deterministic rule stage. |
| `insights/repository.py`, `service.py` | Insights data access; classification and rollup service. |
| `insights/taxonomy.py`, `taxonomy_service.py` | Taxonomy codes; taxonomy administration. |
| `insights/types.py` | Value objects between classifier stages. |
| `jobs/handlers.py` | Built-in job handlers. |
| `jobs/queue.py` | Postgres job queue: enqueue, claim (`FOR UPDATE SKIP LOCKED`), retry. |
| `jobs/registry.py` | Handler registry (`@job_handler("insights.classify")`). |
| `jobs/runtime.py` | Lifespan wiring for background services (event bus, in-process worker). |
| `jobs/worker.py` | Job worker runner; standalone via `python -m app.jobs.worker`. |
| `models/db_models.py` | All SQLAlchemy ORM models. See [Database](05-database.md). |
| `retrieval/embeddings.py` | Azure OpenAI embedding client. |
| `retrieval/reranker.py` | Cross-encoder reranker over the cosine-recall pool. |
| `retrieval/retriever.py` | Embeds a query and retrieves top-k chunks. |
| `retrieval/vector_store.py` | pgvector similarity search wrapper. |
| `schemas/*.py` | Pydantic models per area: `auth`, `branding`, `dashboard`, `document`, `entity_stats`, `feedback`, `filters`, `governance`, `insights`, `metrics`, `query`, `session`, `user_management`. |

### `backend/scripts/`

Run from `backend/` with the venv interpreter.

| Script | Purpose |
|---|---|
| `backfill_evaluation_records.py` | Backfill `evaluation_records` from historical `chat_messages`. |
| `backfill_turn_insights.py` | Queue insights classification for unlabelled chat history. |
| `backfill_usage_accounting.py` | One-time idempotent backfill of legacy usage accounting (dry run; `--apply`). |
| `check_tpm.py` | Query Azure OpenAI rate limits and TPM/RPM quotas. |
| `extract_insights_fixture.py` | Extract the Chatbot Insights reference dataset into a JSON test fixture. |
| `ingest_real_docs.py` | Incrementally ingest `data/real_docs/*.md` (unchanged docs skipped). |
| `inventory_kb_docs.py` | Read-only inventory report of `data/real_docs`. |
| `reconcile_document_names.py` | Reconcile legacy document names, detect case-variant collisions. |
| `rescan_pii.py` | Rescan stored chunks for newly introduced PII entities. |
| `run_admin_load_test.py` | HTTP load/concurrency test with performance budgets (`--smoke` used by CI `perf-smoke`). |
| `run_concurrency_test.py` | N virtual users fire distinct queries at the real RAG pipeline. |
| `run_evaluation.py` | Run the evaluation dataset against the live pipeline (`--ids`, `--types`). |
| `seed_comprehensive_deployiq_data.py` | Seeds 1,000+ synthetic rows across all tables (keeps documents/chunks). |
| `seed_ingest.py` | Ingest via the live `/ingest` API (`--api-url`, `--token`). |
| `seed_usage_data.py` | Deterministic usage data seeder (load tests, analytics validation). |
| `show_chunking.py` | Print one document's stored chunks. |
| `smoke_e2e.py` | Full-stack smoke (no browser): Next.js proxy to FastAPI. Used by CI `fullstack-smoke`. |
| `smoke_journeys.py` | Sign-in smoke journeys through the frontend (`--base-url`). |
| `verify_sso_config.py` | Owner preflight for real Entra ID SSO configuration. |
| `seed_realistic_activity.py`, `seed_realistic_corpus.py` | **In progress (branch `feat/realistic-demo-data`)**: realistic demo activity seeder (dry run by default, `--apply`, `--scale`, `--seed`, `--purge`) and its static vocabulary. |
| `prestart.py` | **In progress (branch `chore/prod-deploy-fullstack`)**: container pre-start (wait for DB, `alembic upgrade head` under an advisory lock, KB ingestion, optional usage backfill). |

### `backend/data/`

| Path | Purpose |
|---|---|
| `data/real_docs/*.md` | The knowledge-base corpus (11 DeployIQ / Exigo Tech Markdown docs: home, about us, platform, E8 x2, zero trust, responsible AI, trust center, CIS benchmark, ISO 27001 controls, security baseline). |
| `data/promoted_test_cases.json` | Golden cases promoted from feedback, used by evaluation. |

### `backend/tests/`

pytest suite. Markers (`pytest.ini`): `real_auth` (real SSO session resolution, no signed-in test user override)
and `perf` (timing gates; CI runs them in a separate step without coverage). `conftest.py` also registers `db`
(test needs the database without using the DB fixtures). See [Development](09-development.md#tests).

| Area | Files |
|---|---|
| Shared fixtures | `conftest.py` (test engine from `TEST_DATABASE_URL`, `client`, `db_session`, signed-in superadmin override, `ALLOW_DB_SKIP`) |
| Helpers | `auth_helpers.py`, `dashboard_helpers.py`, `feedback_helpers.py`, `governance_helpers.py`, `pg_helpers.py`, `loop_guard.py` (event-loop blocking detector), `insights_fixture.py` |
| Fixtures | `fixtures/entra_discovery_v2.json`, `fixtures/insights_reference.json` |
| Auth / RBAC | `test_auth_flow_integration`, `test_auth_oidc`, `test_auth_rbac`, `test_auth_routes`, `test_auth_sessions`, `test_auth_settings`, `test_user_management`, `test_verify_sso_config`, `test_route_guards` (walks every route; must be updated for each new route) |
| Security | `test_owasp_compliance`, `test_phase0_security`, `test_phase5_security_and_load`, `test_security_headers`, `test_rate_limit`, `test_admin_rate_limit_and_audit`, `test_error_handling`, `test_circuit_breaker` |
| RAG pipeline | `test_orchestrator`, `test_pre_checks`, `test_post_checks`, `test_prompts`, `test_reranker`, `test_decompose_query`, `test_vague_and_edge_cases`, `test_vector_store_selection` |
| Ingestion / documents | `test_chunking_strategies`, `test_structure_aware_chunking`, `test_docx_converter`, `test_ingestion_matrix`, `test_naming`, `test_normalizer`, `test_documents_inventory`, `test_phase4_documents` |
| Dashboard / usage | `test_dashboard_aggregates`, `test_usage_eval_series`, `test_usage_queries`, `test_usage_ui_contracts`, `test_stage_accounting`, `test_stage_economic_aggregator`, `test_test_ips_and_cost_fixes`, `test_visitor_conversation_stats`, `test_stats` |
| Governance | `test_governance_defects`, `test_governance_endpoints`, `test_governance_integration`, `test_phase3_governance`, `test_audit_sources`, `test_outcomes`, `test_audience_engine`, `test_phase2_audience_telemetry` |
| Feedback | `test_feedback_integration`, `test_feedback_loop`, `test_feedback_triage_fields`, `test_user_feedback` |
| Insights | `test_insights_classifier`, `test_insights_endpoints`, `test_insights_fixture`, `test_insights_integration`, `test_insights_parity` |
| Evaluation | `test_eval_dataset`, `test_eval_kb_quality`, `test_eval_metrics`, `test_metrics_eval` |
| Platform / perf | `test_async_only`, `test_concurrency_50` (perf), `test_phase8_concurrency_load`, `test_http_efficiency`, `test_jobs_queue`, `test_shared_state`, `test_single_flight`, `test_loop_guard`, `test_load_scripts`, `test_phase3_streaming_and_indexes` |
| Phase regression | `test_phase0_phase1`, `test_phase1_filters_and_queries`, `test_phase2_phase3`, `test_phase6_validation`, `test_filter_schemas`, `test_query_builders` |
| Branding | `test_branding`, `test_branding_integration` |
| Smoke | `test_smoke_journeys`, `test_smoke_signin_journey` |

## Frontend

### Top-level files

| File | Purpose |
|---|---|
| `frontend/middleware.ts` | Per-request CSP nonce and the signed-out redirect to `/login` (UX only; the API is the security boundary). Public paths: `/login`, `/suspended`. |
| `frontend/next.config.js` | `output: "standalone"`, security response headers (`buildSecurityHeaders`; HSTS in production). |
| `frontend/package.json` | Scripts `dev` (port 3000, turbo), `build`, `start`, `lint`, `typecheck`, `test`, `audit:gate`. Next 14.2.35, React 18.3.1. |
| `frontend/vitest.config.ts` | jsdom environment, `tests/setup.ts`, `@` alias. |
| `frontend/.env.local.example` | `API_BASE_URL`, `API_KEY`, `APP_BASE_URL`, session cookie, `ACCESS_CONTACT_EMAIL`, `AUTH_DEV_MOCK`. |
| `frontend/Dockerfile`, `.dockerignore` | Dev/compose image. |
| `frontend/.npmrc` | `legacy-peer-deps=true`. |
| `frontend/tailwind.config.ts`, `postcss.config.js` | Tailwind theme wired to `lib/theme-tokens.ts`. |

### `frontend/app/` routes

| Route | File | Purpose |
|---|---|---|
| `/` | `app/page.tsx` | Dashboard for approved users, Chat for other signed-in users, otherwise sign-in. |
| root layout | `app/layout.tsx`, `globals.css` | Server layout: branding fetch, theme pre-paint script, providers. |
| errors | `app/error.tsx`, `app/not-found.tsx` | Route error boundary and 404. |
| `/login` | `app/login/page.tsx` | Sign-in page (renders allow-listed `?error=` messages). |
| `/suspended` | `app/suspended/page.tsx` | Shown when sign-in is refused for a suspended account. |
| `(app)` group | `app/(app)/layout.tsx` | Authenticated shell (sidebar, header, route gate). |
| `/chat` | `(app)/chat/page.tsx` | Admin chat against `POST /query`. |
| `/documents`, `/documents/[id]` | `(app)/documents/*` | KB inventory and document detail. |
| `/evaluation`, `/evaluation-live` | `(app)/evaluation/page.tsx`, `(app)/evaluation-live/page.tsx` | `/evaluation` redirects to `/admin/evaluation-live`; `/evaluation-live` is the live evaluation page. |
| `/admin` | `(app)/admin/page.tsx`, `admin/layout.tsx` | Dashboard. |
| `/admin/analytics` | `(app)/admin/analytics/page.tsx` | Chatbot Insights analytics. |
| `/admin/conversations`, `/admin/conversations/[id]` | `(app)/admin/conversations/*` | Conversations entity list and detail. |
| `/admin/users`, `/admin/users/[id]` | `(app)/admin/users/*` | Visitors entity list and detail. |
| `/admin/evaluation`, `/admin/evaluation-live` | `(app)/admin/evaluation*/page.tsx` | `/admin/evaluation` redirects to `/admin/evaluation-live`. |
| `/admin/feedback` | `(app)/admin/feedback/page.tsx` | Feedback workspace (review queue, gaps, golden cases). |
| `/admin/governance` (+ `/logs`, `/monitoring`, `/traces`) | `(app)/admin/governance/**` | Governance tabs (`OverallView`, `LogsView`, `MonitoringView`, `TracesView`). |
| `/admin/settings` | `(app)/admin/settings/page.tsx` | Branding and taxonomy settings. |
| `/admin/usage` | `(app)/admin/usage/page.tsx` | Usage and Cost console. |
| `/admin/user-management` | `(app)/admin/user-management/page.tsx` | User Management (superadmin). |
| `/api/proxy/*` | `app/api/proxy/[...path]/route.ts` | Server-side proxy to the backend: adds `X-API-Key`, session header, CSRF check, path hygiene; the browser never calls the backend directly. |
| `/auth/login` | `app/auth/login/route.ts` | Starts SSO: `GET /auth/sso/login`, sets the state cookie, redirects to the IdP. |
| `/auth/callback` | `app/auth/callback/route.ts` | IdP redirect target: state check, backend code exchange, session cookie. |
| `/auth/callback/[provider]`, `/api/auth/callback/[provider]` | `app/auth/callback/[provider]/route.ts`, `app/api/auth/callback/[provider]/route.ts` | Alternate redirect URIs (provider-suffixed, NextAuth-style) handled like `/auth/callback`. |
| `/mock-idp/*` | `app/mock-idp/[...path]/route.ts` | Dev-only forwarder to the backend mock IdP (404 unless `AUTH_DEV_MOCK=true`). |
| `/branding/assets/[sha]` | `app/branding/assets/[sha]/route.ts` | Public passthrough for content-addressed brand images. |

### `frontend/components/`

| Folder / file | Purpose |
|---|---|
| `Sidebar.tsx`, `ThemeToggle.tsx`, `ChatSessionList.tsx`, `MessageBubble.tsx` | App navigation, theme switch, chat session list, chat bubble (badges, sources). |
| `admin/` (root) | Shared admin building blocks: `DataTable`, `TableCard`, `TableToolbar`, `Pagination`, `CursorPagination`, `TabGroup`, `DashboardHeader`, `AudienceScopeTabs`, `QueryInspectorDrawer`, `EvaluationCompareMatrix`. |
| `admin/analytics/` | Chatbot Insights view: KPI strip, trend, topics treemap, flow sankey, drop-off, quality donut, URL state. |
| `admin/dashboard/` | Dashboard KPIs, charts, activity feed, health pill. |
| `admin/entity/` | Conversation preview drawer and shared entity-page parts. |
| `admin/evaluation/` | Quality charts and the legacy evaluation report. |
| `admin/feedback/` | Feedback workspace, review queue, drawer, gaps, golden cases, preference data. |
| `admin/governance/` | Health banner, incident list, status timeline, trace drawer, JSON viewer, badges. |
| `admin/insights/` | Stat/highlight cards, outcome and token bars, accuracy badge. |
| `admin/settings/` | Branding tab (dropzone, colour fields, preview), taxonomy tab, questions editor. |
| `admin/usage/` | Usage tabs (overview, queries, sessions, users), cost insights, test IP manager. |
| `admin/user-management/` | User Management page: users, access requests, roles matrix, auth audit, invite dialog. |
| `auth/` | Login view, Microsoft sign-in button, route gate, access-required and suspended views. |
| `brand/` | `BrandLogo`. |
| `charts/` | Chart catalogue (line, bar, area, funnel, radar, gauge, heatmap, sankey/treemap via lazy `heavy/`). |
| `chat/` | Chat header, feedback reason panel, trace link. |
| `documents/` | Upload dialog, Markdown view, format badge. |
| `providers/` | `MotionProvider` (lazy motion features). |
| `shell/` | Header utilities, command center, notification bell, profile menu, role badge, mobile top bar, branded status page. |
| `ui/` | Primitives: modal, drawer shell, confirm dialog, command palette, date range picker, toasts, skeleton, empty/error states, entity link. |

### `frontend/lib/`

| File | Purpose |
|---|---|
| `api.ts` | Base client: all requests go through `/api/proxy`; error parsing. |
| `auth-api.ts`, `auth-context.tsx`, `auth-gate.ts` | `/auth/*` client; signed-in state provider; pure route-gate decision. |
| `permissions.ts`, `use-can.tsx` | Roles/permissions mirroring `app/auth/permissions.py`; permission hook. |
| `safe-redirect.ts`, `login-errors.ts`, `navigation.ts` | `next` open-redirect protection; allow-listed sign-in error codes; full-page navigation. |
| `brand.ts`, `brand-context.tsx`, `brand-palette.ts`, `brand-server.ts` | Brand model, provider, client palette/WCAG mirror, server-only branding fetch. |
| `theme-tokens.ts`, `theme-context.tsx`, `theme-script.ts`, `use-chart-theme.ts` | Colour tokens (single source), theme provider, pre-paint script, chart colours. |
| `motion.ts`, `motion-features.ts` | Motion presets; lazily loaded feature bundle. |
| `query-cache.ts` | Request cache: dedupe, abort, stale revalidation, prefetch, mutations. |
| `dashboard-api.ts`, `use-dashboard.ts` | Dashboard contract and hook. |
| `documents-api.ts`, `docx-to-markdown.ts` | Documents client; in-browser DOCX to Markdown. |
| `entity-stats-api.ts`, `entity-links.ts`, `entity-routes.ts` | Entity pages client and deep links. |
| `feedback-api.ts`, `feedback-filters.ts` | Feedback client and review-queue filters. |
| `governance-api.ts`, `governance-format.ts`, `governance-links.ts`, `use-governance-stream.ts` | Governance client, formatting, deep links, SSE hook. |
| `insights-api.ts`, `usage-insights-api.ts`, `usage-tabs.ts`, `use-usage-data.ts` | Insights and usage clients, usage tab ids, usage data cache. |
| `settings-api.ts`, `user-management-api.ts` | Settings and User Management clients. |
| `audience-context.tsx`, `range-context.tsx`, `chat-context.tsx`, `mobile-nav-context.tsx` | Global audience scope, date range, chat state, mobile nav. |
| `use-deep-link.ts`, `use-table-state.ts`, `use-table-preferences.ts`, `use-cursor-stack.ts` | URL state, table state/preferences, keyset pagination. |
| `use-focus-trap.ts`, `use-popover.ts`, `shortcuts.ts`, `use-shell-notifications.ts`, `use-visibility-polling.ts` | Overlay focus stack, popovers, keyboard shortcuts, access-request count, visibility-aware polling. |
| `format.ts`, `format-text.tsx`, `time.ts`, `storage.ts`, `types.ts` | Formatting, light text formatter, naive-UTC timestamp handling, safe localStorage, backend schema mirrors. |
| `server/backend.ts` | Server-only: backend URL, service key, forwarded client IP headers. |
| `server/auth-cookies.ts` | Session, OIDC state and `next` cookies. |
| `server/contact.ts` | `ACCESS_CONTACT_EMAIL` for pre-session pages. |

### `frontend/tests/`, `frontend/scripts/`, frontend docs

| Path | Purpose |
|---|---|
| `tests/setup.ts` | Testing Library / jest-dom setup. |
| `tests/components/` (+ `analytics/`, `charts/`, `feedback/`, `settings/`) | Component tests (shell, pages, tables, drawers, branding, user management). |
| `tests/lib/` | Unit tests for lib modules, middleware, proxy, next config, theme tokens (`__snapshots__/`). |
| `tests/journeys/` | Multi-step user journeys in jsdom (auth, navigation, dashboard, documents, feedback, governance, analytics parity, settings, user management). |
| `tests/helpers/`, `tests/fixtures/` | Router mocks, auth and entity harnesses, a fake User Management server; dashboard and insights fixtures. |
| `scripts/audit-gate.mjs` | `npm audit --omit=dev` gate: fails on high/critical advisories not in `.github/audit/npm-audit-allowlist.json` or on expired entries. |
| `docs/technical/frontend/contracts/dashboard-aggregations.md`, `entity-stats.md` | API contracts for the dashboard and entity pages. |
| `docs/technical/frontend/theme-audit.md` | Light/dark theme parity inventory. |

## Repository tooling

| Path | Purpose |
|---|---|
| `.github/workflows/ci.yml` | Jobs `backend-lint`, `backend-tests`, `frontend-checks`, `fullstack-smoke` (needs the first three), `perf-smoke` (manual or `perf` label). Details: [Development](09-development.md#continuous-integration). |
| `.github/pull_request_template.md` | Summary, plan task ids, type, light/dark screenshots, migration checks, OWASP rows, performance, assumptions, test evidence, rollback, checklist. |
| `.github/audit/pip-audit-ignore.txt` | Reviewed pip-audit allow-list; every entry needs `expires YYYY-MM-DD` (currently 7 starlette advisories, expire 2026-12-31). |
| `.github/audit/npm-audit-allowlist.json` | Reviewed npm audit allow-list (Next 14 advisories, expire 2026-12-31). |
| `.github/dependabot.yml` | Weekly pip, npm and GitHub Actions updates. |
| `.vscode/settings.json` | Interpreter `backend/venv/Scripts/python.exe`, Ruff formatter and import sorting on save, Pylint from the venv. |
| `docs/technical/` | This documentation set ([index](README.md)). |

## Files added by in-flight branches

From `git diff --name-status preview/view...<branch>` (added files only) and, for uncommitted work, the
worktree status.

| Branch | New files |
|---|---|
| `perf/dashboard-load-speed` | `backend/alembic/versions/b2d6e08d82b3_add_chat_messages_user_created_index.py`, `backend/tests/test_dashboard_load_perf.py`, `frontend/lib/server/session-me.ts` (server-side `/auth/me` preload so the first HTML response carries the shell), `frontend/tests/lib/session-me.test.ts`, `frontend/tests/lib/usage-summary-cache.test.tsx` |
| `fix/chat-page-fetch-timeouts` (uncommitted) | `frontend/app/global-error.tsx` (root-layout error boundary), `backend/tests/test_connection_release.py`, `frontend/tests/components/GlobalError.test.tsx`, `frontend/tests/journeys/chat-backend-timeout.test.tsx`, `frontend/tests/lib/brand-server.test.ts` |
| `feat/realistic-demo-data` | `backend/scripts/seed_realistic_activity.py`, `backend/scripts/seed_realistic_corpus.py`, `backend/tests/test_live_missing_fields.py`, `test_seed_realistic_activity.py`, `test_seed_realistic_db.py` |
| `feat/askdiq-chat-widget` | `frontend/components/askdiq/` (`AskDiqDrawer`, `AskDiqLauncher`, `AskDiqMascot`), `frontend/components/chat/ChatConversation.tsx`, `SourceChips.tsx`, `use-reported-action.ts`, `frontend/lib/askdiq-widget.ts`, `frontend/public/brand/askdiq-mascot*.{png,webp}`, `frontend/tests/components/AskDiqWidget.test.tsx` |
| `fix/shell-nav-logout-theme-dates` | `frontend/app/auth/logout/route.ts` (sign-out that always clears the cookie), `backend/app/dashboard/active_visitors.py` (DAU/WAU/MAU), `frontend/components/admin/ActiveVisitorsPanel.tsx`, `frontend/components/ui/InfoTip.tsx`, `RelativeTime.tsx`, `frontend/lib/active-visitors.ts`, tests `test_active_visitors.py`, `ActiveVisitors.test.tsx`, `RelativeTime.test.tsx`, `time.test.ts` |
| `chore/prod-deploy-fullstack` | `deploy/` (now `infra/`; `RUNBOOK.md` now `docs/deployment/runbook.md`, `build_kit.ps1`, `docker-compose.prod.example.yml`, `frontend.env.prod.example`, `nginx/askdiq-admin.conf.example`), `backend/docker-entrypoint.sh`, `backend/scripts/prestart.py`, `frontend/Dockerfile.prod` |
| `hotfix/query-filters-ip-ingest` (main-based, live in prod) | `backend/tests/test_query_hardening.py` (other changes modify existing files) |
