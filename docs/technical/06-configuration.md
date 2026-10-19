# Configuration

Scope: every environment variable read by the AskDIQ backend (`backend/app/core/config.py` `Settings`,
plus the few scripts and container files that read the environment directly) and by the Next.js
dashboard (`frontend/`), the values set by the compose files and Dockerfiles, the startup validation
rules, and how configuration differs between `preview/view` (9115c45, the documented base), `main`
plus `hotfix/query-filters-ip-ingest` (what production runs) and the in-flight branches. Defaults
come from the code, not from `.env.example`; where the two disagree it is noted. No real values from
any `.env` are shown.

## Contents

- [How configuration is loaded](#how-configuration-is-loaded)
- [Validation rules](#validation-rules)
- [Backend: core and database](#backend-core-and-database)
- [Backend: Azure OpenAI and LLM](#backend-azure-openai-and-llm)
- [Backend: embeddings, retrieval and rerank](#backend-embeddings-retrieval-and-rerank)
- [Backend: guardrails](#backend-guardrails)
- [Backend: chat sessions](#backend-chat-sessions)
- [Backend: rate limits, quotas and capacity](#backend-rate-limits-quotas-and-capacity)
- [Backend: pricing](#backend-pricing)
- [Backend: auth, SSO and RBAC](#backend-auth-sso-and-rbac)
- [Backend: security, IP handling and headers](#backend-security-ip-handling-and-headers)
- [Backend: CORS](#backend-cors)
- [Backend: feature flags](#backend-feature-flags)
- [Backend: brand and white-label](#backend-brand-and-white-label)
- [Backend: usage and analytics](#backend-usage-and-analytics)
- [Backend: jobs, worker and shared state](#backend-jobs-worker-and-shared-state)
- [Backend: SSE, events and governance](#backend-sse-events-and-governance)
- [Backend: feedback and insights](#backend-feedback-and-insights)
- [Backend: ingestion](#backend-ingestion)
- [Backend: observability and HTTP](#backend-observability-and-http)
- [Backend: testing](#backend-testing)
- [Backend: variables outside Settings](#backend-variables-outside-settings)
- [Frontend](#frontend)
- [Compose files and Dockerfiles](#compose-files-and-dockerfiles)
- [Production (main and hotfix) differences](#production-main-and-hotfix-differences)
- [In-flight configuration](#in-flight-configuration)
- [Drift between code and .env.example](#drift-between-code-and-envexample)

## How configuration is loaded

| Item | Detail |
|---|---|
| Class | `Settings(BaseSettings)` in `backend/app/core/config.py`, read once through `get_settings()` (`@lru_cache`). |
| Source | Process environment, then `.env` in the working directory (`env_file=".env"`). The process environment wins. In containers the env comes from `env_file: ./backend/.env` plus compose `environment:` overrides. |
| Names | Field name upper-cased (pydantic-settings is case-insensitive). `populate_by_name=True`. |
| Unknown keys | `extra="ignore"`: unknown variables (e.g. `AZURE_OPENAI_CREATIVE_TEMPERATURE` in `.env.example`) are silently ignored. |
| Lists | `list[...]` fields are JSON: `CORS_ALLOWED_ORIGINS=["https://a.example"]`, `RATE_LIMIT_EXEMPT_ACTORS=[]`. |
| Secrets | `SESSION_SECRET` and `ENTRA_CLIENT_SECRET` are `SecretStr` (masked in `repr`/logs). |
| Count | 168 fields on `preview/view` (verified by introspecting `Settings.model_fields`). |
| Frontend | Server-only `process.env.*` reads at request time (route handlers, `middleware.ts`, `lib/server/*`). No `NEXT_PUBLIC_*` variables exist, so nothing is baked into the client bundle. |

Rule from `CLAUDE.md`: never create `.env`; copy `backend/.env.example` to `backend/.env` and
`frontend/.env.local.example` to `frontend/.env.local`, then fill in values.

## Validation rules

Enforced at import/startup (`Settings._validate_auth`, field constraints, `main.py` lifespan):

| Rule | Effect |
|---|---|
| `DATABASE_URL`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, `AZURE_DEPLOYMENT_NAME`, `SESSION_SECRET` have no default | Startup fails with a pydantic `ValidationError` if unset. |
| `SESSION_SECRET` empty | `ValueError("SESSION_SECRET must not be empty.")` |
| `ENV=production` and `SESSION_SECRET` shorter than 32 bytes (`MIN_SESSION_SECRET_BYTES`) | Startup refused. Generate with `secrets.token_hex(32)`. |
| `AUTH_DEV_MOCK=true` and `ENV != development` | Startup refused ("only allowed when ENV=development"). |
| `AUTH_DEV_MOCK=true` | Missing `ENTRA_TENANT_ID`/`ENTRA_CLIENT_ID`/`ENTRA_CLIENT_SECRET` are filled with mock placeholders (`MOCK_TENANT_ID`, `MOCK_CLIENT_ID`, `MOCK_CLIENT_SECRET`). |
| `AUTH_DEV_MOCK=false` | `ENTRA_TENANT_ID` (or one parsed from `OIDC_DISCOVERY_URL` of the form `https://<host>/<tenant>/v2.0/.well-known/openid-configuration`), `ENTRA_CLIENT_ID` and `ENTRA_CLIENT_SECRET` (or their `OIDC_*` aliases) are required; otherwise `Missing SSO settings: ...`. |
| `ENTRA_REDIRECT_URI` unset | Derived as `APP_BASE_URL` + `/auth/callback`. |
| `API_KEY_NAME` empty (default) | App starts but every authenticated route answers 401 (fail closed, `require_service_key`). |
| Field constraints | `ge`/`gt`/`le` bounds shown in the Default column below (e.g. `SESSION_TTL_MINUTES >= 5`); `INSIGHTS_TIMEZONE` must match an IANA-style pattern. |
| `ENV=production` and `TRUST_PROXY_HEADERS=false` | Not fatal: lifespan logs `production_startup_warning` (client IPs and per-IP limits will see the proxy's address). |
| `STATE_BACKEND` not `auto`/`memory`/`postgres` | `ValueError("Unknown STATE_BACKEND ...")` when the state backend is first used (`app/core/state.py`). |

Not validated (operator responsibility): `SESSION_COOKIE_SECURE=true` in production, `CORS_ALLOWED_ORIGINS`
contents, `APP_BASE_URL` matching the frontend's, `API_KEY_NAME` strength.

## Backend: core and database

| Name | Default | Required | Meaning |
|---|---|---|---|
| `DATABASE_URL` | none | Yes | Async SQLAlchemy URL, `postgresql+asyncpg://user:pass@host:5432/db`. One Postgres with pgvector for relational and vector data. In a container use the DB host name (prod: `et-platform-postgres-1`), never `localhost`. Alembic derives its sync URL from it (`backend/alembic/env.py`). |
| `ENV` | `development` | No | `development` / `test` / `production`. `production` turns on HSTS, disables `/docs` (unless `ENABLE_API_DOCS`), enforces the 32-byte session secret and ignores body `ip_address` overrides on `/query`. `test` makes `STATE_BACKEND=auto` use memory. |
| `LOG_LEVEL` | `info` | No | Root log level for structlog (`app/core/logging_config.py`). |
| `DB_POOL_SIZE` | `20` | No | SQLAlchemy pool size per process. Keep replicas x workers x (pool + overflow) <= Postgres `max_connections` - 20. |
| `DB_MAX_OVERFLOW` | `10` | No | Extra connections above the pool. |
| `DB_POOL_PRE_PING` | `true` | No | Validate pooled connections on checkout. |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | No | Recycle connections older than this. |
| `DB_ECHO` | `false` | No | Log every SQL statement (very noisy: includes 1536-float vectors). Not in `.env.example`. |
| `DASHBOARD_STATEMENT_TIMEOUT_MS` | `5000` | No | `SET LOCAL statement_timeout` applied by `read_only_session()` for dashboard reads. |
| `WEB_CONCURRENCY` | `1` | No | Uvicorn workers per container. Read by the `backend/Dockerfile.prod` shell `CMD` (`--workers ${WEB_CONCURRENCY:-1}`); the `Settings` field itself is not read by Python code. |
| `THREAD_POOL_WORKERS` | `100` | No | Size of asyncio's default thread pool (blocking embedding/LLM/rerank calls). Not in `.env.example`. |
| `TASK_DRAIN_TIMEOUT_SECONDS` | `10.0` | No | Max seconds shutdown waits for supervised background tasks. |
| `LATENCY_BUDGET_MS` | `200` | No | Budget for the retrieval + guardrail pipeline (LLM excluded) used by `/metrics/latency`. Not in `.env.example`. |
| `DEV_TEST_IPS` | `127.0.0.1,::1,localhost,0.0.0.0` | No | Comma-separated IPs seeded as test/dev traffic, excluded from user analytics; manageable in the dashboard (`app/governance/test_ips.py`). |

## Backend: Azure OpenAI and LLM

| Name | Default | Required | Meaning |
|---|---|---|---|
| `AZURE_OPENAI_API_KEY` | none | Yes | Azure OpenAI key (secret). |
| `AZURE_OPENAI_ENDPOINT` | none | Yes | Resource endpoint, e.g. `https://<resource>.openai.azure.com/`. |
| `AZURE_OPENAI_API_VERSION` | none | Yes | API version, e.g. `2024-12-01-preview`. |
| `AZURE_DEPLOYMENT_NAME` | none | Yes | Chat deployment (e.g. `gpt-5-mini`). Also the insights classifier model when `INSIGHTS_MODEL` is empty. |
| `AZURE_OPENAI_REQUEST_TIMEOUT` | `60.0` | No | Timeout (s) for chat-completion clients. |
| `AZURE_OPENAI_MAX_RETRIES` | `2` | No | SDK retries for chat, judge and embedding clients. Not in `.env.example`. |
| `AZURE_OPENAI_TEMPERATURE` | `1.0` | No | Defined, but not read by any code at 9115c45 (verified by grep). gpt-5-mini accepts only 1.0. |
| `LLM_MAX_COMPLETION_TOKENS` | `1400` | No | Defined, but not read by any code at 9115c45 (verified by grep). Not in `.env.example`. |
| `LLM_REASONING_EFFORT` | `minimal` | No | `minimal`/`low`/`medium`/`high`; passed as `reasoning_effort` (`app/harness/prompts.py`). Main latency lever. |
| `ANSWER_MAX_WORDS` | `150` | No | Word cap stated in the prompt and checked by the evaluator. |
| `LLM_CONTEXT_MAX_TOKENS` | `2400` | No | Token budget for retrieved evidence in the prompt. |
| `LLM_HISTORY_MAX_TOKENS` | `400` | No | Token budget for chat history in the prompt. |
| `MAX_LLM_RETRIES` | `1` | No | Extra generation attempts on empty/failed/ungrounded output. |
| `OUTBOUND_TIMEOUT_SECONDS` | `30.0` | No | Default timeout for outbound HTTP clients without their own setting (embeddings, httpx). |
| `LLM_BREAKER_FAILURE_THRESHOLD` | `5` | No | Circuit breaker opens after this many transient failures (`app/core/circuit.py`). |
| `LLM_BREAKER_WINDOW_SECONDS` | `60.0` | No | Rolling window for the failure count. |
| `LLM_BREAKER_RESET_SECONDS` | `30.0` | No | Open time before one half-open probe. |

## Backend: embeddings, retrieval and rerank

| Name | Default | Required | Meaning |
|---|---|---|---|
| `EMBEDDING_MODEL` | `text-embedding-3-small` | No | Azure embedding deployment name. |
| `EMBEDDING_DIM` | `1536` | No | Must equal the pgvector column width (migration `f9462f0a85c6`). Changing it needs a migration and re-embedding. |
| `DEFAULT_TOP_K` | `5` | No | Chunks retrieved per query; server-side only (not a request field). |
| `ENABLE_QUERY_DECOMPOSITION` | `true` | No | Split compound questions into sub-queries. |
| `COMPOUND_QUERY_TOP_K` | `9` | No | top-k used for compound queries. |
| `ENABLE_RERANKER` | `true` | No | Cross-encoder rerank of the candidate pool. |
| `RERANKER_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` | No | ONNX reranker model (preloaded at startup). |
| `RERANK_MAX_CONCURRENCY` | `4` | No | Concurrent CPU-bound rerank calls. |
| `RERANK_BATCH_SIZE` | `1` | No | Pairs per ONNX call. |
| `RETRIEVAL_CANDIDATE_MULTIPLIER` | `1` | No | Candidate pool = max(top_k x multiplier, `RETRIEVAL_CANDIDATE_MIN`). |
| `RETRIEVAL_CANDIDATE_MIN` | `8` | No | Minimum candidate pool. |
| `CROSS_DOC_QUOTA_MAX_DOCUMENTS` | `3` | No | Round-robin chunk selection across at most this many documents. |
| `MIN_RETRIEVAL_SCORE` | `0.15` | No | Below this raw cosine score retrieval is treated as off-topic. |
| `RETRIEVAL_FILTER_KEYS` | `["source_type","corpus","ingestion_source"]` | No | **Hotfix only** (`hotfix/query-filters-ip-ingest`, live in prod; not on `preview/view`). Allow-list of `/query` `filters` keys; other keys and non-scalar values are dropped (`usable_filters()` in `app/retrieval/vector_store.py`). |

All of these except the first three are absent from `.env.example`; they take code defaults.

## Backend: guardrails

| Name | Default | Required | Meaning |
|---|---|---|---|
| `GROUNDEDNESS_THRESHOLD` | `0.45` | No | Semantic cosine threshold for the post-generation groundedness check. |
| `MAX_QUERY_LENGTH` | `2000` | No | Pre-check length cap (the request schema also caps `query` at 2000). |
| `PII_ALLOWLIST` | `[]` | No | JSON list of strings never redacted by `redact_pii` (insights masking, `scripts/rescan_pii.py`). Not in `.env.example`. |

## Backend: chat sessions

Chat conversation sessions (`chat_sessions`), not sign-in sessions.

| Name | Default | Required | Meaning |
|---|---|---|---|
| `SESSION_TIMEOUT_MINUTES` | `60` | No | Inactivity after which a chat session expires and a new one starts. Not in `.env.example`. |
| `SESSION_HISTORY_MAX_MESSAGES` | `20` | No | Prior messages loaded as context for a follow-up. Not in `.env.example`. |

## Backend: rate limits, quotas and capacity

| Name | Default | Required | Meaning |
|---|---|---|---|
| `RATE_LIMIT_PER_MINUTE` | `10` | No | `/query` per user per 60 s. Not in `.env.example`. |
| `RATE_LIMIT_PER_MINUTE_PER_IP` | `30` | No | `/query` per client IP per 60 s. Not in `.env.example`. |
| `RATE_LIMIT_EXEMPT_ACTORS` | `[]` | No | JSON list of actors exempt from the per-user limit (load tests only). |
| `AUTH_LOGIN_RATE_LIMIT_PER_MINUTE` | `10` (`>=1`) | No | Per-IP cap on `/auth/sso/login` + `/auth/sso/callback`. |
| `LLM_MAX_CONCURRENCY` | `64` | No | Global in-flight LLM requests per process; above it `/query` returns 429 + `Retry-After`. |
| `LLM_SATURATED_RETRY_AFTER_SECONDS` | `2` | No | `Retry-After` on LLM saturation. |
| `AZURE_OPENAI_TPM_LIMIT` | `1000000` | No | Chat tokens/minute quota used by quota telemetry (`app/core/quota.py`). |
| `AZURE_OPENAI_RPM_LIMIT` | `1000` | No | Chat requests/minute quota. |
| `AZURE_OPENAI_EMBEDDING_TPM_LIMIT` | `1000000` | No | Embedding TPM quota. Not in `.env.example`. |
| `AZURE_OPENAI_EMBEDDING_RPM_LIMIT` | `1000` | No | Embedding RPM quota. Not in `.env.example`. |

Feedback (30/min) and heavy-admin (60/min) limits are code constants, not settings. See
[Security](07-security.md#rate-limiting-and-capacity).

## Backend: pricing

USD per million tokens, used by cost accounting and dashboards.

| Name | Default | Required | Meaning |
|---|---|---|---|
| `PRICE_EMBED_PER_MILLION` | `0.02` | No | Embedding tokens. |
| `PRICE_PROMPT_PER_MILLION` | `0.25` | No | Prompt (input) tokens. |
| `PRICE_OUTPUT_PER_MILLION` | `2.00` | No | Completion tokens. |
| `PRICE_CACHED_PER_MILLION` | `0.125` | No | Cached input tokens. |
| `MONTHLY_COST_BUDGET_USD` | unset (`None`, `>=0`) | No | Budget line on the dashboard cost bullet and usage forecast. `.env.example` sets `100`. |

## Backend: auth, SSO and RBAC

| Name | Default | Required | Meaning |
|---|---|---|---|
| `API_KEY_NAME` | `""` | Effectively yes | Service key the Next.js proxy sends as `X-API-Key`; compared with `hmac.compare_digest`. Empty = every guarded route 401. Must equal frontend `API_KEY`. Despite the name it is the key value. |
| `ENTRA_TENANT_ID` (alias `OIDC_TENANT_ID`) | `None` | Yes unless `AUTH_DEV_MOCK` or derivable from `OIDC_DISCOVERY_URL` | Entra tenant; pinned in issuer and `tid` checks. |
| `ENTRA_CLIENT_ID` (alias `OIDC_CLIENT_ID`) | `None` | Yes unless `AUTH_DEV_MOCK` | App registration client id (`aud`). |
| `ENTRA_CLIENT_SECRET` (alias `OIDC_CLIENT_SECRET`) | `None` | Yes unless `AUTH_DEV_MOCK` | Client secret for the code exchange (secret). |
| `ENTRA_REDIRECT_URI` (alias `OIDC_REDIRECT_URI`) | `APP_BASE_URL/auth/callback` | No | Must equal the redirect URI registered in Entra exactly. |
| `ENTRA_AUTHORITY_HOST` | `https://login.microsoftonline.com` | No | Authority for real Entra (sovereign clouds differ). |
| `OIDC_DISCOVERY_URL` | `""` | No | Explicit discovery URL; empty = built from authority + tenant. Ignored with the mock IdP. Issuer still pinned to the tenant. |
| `OIDC_HTTP_TIMEOUT_SECONDS` | `10.0` (`>0`) | No | Timeout for discovery/JWKS/token calls. |
| `OIDC_STATE_TTL_SECONDS` | `600` (`>=60`) | No | Lifetime of stored `state`/`nonce`/PKCE verifier. |
| `OIDC_METADATA_CACHE_SECONDS` | `3600` (`>=60`) | No | Discovery and JWKS cache. |
| `OIDC_CLOCK_SKEW_SECONDS` | `60` (`0..300`) | No | Leeway for `exp`/`nbf`/`iat`. |
| `APP_BASE_URL` | `http://localhost:3000` | Yes in prod | Public dashboard origin. Used for the default redirect URI, the mock IdP base and the CSRF `Origin` check (`app_origin`). Must equal the frontend's `APP_BASE_URL`. |
| `SESSION_SECRET` | none | Yes | HMAC-SHA256 key for hashing session ids (secret). >= 32 bytes in production. Rotating it invalidates every sign-in session. |
| `SESSION_TTL_MINUTES` | `480` (`>=5`) | No | Absolute sign-in session lifetime. |
| `SESSION_IDLE_MINUTES` | `60` (`>=1`) | No | Idle timeout (capped by the absolute expiry). |
| `SESSION_TOUCH_INTERVAL_SECONDS` | `60` (`>=1`) | No | `last_seen_at` written at most this often. |
| `SESSION_COOKIE_NAME` | `askdiq_session` | No | Returned to the frontend in the callback payload; must match frontend `SESSION_COOKIE_NAME`. |
| `SESSION_COOKIE_SECURE` | `false` | No (set `true` in prod) | Returned in the callback payload; must match the frontend's. |
| `SUPERADMIN_EMAILS` | `""` | No | Comma-separated emails bootstrapped to active superadmin on first sign-in while `pending`. |
| `ACCESS_CONTACT_EMAIL` | `""` | No | Shown on Access Required / suspended pages (also from `/auth/me`). |
| `AUTH_DEV_MOCK` | `false` | No | Mounts the local mock Entra IdP (`app/auth/mock_idp.py`). Refused unless `ENV=development`. |
| `AUTH_MOCK_BASE_URL` | `""` | No | Mock authority base; empty = `APP_BASE_URL` + `/mock-idp`. |

## Backend: security, IP handling and headers

| Name | Default | Required | Meaning |
|---|---|---|---|
| `TRUST_PROXY_HEADERS` | `false` | No (set `true` behind nginx) | Use the first `X-Forwarded-For` hop as the client IP (`get_client_ip`, `app/core/request_meta.py`); otherwise the TCP peer. |
| `ALLOW_CLIENT_IP_OVERRIDE` | `true` in code | No | **Hotfix only** (`hotfix/query-filters-ip-ingest`, live in prod). Honour a valid IPv4/IPv6 `ip_address` in `/query`, `POST /sessions` and feedback bodies (`resolve_client_ip`). **Discrepancy:** the code default is `true`, while the hotfix `.env.example` documents `ALLOW_CLIENT_IP_OVERRIDE=false`. A server whose `.env` omits it gets `true`. On `preview/view` the setting does not exist; `/query` honours `ip_address` only when `ENV != production`. |
| `ENABLE_API_DOCS` | `false` | No | Serve `/docs`, `/redoc`, `/openapi.json` when `ENV=production`. Always served outside production. |
| `SECURITY_CSP` | `default-src 'none'; frame-ancestors 'none'` | No | API `Content-Security-Policy` (skipped on docs pages). |
| `SECURITY_HSTS` | `max-age=63072000; includeSubDomains` | No | Sent only when `ENV=production`. |
| `SECURITY_REFERRER_POLICY` | `strict-origin-when-cross-origin` | No | `Referrer-Policy`. |
| `SECURITY_PERMISSIONS_POLICY` | `camera=(), microphone=(), geolocation=()` | No | `Permissions-Policy`. |
| `SECURITY_COOP` | `same-origin` | No | `Cross-Origin-Opener-Policy`. |
| `SECURITY_FRAME_OPTIONS` | `DENY` | No | `X-Frame-Options`. |

## Backend: CORS

| Name | Default | Required | Meaning |
|---|---|---|---|
| `CORS_ALLOWED_ORIGINS` | `["http://localhost:3000"]` | No | JSON list of allowed origins (credentials allowed). |
| `CORS_ALLOW_HEADERS` | `["Content-Type","X-API-Key","X-Request-ID","X-Client-Id","If-None-Match"]` | No | Explicit header allow-list (no wildcard). |
| `CORS_ALLOW_METHODS` | `["GET","POST","PATCH","DELETE","OPTIONS"]` | No | Explicit method allow-list. |

On `main`/hotfix CORS uses `allow_headers=["*"]` and hard-coded methods; only `CORS_ALLOWED_ORIGINS` exists.

## Backend: feature flags

| Name | Default | Required | Meaning |
|---|---|---|---|
| `ONLINE_EVAL_ENABLED` | `false` | No | LLM-judge scoring of live answers (`app/governance/online_eval.py`). |
| `ONLINE_EVAL_SAMPLE_RATE` | `0.10` | No | Intended sample rate; defined but not read by any code at 9115c45 (verified by grep). |
| `ONLINE_EVAL_MAX_CONCURRENCY` | `4` | No | Concurrent online-eval judge calls. |
| `INGEST_SERVER_CONVERSION` | `true` | No | Defined, but not read by any code at 9115c45 (verified by grep). |
| `INSIGHTS_CLASSIFY_ON_WRITE` | `true` | No | Enqueue `insights.classify` after each assistant message. |
| `JOBS_WORKER_ENABLED` | `true` | No | Run the job worker in-process (see [jobs](#backend-jobs-worker-and-shared-state)). |

## Backend: brand and white-label

Config values are the fallback; the `brand_settings` DB row (Settings > Branding) overrides them.
Colours and login copy are code constants in `backend/app/branding/defaults.py` (`PRIMARY_HEX=#008080`,
`ACCENT_HEX=#00584f`, `TINT_HEX=#eaf6f5`, `LOGIN_EYEBROW`, `LOGIN_HEADLINE`, `LOGIN_SUBTEXT`,
`LOGIN_DESCRIPTION`, `CHAT_SAMPLE_QUESTIONS`) matching the row seeded by migration `b0c1d2e3f4a5`;
they are not environment variables.

| Name | Default | Required | Meaning |
|---|---|---|---|
| `BRAND_NAME` | `DeployIQ` | No | Product name (also the FastAPI title `"<BRAND_NAME> RAG Backend"`). |
| `BRAND_ASSISTANT_NAME` | `AskDIQ` | No | Assistant name. |
| `BRAND_COMPANY_NAME` | `Exigo Tech` | No | Company name. |
| `BRAND_SUPPORT_EMAIL` | `support@deployiq.com` | No | Support email. |
| `BRANDING_CACHE_TTL_SECONDS` | `60` (`>=1`) | No | In-process cache TTL of public `GET /branding`. |
| `BRANDING_HTTP_MAX_AGE_SECONDS` | `300` (`>=0`) | No | Browser `max-age` on `GET /branding`. |
| `BRANDING_ASSET_MAX_BYTES` | `524288` (`>=1024`) | No | Max upload size of a brand asset (PNG, WebP, sanitized SVG). |
| `BRANDING_ASSET_MAX_DIMENSION` | `4096` (`>=16`) | No | Max pixel width/height. |

## Backend: usage and analytics

| Name | Default | Required | Meaning |
|---|---|---|---|
| `AUDIENCE_CACHE_TTL_SECONDS` | `30` | No | Audience analytics cache. |
| `USAGE_SUMMARY_CACHE_TTL_SECONDS` | `15` | No | Usage summary cache. |
| `USAGE_PAGE_MAX_LIMIT` | `200` | No | Max `limit` on usage list endpoints (400 above). |
| `USAGE_MAX_OFFSET` | `10000` | No | Max `offset`. |
| `USAGE_EXPORT_MAX_ROWS` | `10000` | No | CSV export cap. |
| `USAGE_MAX_RANGE_DAYS` | `400` | No | Max date range. |
| `USAGE_AGG_MAX_CONCURRENCY` | `4` | No | Parallel usage aggregations. |
| `AGG_MAX_CONCURRENCY` | `4` | No | Default bound for `gather_scoped()` parallel aggregation. |
| `TEST_IP_MAX_ENTRIES` | `500` | No | Max rows in the test-IP list. |

## Backend: jobs, worker and shared state

| Name | Default | Required | Meaning |
|---|---|---|---|
| `JOBS_WORKER_ENABLED` | `true` | No | In-process worker for the Postgres `SKIP LOCKED` queue. Set `false` on API replicas when a dedicated `python -m app.jobs.worker` runs (`docker-compose.prod.yml` does). |
| `JOBS_MAX_CONCURRENCY` | `4` | No | Concurrent jobs per worker. |
| `JOBS_LOCK_TIMEOUT_SECONDS` | `300` | No | Lock lease before a job is considered abandoned. |
| `JOBS_POLL_INTERVAL_SECONDS` | `1.0` | No | Queue poll interval. |
| `JOBS_MAX_ATTEMPTS` | `5` | No | Attempts before a job is dead. |
| `JOBS_BACKOFF_BASE_SECONDS` | `2.0` | No | Exponential backoff base. |
| `JOBS_BACKOFF_MAX_SECONDS` | `600.0` | No | Backoff cap. |
| `JOBS_SHUTDOWN_TIMEOUT_SECONDS` | `30.0` | No | Drain time on shutdown. |
| `JOBS_RETENTION_DAYS` | `7` | No | Finished-job retention. |
| `STATE_BACKEND` | `auto` | No | Shared state for rate limits/OIDC state: `auto` (memory when `ENV=test`, else `postgres`), `memory`, `postgres`. |
| `STATE_CLEANUP_INTERVAL_SECONDS` | `60` | No | Expired shared-state row cleanup. |

## Backend: SSE, events and governance

| Name | Default | Required | Meaning |
|---|---|---|---|
| `EVENTS_CHANNEL` | `askdiq_events` | No | Postgres LISTEN/NOTIFY channel for cross-worker events. |
| `EVENTS_RECONNECT_MAX_SECONDS` | `30.0` | No | Max reconnect backoff of the listener. |
| `SSE_HEARTBEAT_SECONDS` | `15.0` | No | Keepalive comment interval on SSE streams. |
| `SSE_RETRY_MS` | `3000` | No | `retry:` hint sent to SSE clients. |
| `GOVERNANCE_STREAM_INTERVAL_SECONDS` | `5.0` (`>0`) | No | Governance SSE producer tick. |
| `GOVERNANCE_QUOTA_SAMPLE_SECONDS` | `60.0` (`>0`) | No | Quota sampling interval. |
| `GOVERNANCE_QUOTA_SERIES_POINTS` | `60` (`>=1`) | No | Points kept in the quota series. |
| `GOVERNANCE_EXACT_COUNT_CAP` | `100000` (`>=1`) | No | Above this, list totals become planner estimates. |
| `GOVERNANCE_EXPORT_MAX_ROWS` | `100000` (`>=1`) | No | Governance CSV export cap. |
| `GOVERNANCE_LATENCY_BUCKETS_MS` | `[1000,3000,10000]` | No | Fast/moderate/slow trace bucket bounds. |
| `GOVERNANCE_HEALTH_WINDOW_MINUTES` | `60` (`>=1`) | No | Health status window. |
| `GOVERNANCE_HEALTH_ERROR_RATE_DEGRADED_PCT` | `2.0` | No | Error rate for Degraded. |
| `GOVERNANCE_HEALTH_ERROR_RATE_INCIDENT_PCT` | `10.0` | No | Error rate for Incident. |
| `GOVERNANCE_HEALTH_P95_DEGRADED_MS` | `8000.0` | No | p95 latency for Degraded. |
| `GOVERNANCE_HEALTH_P95_INCIDENT_MS` | `20000.0` | No | p95 latency for Incident. |
| `GOVERNANCE_HEALTH_QUOTA_DEGRADED_PCT` | `80.0` | No | Quota use for Degraded. |
| `GOVERNANCE_HEALTH_QUOTA_INCIDENT_PCT` | `95.0` | No | Quota use for Incident. |
| `GOVERNANCE_INCIDENTS_WINDOW_HOURS` | `24` (`>=1`) | No | Incident list window. |
| `GOVERNANCE_INCIDENTS_LIMIT` | `10` (`1..100`) | No | Incidents returned. |

## Backend: feedback and insights

| Name | Default | Required | Meaning |
|---|---|---|---|
| `FEEDBACK_TREND_DAYS` | `30` (`1..365`) | No | Feedback trend window. |
| `FEEDBACK_GAP_CONFIDENCE_THRESHOLD` | `0.5` (`0..1`) | No | Retrieval confidence below which a low-rated turn is a knowledge gap. |
| `FEEDBACK_GAP_LIMIT` | `10` (`1..100`) | No | Knowledge gaps returned. |
| `FEEDBACK_EXPORT_BATCH_SIZE` | `500` (`10..5000`) | No | Rows per batch in feedback export. |
| `INSIGHTS_BATCH_SIZE` | `10` (`1..50`) | No | Turns per classifier LLM call. |
| `INSIGHTS_DAILY_TOKEN_BUDGET` | `200000` (`>=0`) | No | Classifier tokens per local day; over budget jobs are deferred. |
| `INSIGHTS_TIMEZONE` | `Australia/Sydney` | No | IANA zone for day boundaries. |
| `INSIGHTS_USER_MAX_CHARS` | `1000` | No | User text truncation after PII masking. |
| `INSIGHTS_ANSWER_MAX_CHARS` | `600` | No | Answer text truncation after PII masking. |
| `INSIGHTS_MODEL` | `""` | No | Classifier deployment; empty = `AZURE_DEPLOYMENT_NAME`. |
| `INSIGHTS_REASONING_EFFORT` | `minimal` | No | Classifier reasoning effort. |
| `INSIGHTS_COMPLETION_TOKENS_PER_TURN` | `120` | No | Completion token allowance per turn. |

## Backend: ingestion

| Name | Default | Required | Meaning |
|---|---|---|---|
| `INGEST_MAX_FILE_BYTES` | `10485760` | No | Max upload size (10 MB). The Next proxy caps bodies at 15 MB and nginx at `client_max_body_size 15m`. |
| `INGEST_CONVERT_CONCURRENCY` | `4` | No | Concurrent DOCX conversions. Not in `.env.example`. |
| `INGEST_MAX_CHARS` | `500000` | No | Converted markdown truncated beyond this. Not in `.env.example`. |

## Backend: observability and HTTP

| Name | Default | Required | Meaning |
|---|---|---|---|
| `LOG_LEVEL` | `info` | No | See [core](#backend-core-and-database). |
| `GZIP_MINIMUM_SIZE` | `1024` | No | Minimum body size for GZip (SSE excluded). |
| `HTTP_CACHE_MAX_AGE_SECONDS` | `15` | No | `Cache-Control: private, max-age` on ETag-cached GETs (`app/core/http_cache.py`). |

There are no OpenTelemetry/Sentry variables: no such integration exists in the code (verified by grep).

## Backend: testing

| Name | Default | Required | Meaning |
|---|---|---|---|
| `TEST_DATABASE_URL` | `None` | For DB tests | Database used by `tests/conftest.py` instead of `DATABASE_URL`. Role needs `CREATEDB`. |
| `TEST_DB_SUFFIX` | `""` | No | Appended as `_<suffix>` to the test DB name for parallel workers. Set per command. |

CI (`.github/workflows/ci.yml`) injects test-only values (e.g. `SESSION_SECRET: ci-only-session-secret-...`,
`AUTH_DEV_MOCK=true`, `ENV=development`); none are real secrets.

## Backend: variables outside Settings

| Name | Read by | Default | Meaning |
|---|---|---|---|
| `WEB_CONCURRENCY` | `backend/Dockerfile.prod` `CMD` | `1` | Uvicorn `--workers`. |
| `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`, `PIP_NO_CACHE_DIR` | Dockerfiles | `1` | Image build/runtime hygiene. |
| `SMOKE_FRONTEND_URL`, `SMOKE_BACKEND_URL`, `SMOKE_SIGNIN_EMAIL` | `backend/scripts/smoke_e2e.py` | script constants / `""` | Smoke test targets. |
| `API_KEY_NAME` | `backend/scripts/run_admin_load_test.py` | `""` | Fallback for `--api-key`. |
| `SUPERADMIN_EMAILS` | `backend/scripts/smoke_journeys.py` | `""` | Picks the sign-in identity. |

## Frontend

All server-only; read per request, so changing them needs a restart, not a rebuild.

| Name | Default | Required | Meaning |
|---|---|---|---|
| `API_BASE_URL` | `http://127.0.0.1:8000` | Yes outside local dev | FastAPI base URL (no trailing slash). Read by `lib/server/backend.ts`, `lib/brand-server.ts`, `app/branding/assets/[sha]/route.ts`. Compose sets `http://backend:8000`. |
| `API_KEY` | none | Yes | Sent as `X-API-Key`; must equal backend `API_KEY_NAME`. Omitted header when unset (backend then answers 401). |
| `APP_BASE_URL` | request origin | Yes in prod | Public origin; redirects and `Origin` of server-originated state changes. Must equal backend `APP_BASE_URL`. |
| `SESSION_COOKIE_NAME` | `askdiq_session` | No | Must match backend; ignored unless it matches `^[A-Za-z0-9_-]{1,64}$`. |
| `SESSION_COOKIE_SECURE` | `false` (anything but `"true"`) | No (`true` in prod) | Adds `Secure` to the session and sign-in cookies. |
| `ACCESS_CONTACT_EMAIL` | `""` | No | Shown on login/suspended pages before a session exists. |
| `AUTH_DEV_MOCK` | `false` | No | `"true"` enables forwarding `/mock-idp/*` to the backend mock IdP. Never true in production. |
| `NODE_ENV` | set by Next | n/a | `production` adds HSTS (`next.config.js`); `development` relaxes the CSP (`'unsafe-eval'`, `ws:`). |
| `NEXT_TELEMETRY_DISABLED` | `1` in Dockerfile | n/a | Disables Next telemetry. |
| `AUDIT_ALLOWLIST` | `.github/audit/npm-audit-allowlist.json` | No | Override path for `frontend/scripts/audit-gate.mjs` (CI only). |

`frontend/next.config.js` itself has no tunable env other than `NODE_ENV`: `output: "standalone"`,
`poweredByHeader: false`, `optimizePackageImports: ["lucide-react","recharts"]`, static security headers
and the `/admin/evaluation` and `/evaluation` redirects to `/admin/evaluation-live`.

## Compose files and Dockerfiles

| File | Variable | Value | Note |
|---|---|---|---|
| `docker-compose.yml` (local) | `POSTGRES_PASSWORD` | `${POSTGRES_PASSWORD:-12345}` | Local pgvector container only. Host port 5433. |
| `infra/docker-compose.yml` | backend `DATABASE_URL` | `postgresql+asyncpg://postgres:${POSTGRES_PASSWORD:-12345}@postgres:5432/ragdb` | Overrides `backend/.env`. |
| `infra/docker-compose.yml` | frontend `API_BASE_URL` | `http://backend:8000` | Overrides `frontend/.env.local`. |
| `infra/scale-out/docker-compose.yml` (api-1, api-2) | `WEB_CONCURRENCY` / `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `2` / `10` / `5` (shell overridable) | Plus `JOBS_WORKER_ENABLED: "false"`. |
| `infra/scale-out/docker-compose.yml` (worker) | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `${WORKER_DB_POOL_SIZE:-5}` / `${WORKER_DB_MAX_OVERFLOW:-5}` | Dedicated job worker. |
| `frontend/Dockerfile` | `NODE_ENV`, `NEXT_TELEMETRY_DISABLED` | `production`, `1` | |

Prod reality differs from `infra/scale-out/docker-compose.yml`: production runs a single `askdiq-api` container on
host port `127.0.0.1:5500` with env only from `/opt/askdiq/backend/.env`, and the repo compose file has a
placeholder `NETWORK_NAME`. Never ship it (see [Deployment](08-deployment-operations.md)).

## Production (main and hotfix) differences

Production runs `main` plus `hotfix/query-filters-ip-ingest` (branched from `main` at `1a88798`), not
`preview/view`. Its `Settings` has 72 fields on `main` and 74 with the hotfix:

| Difference | `main` / hotfix | `preview/view` |
|---|---|---|
| `API_JWT_SECRET` | Required (no default) but not used by any code; must still be set or startup fails. | Removed. |
| `API_JWT_ALGORITHM` | `HS256`, unused. | Removed. |
| `RETRIEVAL_FILTER_KEYS`, `ALLOW_CLIENT_IP_OVERRIDE` | Hotfix only (see above). | Absent. |
| SSO, sessions, RBAC, security headers, CORS lists, shared state, jobs, governance, feedback, insights, branding runtime, circuit breaker (98 settings) | Absent. | Present. |
| Docs | Disabled whenever `ENV=production` (no opt-in). | `ENABLE_API_DOCS` opt-in. |
| `backend/Dockerfile.prod` | `uvicorn main:app` single worker. | `--workers ${WEB_CONCURRENCY:-1}`. |

Unverified: the exact production `.env` contents (never read). The 2026-10-16 incident showed that a
dev-derived `.env` (`DATABASE_URL` host `localhost`) crash-loops the container; restore from the server
backup, never from a package.

## In-flight configuration

| Variable | Default | Meaning | Status |
|---|---|---|---|
| `DB_POOL_TIMEOUT_SECONDS` | `120.0` (`>0`) | Last-resort wait for a pooled connection; bursts queue instead of failing. | **In progress (branch `fix/chat-page-fetch-timeouts`)**, uncommitted |
| `BRANDING_LOAD_TIMEOUT_SECONDS` | `5.0` (`>0`) | Upper bound on one branding DB read; config defaults served on timeout. | **In progress (branch `fix/chat-page-fetch-timeouts`)** |
| `AGG_POOL_RESERVE` | `5` (`>=0`) | Pool connections parallel aggregation may never take, so sign-in, `/health` and plain reads always find one. | **In progress (branch `fix/chat-page-fetch-timeouts`)** |
| `GOVERNANCE_STREAM_SNAPSHOT_TIMEOUT_SECONDS` | `60.0` (`>0`) | Upper bound on one governance SSE producer snapshot. | **In progress (branch `fix/chat-page-fetch-timeouts`)** |
| `BACKEND_TIMEOUT_MS` (frontend) | `60000` | Proxy wait for backend response headers on reads before 504. | **In progress (branch `fix/chat-page-fetch-timeouts`)** |
| `BACKEND_WRITE_TIMEOUT_MS` (frontend) | `180000` | Same for writes (chat answers, uploads). SSE never cut once headers arrive. | **In progress (branch `fix/chat-page-fetch-timeouts`)** |
| `BACKEND_RETRIES` (frontend) | `2` (max 5, `0` disables) | Jittered retries of reads and sign-out after a connection error or 502/503/504. | **In progress (branch `fix/chat-page-fetch-timeouts`)** |
| `RUN_MIGRATIONS` | `true` | `backend/scripts/prestart.py`: `alembic upgrade head` under an advisory lock before uvicorn; failure stops the container. | **In progress (branch `chore/prod-deploy-fullstack`)** |
| `RUN_INGESTION` | `true` | Knowledge-base ingestion under a try-lock (non-fatal). | **In progress (branch `chore/prod-deploy-fullstack`)** |
| `RUN_USAGE_BACKFILL` | `false` | One-off `backfill_usage_accounting.py --apply` (non-fatal). | **In progress (branch `chore/prod-deploy-fullstack`)** |
| `DB_WAIT_RETRIES` / `DB_WAIT_INTERVAL_SECONDS` / `DB_CONNECT_TIMEOUT_SECONDS` | `30` / `2` / `20` | Wait-for-Postgres loop in prestart. | **In progress (branch `chore/prod-deploy-fullstack`)** |
| `MIGRATION_LOCK_TIMEOUT_SECONDS` | `600` | Wait for the migration advisory lock. | **In progress (branch `chore/prod-deploy-fullstack`)** |
| `INGESTION_TIMEOUT_SECONDS` / `BACKFILL_TIMEOUT_SECONDS` | `900` / `1800` | Step timeouts in prestart. | **In progress (branch `chore/prod-deploy-fullstack`)** |
| `infra/frontend.env.prod.example` | n/a | Prod frontend env template: `API_BASE_URL=http://askdiq-api:8000`, `APP_BASE_URL=https://askdiqui.ethub.co`, `SESSION_COOKIE_SECURE=true`, `AUTH_DEV_MOCK=false`. Placed at `/opt/askdiq/frontend/.env`. No `NEXT_PUBLIC_*` used. Step by step: [environment-and-sso.md](../deployment/environment-and-sso.md). | Merged |
| `next.config.js` `optimizePackageImports` | adds `motion` | Bundle optimisation only; no new env. | **In progress (branch `perf/dashboard-load-speed`)** |

The prestart toggles are read with `os.environ` by `backend/scripts/prestart.py` (run by
`backend/docker-entrypoint.sh` only for uvicorn commands), not by `Settings`. The prod compose example
sets the `RUN_*` flags to `false` on the optional worker. `perf/dashboard-load-speed`,
`feat/realistic-demo-data`, `feat/askdiq-chat-widget` and `fix/shell-nav-logout-theme-dates` add no
settings (verified by diffing `config.py`, `.env.example`, `.env.local.example` and `process.env` reads).

## Drift between code and .env.example

| Item | Detail |
|---|---|
| In `.env.example`, not a setting | `AZURE_OPENAI_CREATIVE_TEMPERATURE` (ignored). |
| Settings missing from `.env.example` | `ANSWER_MAX_WORDS`, `AZURE_OPENAI_EMBEDDING_RPM_LIMIT`, `AZURE_OPENAI_EMBEDDING_TPM_LIMIT`, `AZURE_OPENAI_MAX_RETRIES`, `COMPOUND_QUERY_TOP_K`, `CROSS_DOC_QUOTA_MAX_DOCUMENTS`, `DB_ECHO`, `ENABLE_QUERY_DECOMPOSITION`, `ENABLE_RERANKER`, `INGEST_CONVERT_CONCURRENCY`, `INGEST_MAX_CHARS`, `LATENCY_BUDGET_MS`, `LLM_CONTEXT_MAX_TOKENS`, `LLM_HISTORY_MAX_TOKENS`, `LLM_MAX_COMPLETION_TOKENS`, `LLM_REASONING_EFFORT`, `MAX_LLM_RETRIES`, `MIN_RETRIEVAL_SCORE`, `PII_ALLOWLIST`, `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_PER_MINUTE_PER_IP`, `RERANKER_MODEL`, `RERANK_BATCH_SIZE`, `RERANK_MAX_CONCURRENCY`, `RETRIEVAL_CANDIDATE_MIN`, `RETRIEVAL_CANDIDATE_MULTIPLIER`, `SESSION_HISTORY_MAX_MESSAGES`, `SESSION_TIMEOUT_MINUTES`, `THREAD_POOL_WORKERS`. |
| Value differs from code default | `MONTHLY_COST_BUDGET_USD` (example `100`, code unset); hotfix `ALLOW_CLIENT_IP_OVERRIDE` (example `false`, code `true`). |
| Settings no code reads | `AZURE_OPENAI_TEMPERATURE`, `LLM_MAX_COMPLETION_TOKENS`, `INGEST_SERVER_CONVERSION`, `ONLINE_EVAL_SAMPLE_RATE`, `WEB_CONCURRENCY` (read only by the Dockerfile shell). |
