# API reference

Scope: every HTTP route of the FastAPI backend (`backend/main.py`) on `preview/view` (9115c45): method, path,
guard, inputs, outputs, status codes, caching and rate limits, plus the different contract that is live in
production (`hotfix/query-filters-ip-ingest`) and route changes in other in-flight branches. Generated from
`app.openapi()` and `app.routes` (CI env vars, `AUTH_DEV_MOCK=true`) and cross-checked against
`backend/app/api/routes/*.py`, `backend/app/auth/deps.py`, `backend/app/auth/permissions.py`,
`backend/tests/test_route_guards.py` and `backend/app/schemas/*.py`.

Contents

- [1. Conventions](#1-conventions)
- [2. Guards, roles and permissions](#2-guards-roles-and-permissions)
- [3. Error envelope (problem+json)](#3-error-envelope-problemjson)
- [4. Caching and conditional GET](#4-caching-and-conditional-get)
- [5. Rate limits and back-pressure](#5-rate-limits-and-back-pressure)
- [6. Route summary](#6-route-summary)
- [7. Health and docs](#7-health-and-docs)
- [8. Query and chat sessions](#8-query-and-chat-sessions)
- [9. Ingest and documents](#9-ingest-and-documents)
- [10. Metrics and audit log](#10-metrics-and-audit-log)
- [11. Admin: dashboard, users, conversations, visitors](#11-admin-dashboard-users-conversations-visitors)
- [12. Admin: usage and cost](#12-admin-usage-and-cost)
- [13. Admin: feedback (RLHF loop)](#13-admin-feedback-rlhf-loop)
- [14. Admin: governance](#14-admin-governance)
- [15. Admin: insights and taxonomy](#15-admin-insights-and-taxonomy)
- [16. Branding](#16-branding)
- [17. Auth (SSO, session, access requests)](#17-auth-sso-session-access-requests)
- [18. User management](#18-user-management)
- [19. Mock IdP (development only)](#19-mock-idp-development-only)
- [20. Schema reference](#20-schema-reference)
- [21. Production contract (hotfix branch)](#21-production-contract-hotfix-branch)
- [22. In-flight route and contract changes](#22-in-flight-route-and-contract-changes)
- [23. Known gaps and unverified items](#23-known-gaps-and-unverified-items)

---

## 1. Conventions

| Item | Value |
|---|---|
| Base URL | No global prefix (no `/api/v1`). Routes are mounted at the root, e.g. `POST /query`. |
| Intended caller | The Next.js proxy (`frontend/app/api/proxy/[...path]/route.ts`); the browser never calls the backend directly. See [Security](07-security.md). |
| OpenAPI | `/docs`, `/redoc`, `/openapi.json` outside production; in production only when `ENABLE_API_DOCS=true` (`main.py` `api_docs_enabled`). |
| Title / version | `"{BRAND_NAME} RAG Backend"`, `0.1.0` |
| Content type | `application/json` unless stated; errors are `application/problem+json`. |
| Timestamps | Most fields are ISO-8601. DB columns are naive UTC; several admin routes append `Z`, others do not (see each route). |
| Request id | `LatencyMiddleware` (`backend/app/core/middleware.py`) generates a UUID4 per request (an incoming `X-Request-ID` is not reused) and returns it as `X-Request-ID`, plus `X-Latency-MS`. The same id is `request_id` in `QueryResponse` and in every error body. |
| Compression | `SSEAwareGZipMiddleware` gzips responses >= `GZIP_MINIMUM_SIZE` (1024) bytes, never `text/event-stream`. |
| Security headers | `SecurityHeadersMiddleware` on every response (incl. errors). See [Security](07-security.md). |
| CORS | `CORS_ALLOWED_ORIGINS`; methods `GET, POST, PATCH, DELETE, OPTIONS`; headers `Content-Type, X-API-Key, X-Request-ID, X-Client-Id, If-None-Match` (`backend/app/core/config.py`). `PUT` and `X-Session` are not in the allow-list; irrelevant for the server-side proxy, but a direct browser call to `PUT /admin/branding` would fail preflight. |
| Pagination styles | `limit`/`offset` (most admin lists), opaque keyset `cursor` (`/admin/feedback`, `/admin/governance/traces/page`, `/admin/governance/logs`). |
| Audience | Many admin reads take `audience=overview|users|testing` (`app/governance/audience.py`); anything else is 422 `Invalid audience '<x>'. Must be one of: overview, users, testing.`. `users` excludes test IPs, `testing` keeps only test IPs (`DEV_TEST_IPS` + `/admin/usage/test-ips`). |

### Request headers sent by the proxy

| Header | When | Purpose |
|---|---|---|
| `X-API-Key` | Every non-public request, incl. `/auth/sso/*` | Service key; must equal `API_KEY_NAME` (constant-time compare). |
| `X-Session` | Every session-guarded request | Raw session id from the httpOnly cookie. |
| `X-Requested-With: askdiq` + `Origin` | POST/PUT/PATCH/DELETE with a session | CSRF defence; `Origin` must equal the origin of `APP_BASE_URL`. |
| `X-Forwarded-For` | Always (proxy) | Client IP; honoured only when `TRUST_PROXY_HEADERS=true` (first hop). |
| `If-None-Match` | Optional on cached GETs | ETag revalidation (304). |

## 2. Guards, roles and permissions

Dependencies in `backend/app/auth/deps.py` (deny by default; `tests/test_route_guards.py` fails the build if a
non-public route has no guard).

| Guard (label used below) | Dependency | Requires | Failure |
|---|---|---|---|
| public | none | nothing | - |
| service key | `require_service_key` | `X-API-Key` | 401 `Access Denied!` |
| signed-in | `current_user` | service key + valid `X-Session` (+ CSRF on writes); any status incl. suspended | 401 `Not authenticated.`; 403 `Cross-site request rejected.` |
| chat | `require_role_or_chat` | signed-in and status in `active, pending, rejected` | 403 `Account suspended.` |
| chat-or-key | `require_chat_actor` | service key, then either a session as **chat** (actor = email) or, with no `X-Session` and no `X-AskDIQ-Proxy` marker, the key alone (actor = sanitised `X-Client-Id`) | 401 `Access Denied!` / `Not authenticated.`; 403 as **chat** |
| perm `<p>` | `require_permission(p)` | signed-in, status `active`, role grants `p` | 403 `Account suspended.` / `Dashboard access requires approval.` / `Insufficient permissions.` |

The user row is reloaded on every request, so role/status changes apply to the next call.

Role to permission table (`backend/app/auth/permissions.py`):

| Permission | operator | admin | superadmin |
|---|---|---|---|
| `dashboard:read` | yes | yes | yes |
| `documents:write`, `feedback:triage`, `feedback:export`, `eval:run`, `testip:write`, `usage:export` | - | yes | yes |
| `users:manage`, `audit:auth:read`, `branding:write`, `taxonomy:write` | - | - | yes |

`eval:run` is defined but no route requires it. `GET /auth/me` returns the caller's permission list so the UI
and API agree.

## 3. Error envelope (problem+json)

All errors go through `backend/app/core/exceptions.py` (RFC 9457), media type `application/problem+json`, header
`X-Request-ID`:

```json
{
  "type": "about:blank",
  "title": "Not Found",
  "status": 404,
  "detail": "Document not found",
  "request_id": "6f1c0d0e-4f0a-4d0e-9c43-2a7f0b1d1e55"
}
```

| Case | `type` | `detail` | Extra |
|---|---|---|---|
| `HTTPException` (any route) | `about:blank` | the exception's detail (string, or object for a few routes) | `Retry-After` header kept for 429 |
| Request validation (422) | `urn:askdiq:problem:validation-error` | `Request validation failed.` | `errors: [{loc: [..], msg, type}]` (submitted values are never echoed; messages PII-masked) |
| `DomainError` / `NotFoundError` (404) / `ConflictError` (409) / `ForbiddenError` (403) | `about:blank` | service message | optional extra fields from the service |
| Unhandled exception (500) | `urn:askdiq:problem:internal-error` | `An unexpected error occurred. Quote the request_id when contacting support.` | audited as `server_error`; security headers applied |
| 204 / 304 | - | no body | headers kept (ETag) |

Note: the OpenAPI document still lists FastAPI's default `HTTPValidationError` schema for many 422 responses;
the real body is the problem+json shape above.

Common statuses: 401 (no/wrong key or session), 403 (CSRF, suspended, pending, missing permission), 404, 409
(version conflict, invalid transition, duplicate), 413/415 (uploads), 422 (validation or semantic checks), 429
(rate limit / LLM capacity), 502 (IdP), 503 (health degraded, dashboard statement timeout).

## 4. Caching and conditional GET

`cached_response()` in `backend/app/core/http_cache.py`: weak ETag `W/"<first 32 hex of sha256(body)>"`;
`If-None-Match` match (or `*`) returns 304 with no body.

| Mode | Headers |
|---|---|
| private (default) | `Cache-Control: private, max-age=HTTP_CACHE_MAX_AGE_SECONDS` (0), `Vary: X-API-Key, X-Client-Id` |
| public | `Cache-Control: public, max-age=<n>` |

| Route | Mode / max-age |
|---|---|
| `GET /branding` | public, `BRANDING_HTTP_MAX_AGE_SECONDS` (300) |
| `GET /branding/assets/{sha}` | strong ETag `"<sha>"`, `public, max-age=31536000, immutable` |
| `GET /auth/me` | private, `max-age=0`, `Vary: X-API-Key, X-Session` |
| `GET /admin/dashboard`, `/admin/dashboard/activity`, `/admin/usage/summary`, `/admin/feedback/analytics`, `/admin/governance/overview`, `/admin/visitors/stats`, `/admin/conversations/stats`, `/documents/stats/citations`, `/admin/insights/summary` | private, 15 s |

`Vary` still names `X-Client-Id` (legacy header) and not `X-Session`; only `/auth/me` overrides it. Per-user
data stays out of shared caches because of `private`.

Server-side caches (not HTTP): `/admin/usage/summary` and `/admin/usage/audience-counts` keep an in-process
result for `USAGE_SUMMARY_CACHE_TTL_SECONDS` (15 s), cleared when test IPs change; `/branding` has an in-process
TTL cache (`BRANDING_CACHE_TTL_SECONDS`, 60). Streams and exports send `Cache-Control: no-store`.

## 5. Rate limits and back-pressure

Implemented in `backend/app/core/rate_limit.py` (60 s window, Postgres-backed shared state by default, in-memory
with `ENV=test`/`STATE_BACKEND=memory`). Over the limit: 429 with `Retry-After`, audited as `rate_limited`.

| Scope | Routes | Limit | Key |
|---|---|---|---|
| per user | `POST /query` | `RATE_LIMIT_PER_MINUTE` (10) | user email; actors in `RATE_LIMIT_EXEMPT_ACTORS` skip it |
| per IP | `POST /query` | `RATE_LIMIT_PER_MINUTE_PER_IP` (30) | `get_client_ip()` |
| feedback | `POST /sessions/{id}/messages/{mid}/feedback` | 30 per user and 30 per IP | email, IP |
| sign-in | `GET /auth/sso/login`, `POST /auth/sso/callback` | `AUTH_LOGIN_RATE_LIMIT_PER_MINUTE` (10) | client IP; detail `Too many sign-in attempts. Try again shortly.` |
| LLM capacity | `POST /query` (`llm_capacity`, `app/core/concurrency.py`) | `LLM_MAX_CONCURRENCY` (64) in-flight per process | 429 `The assistant is at capacity. Please retry shortly.`, `Retry-After: LLM_SATURATED_RETRY_AFTER_SECONDS` (2) |

The 429 detail text is `Rate limit exceeded (<scope>): max <n> questions per minute. Try again in <s>s.`
`rate_limited_admin_analytics` (60/min per user and IP for heavy admin routes) exists but is not attached to any
route on `preview/view`.

Insights and other `read_only_session` routes run under `SET LOCAL statement_timeout =
DASHBOARD_STATEMENT_TIMEOUT_MS` (5000); a timeout becomes 503.

## 6. Route summary

Guard column: see [section 2](#2-guards-roles-and-permissions). 99 API routes plus 5 dev-only mock IdP routes and the 4 docs routes.

| Method | Path | Guard | Summary |
|---|---|---|---|
| GET | `/health` | public | Readiness probe (DB + reranker) |
| POST | `/query` | chat + rate limits | Ask a question (RAG) |
| GET | `/sessions` | chat | List my chat sessions |
| POST | `/sessions` | chat | Create a session |
| GET | `/sessions/{session_id}` | chat | Session with messages |
| PATCH | `/sessions/{session_id}` | chat | Rename |
| DELETE | `/sessions/{session_id}` | chat | Delete session |
| DELETE | `/sessions/{session_id}/messages/{message_id}` | chat | Delete one exchange |
| POST | `/sessions/{session_id}/messages/{message_id}/feedback` | chat + feedback limit | Rate an answer |
| POST | `/ingest` | perm `documents:write` | Ingest raw text (JSON) |
| POST | `/ingest/file` | perm `documents:write` | Ingest `.md`/`.docx` upload |
| GET | `/documents` | perm `dashboard:read` | Document inventory |
| GET | `/documents/stats/citations` | perm `dashboard:read` | Citation stats (ETag) |
| GET | `/documents/{document_id}` | perm `dashboard:read` | Document detail |
| GET | `/documents/{document_id}/canonical` | perm `dashboard:read` | Canonical Markdown |
| DELETE | `/documents/{document_id}` | perm `documents:write` | Delete document + chunks |
| GET | `/documents/{document_id}/chunks` | perm `dashboard:read` | Chunks with hits |
| GET | `/documents/{document_id}/low-rated-citations` | perm `dashboard:read` | Low-rated answers citing it |
| GET | `/metrics/latency` | perm `dashboard:read` | Latency percentiles |
| GET | `/metrics/evaluation` | perm `dashboard:read` | Offline/live evaluation report |
| GET | `/metrics/evaluation/live` | perm `dashboard:read` | Live vs benchmark evaluation |
| GET | `/metrics/evaluation/record/{record_id}` | perm `dashboard:read` | One evaluation record |
| GET | `/governance/audit-log` | perm `dashboard:read` | Raw audit log |
| GET | `/admin/dashboard` | perm `dashboard:read` | Dashboard KPIs and series (ETag) |
| GET | `/admin/dashboard/activity` | perm `dashboard:read` | Live activity feed (ETag) |
| GET | `/admin/users` | perm `dashboard:read` | Visitors (by IP) list |
| GET | `/admin/users/{user_id}` | perm `dashboard:read` | Visitor / user detail |
| GET | `/admin/conversations` | perm `dashboard:read` | Conversations list |
| GET | `/admin/conversations/stats` | perm `dashboard:read` | Conversation stats (ETag) |
| GET | `/admin/conversations/{conversation_id}` | perm `dashboard:read` | Conversation with turns |
| GET | `/admin/visitors/stats` | perm `dashboard:read` | Visitor KPIs and heatmap (ETag) |
| GET | `/admin/visitors/{visitor_id}/timeline` | perm `dashboard:read` | One visitor's timeline |
| GET | `/admin/usage/audience-counts` | perm `dashboard:read` | Audience badge counts |
| GET | `/admin/usage/summary` | perm `dashboard:read` | Stage economics summary (ETag) |
| GET | `/admin/usage/users` | perm `dashboard:read` | Usage by client IP |
| GET | `/admin/usage/sessions` | perm `dashboard:read` | Usage by session |
| GET | `/admin/usage/queries` | perm `dashboard:read` | Usage by turn |
| GET | `/admin/usage/queries/{request_id}` | perm `dashboard:read` | Turn inspector (see duplicate note) |
| GET | `/admin/usage/export` | perm `usage:export` | CSV export (streamed) |
| GET | `/admin/usage/llm` | perm `dashboard:read` | LLM usage card |
| GET | `/admin/usage/embedding` | perm `dashboard:read` | Embedding usage card |
| GET | `/admin/usage/reranker` | perm `dashboard:read` | Reranker usage card |
| GET | `/admin/usage/metadata` | perm `dashboard:read` | Model/config metadata |
| GET | `/admin/usage/test-ips` | perm `dashboard:read` | Test IP configuration |
| POST | `/admin/usage/test-ips` | perm `testip:write` | Add/update test IP |
| DELETE | `/admin/usage/test-ips/{ip_address:path}` | perm `testip:write` | Remove test IP |
| GET | `/admin/usage/cost-breakdown` | perm `dashboard:read` | Four-level cost breakdown |
| GET | `/admin/feedback` | perm `dashboard:read` | Feedback review queue |
| GET | `/admin/feedback/analytics` | perm `dashboard:read` | Feedback analytics (ETag) |
| GET | `/admin/feedback/golden` | perm `dashboard:read` | Golden cases |
| GET | `/admin/feedback/export` | perm `feedback:export` | Preference pairs JSONL (streamed) |
| POST | `/admin/feedback/golden/{case_id}/run` | perm `feedback:triage` | Queue golden-case run |
| POST | `/admin/feedback/bulk` | perm `feedback:triage` | Bulk triage |
| PATCH | `/admin/feedback/{feedback_id}` | perm `feedback:triage` | Triage one row |
| POST | `/admin/feedback/{message_id}/promote-to-test` | perm `feedback:triage` | Promote to golden case |
| GET | `/admin/governance/monitoring` | perm `dashboard:read` | 24 h monitoring snapshot |
| GET | `/admin/governance/traces` | perm `dashboard:read` | Traces (legacy array) |
| GET | `/admin/governance/traces/page` | perm `dashboard:read` | Traces (keyset page) |
| GET | `/admin/governance/traces/{request_id}` | perm `dashboard:read` | Trace detail |
| GET | `/admin/governance/logs` | perm `dashboard:read` | Governance logs (keyset page) |
| GET | `/admin/governance/logs/export.csv` | perm `dashboard:read` | Logs CSV (streamed) |
| GET | `/admin/governance/overview` | perm `dashboard:read` | Overview (ETag) |
| GET | `/admin/governance/stream` | perm `dashboard:read` | Live updates (SSE) |
| GET | `/admin/insights/summary` | perm `dashboard:read` | KPI strip (ETag) |
| GET | `/admin/insights/trend` | perm `dashboard:read` | Daily trend |
| GET | `/admin/insights/hourly` | perm `dashboard:read` | By hour of day |
| GET | `/admin/insights/flow` | perm `dashboard:read` | User type to outcome flow |
| GET | `/admin/insights/topics` | perm `dashboard:read` | Per-intent questions |
| GET | `/admin/insights/quality` | perm `dashboard:read` | Answer quality mix |
| GET | `/admin/insights/dropoff` | perm `dashboard:read` | Drop-off issues |
| GET | `/admin/insights/people` | perm `dashboard:read` | Per pseudonymous person |
| GET | `/admin/insights/conversations` | perm `dashboard:read` | Conversations table |
| GET | `/admin/insights/conversations/{conv_id}` | perm `dashboard:read` | Conversation turns |
| GET | `/admin/insights/pending` | perm `dashboard:read` | Classification progress |
| GET | `/admin/insights/taxonomy` | perm `dashboard:read` | List taxonomy |
| POST | `/admin/insights/taxonomy` | perm `taxonomy:write` | Create entry |
| PATCH | `/admin/insights/taxonomy/{kind}/{code}` | perm `taxonomy:write` | Edit entry |
| POST | `/admin/insights/taxonomy/{kind}/{code}/archive` | perm `taxonomy:write` | Archive entry |
| GET | `/branding` | public | Resolved branding (ETag, public) |
| GET | `/branding/assets/{sha}` | public | Brand image bytes |
| PUT | `/admin/branding` | perm `branding:write` | Update branding |
| POST | `/admin/branding/assets` | perm `branding:write` | Upload brand asset |
| GET | `/auth/sso/login` | service key + login limit | Start SSO |
| POST | `/auth/sso/callback` | service key + login limit | Complete SSO, issue session |
| GET | `/auth/me` | signed-in | Current user and permissions |
| POST | `/auth/logout` | signed-in | Revoke current session |
| GET | `/auth/access-request` | chat | My access status |
| POST | `/auth/access-request` | chat | Request dashboard access |
| GET | `/admin/users-accounts` | perm `users:manage` | List accounts |
| GET | `/admin/users-accounts/stats` | perm `users:manage` | User-management KPIs |
| POST | `/admin/users-accounts/invite` | perm `users:manage` | Invite by email |
| GET | `/admin/users-accounts/{user_id}` | perm `users:manage` | Account detail + sessions |
| PATCH | `/admin/users-accounts/{user_id}` | perm `users:manage` | Change role/status |
| POST | `/admin/users-accounts/{user_id}/revoke-sessions` | perm `users:manage` | Force sign-out |
| GET | `/admin/access-requests` | perm `users:manage` | Access request queue |
| POST | `/admin/access-requests/bulk-approve` | perm `users:manage` | Bulk approve |
| POST | `/admin/access-requests/{request_id}/approve` | perm `users:manage` | Approve |
| POST | `/admin/access-requests/{request_id}/reject` | perm `users:manage` | Reject |
| GET/POST | `/mock-idp/{tenant}/...` (5 routes) | public, only `AUTH_DEV_MOCK=true` | Local Entra stand-in |
| GET | `/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json` | public, not in production by default | API docs |

## 7. Health and docs

### GET /health

Public; `backend/app/api/routes/health.py`. Runs `SELECT 1`.

| Status | Body |
|---|---|
| 200 | `{"status": "ok", "database": "healthy", "reranker": "ready" \| "lazy_load", "timestamp": "<ISO>"}` |
| 503 | `{"status": "degraded", "database": "unreachable", "reranker": "ready" \| "not_loaded", "timestamp": ...}` |

The response is a plain dict (no response model). There is no separate liveness route and no Prometheus
`/metrics` endpoint; `/metrics/*` are dashboard reads ([section 10](#10-metrics-and-audit-log)).

## 8. Query and chat sessions

All routes in this section are **chat-or-key**-guarded: the dashboard calls them with an SSO session, the public
website server-to-server with `X-API-Key` + `X-Client-Id` (contract: [Website integration](10-website-integration.md)).
Sessions are scoped to the actor (email, or client id); another actor's session id gives 404.

### POST /query

`backend/app/api/routes/query.py`. Dependencies: `llm_capacity`, `rate_limited_actor` (chat guard + per-user +
per-IP limits).

Request `QueryRequest`:

| Field | Type | Constraints | Notes |
|---|---|---|---|
| `query` | string | 1..2000 chars | required |
| `filters` | object or null | none | Metadata filters passed straight to retrieval: each key/value becomes `chunk_metadata[key] == str(value)`. Any unknown key matches no chunk (empty answer). Fixed in the hotfix, see section 21. |
| `session_id` | string or null | - | Omit to resume the caller's latest non-expired session or start one. History is loaded server-side. |
| `ip_address` | string or null | - | Honoured only when `ENV != production`; otherwise the detected client IP is used. |

Response `QueryResponse` (all fields always present):

| Field | Type | Notes |
|---|---|---|
| `answer` | string | |
| `sources` | `SourceChunk[]` | `chunk_id`, `document_id`, `content`, `score`, `metadata` (embedding excluded) |
| `grounded` | bool | post-generation groundedness check |
| `confidence` | float | |
| `request_id` | string | same as `X-Request-ID` |
| `latency_ms` | float | |
| `guardrail_flags` | string[] | e.g. `off_topic`, injection/PII flags |
| `session_id`, `session_title` | string | session actually used (may rotate when expired) |
| `user_message_id`, `assistant_message_id` | string | persisted message ids |
| `ip_address` | string | IP the request was attributed to |
| `follow_up_detected` | bool | |
| `history_turns_sent` | int | <= 6 |
| `prompt_tokens`, `completion_tokens`, `total_tokens`, `reasoning_tokens` | int or null | Azure-reported usage; null when no LLM call |
| `embedding_tokens`, `guardrail_tokens` | int or null | query vs guardrail embedding tokens |
| `cost_usd` | float or null | computed by `compute_exchange_cost` |
| `outcome` | string | `answered`, `ungrounded`, `blocked`, `conversational`, `llm_failed` |
| `token_details` | object or null | cost breakdown + token details + `outcome` |

Errors: 401/403 (guard), 422 (validation), 429 (user/IP limit or LLM capacity). Side effects: exchange write,
audit event `query`, online evaluation scheduled (background).

### Sessions (`backend/app/api/routes/sessions.py`)

| Route | Body | Response | Errors |
|---|---|---|---|
| `GET /sessions` | - | `ChatSessionSummary[]` | |
| `POST /sessions` | `CreateSessionRequest {title?: string <= 256}` | 201 `ChatSessionSummary` | 422 |
| `GET /sessions/{session_id}` | - | `ChatSessionDetail` | 404 `Chat session not found` |
| `PATCH /sessions/{session_id}` | `RenameSessionRequest {title: string 1..256}` | `ChatSessionSummary` | 404, 422 |
| `DELETE /sessions/{session_id}` | - | 204 | 404 |
| `DELETE /sessions/{session_id}/messages/{message_id}` | - | 204 (deletes the user+assistant pair) | 404 `Message not found` |
| `POST /sessions/{session_id}/messages/{message_id}/feedback` | `UserFeedbackRequest` | 200 `UserFeedbackResponse` | 404 `Assistant message not found in this session`, 422, 429 |

`UserFeedbackRequest`: `rating` int 1..5 (required); `comment` <= 1000 chars, control chars stripped, blank ->
null; `reason_tags` list of `wrong|incomplete|off_topic|unsafe|too_slow|unclear` (deduped, max 6);
`corrected_answer` <= 4000 chars, PII-masked before storage. One row per (message, user); resubmitting updates
it. `UserFeedbackResponse`: `message_id`, `session_id`, `rating`, `comment`, `reason_tags`, `corrected_answer`,
`triage_status` (default `new`), `created_at`, `updated_at`.

## 9. Ingest and documents

### POST /ingest

Perm `documents:write`. Query: `dry_run` (bool, false), `force` (bool, false); either query or body flag set to
true applies.

`IngestRequest`: `source_name` string (required); `source_type` default `markdown`; `content` string (required);
`chunking_strategy` default `auto` (`fixed_size|semantic|structure_aware|metadata_aware|auto`); `metadata`
object; `force`, `dry_run` bools. No length limits on `content` at the schema level.

Response `IngestResponse`: `document_id`, `source_name`, `original_filename`, `converted_from`, `status`
(default `new`), `content_hash`, `previous_content_hash`, `chunk_count`, `chunks_added`, `chunks_kept`,
`chunks_removed`, `embedding_tokens`, `embedding_cost_usd`, `strategy_used`, `warnings[]`, `quarantined`,
`dry_run`, `quality`, `original_size_bytes`, `canonical_size_bytes`.

Errors: 422 (service `ValueError`), 500 `Ingestion failed. Reference Request ID: <id>`.

### POST /ingest/file

Multipart: `file` (required), form `chunking_strategy` (default `auto`), `force`, `dry_run`; `force` / `dry_run`
also accepted as query params (the dashboard uses query params).

| Status | Cause |
|---|---|
| 413 | file > `INGEST_MAX_FILE_BYTES` (10 MB) or DOCX part over a limit |
| 415 | not `.md` / `.markdown` / `.docx`; DOCX without ZIP signature or macro-enabled |
| 422 | invalid UTF-8 Markdown, malformed DOCX, service `ValueError` |
| 500 | ingestion failure (with request id) |

Response: `IngestResponse` (as above).

### Documents (`backend/app/api/routes/documents.py`)

| Route | Params | Response | Errors |
|---|---|---|---|
| `GET /documents` | - | `DocumentSummary[]`, newest first, no pagination | |
| `GET /documents/stats/citations` | `from`, `to` (date, inclusive, default last 7 days, span <= `USAGE_MAX_RANGE_DAYS` 400), `limit` 1..50 (10) | `DocumentCitationStats`, ETag | 422 `'to' must not be before 'from'.` / range |
| `GET /documents/{document_id}` | UUID | `DocumentDetail` | 404 |
| `GET /documents/{document_id}/canonical` | UUID | `CanonicalMarkdown` (`available=false` for pre-canonical documents) | 404 |
| `DELETE /documents/{document_id}` | UUID | 204; chunks + document + audit `document_delete` in one transaction | 404 |
| `GET /documents/{document_id}/chunks` | `q` <= 200 (text/heading, case-insensitive), `sort` `index|hits` | `DocumentChunkItem[]` (`hits` = last 8 ISO weeks) | 404, 422 |
| `GET /documents/{document_id}/low-rated-citations` | `limit` 1..100 (20) | `LowRatedCitations` (rating <= 2) | 404, 422 |

See [schema reference](#documents) for field lists.

## 10. Metrics and audit log

| Route | Params | Response |
|---|---|---|
| `GET /metrics/latency` | - | `LatencyReport`: `pipeline_ex_llm` (p50/p70/p100 + `within_budget_pct` vs `LATENCY_BUDGET_MS`), `end_to_end`, `llm`, `by_stage[]`. Loads every `latency_metrics` row. |
| `GET /metrics/evaluation` | `audience` (default `overview`; free string, not validated) | `users`: live telemetry built from chat rows. Otherwise the latest offline report JSON from `backend/reports/` with `cases[]`, `failed_cases[]`, `report_file`. 404 `No evaluation report found. Run scripts/run_evaluation.py --json reports/eval_report.json`. |
| `GET /metrics/evaluation/live` | `audience` (default `users`), `compare` (bool, unused in the code path) | Live or benchmark payload plus `comparison {deltas, dual_radar[]}` and run-series insight fields. |
| `GET /metrics/evaluation/record/{record_id}` | UUID or request id | One evaluation record (`faithfulness`, `relevance`, `completeness`, `citation_precision`, `citation_coverage`, `retrieval_recall`, `retrieved_chunks`, `judge_reason`, ...). 404 `Evaluation record not found`. |
| `GET /governance/audit-log` | `limit` 1..500 (100), `action` (exact), `category=auth` | `AuditLogEntry[]` newest first (`id`, `request_id`, `actor`, `action`, `detail`, `created_at`, `ip_address`). Auth events only for `audit:auth:read`. |

All are perm `dashboard:read`; the evaluation responses are untyped dicts (`object` in OpenAPI).

## 11. Admin: dashboard, users, conversations, visitors

### GET /admin/dashboard

Params: `audience`; `from`, `to` (ISO datetime, UTC when no offset; `to` exclusive). Without a window: legacy
KPIs (all time / last 24 h / since process start). With a window: totals for the window plus `window`,
`sparklines`, `deltas` (vs the previous equal window), `cost_usd`, `queries_by_outcome`, `latency_series`,
`cost` (current UTC month), `satisfaction`, `top_intents`. `to <= from` or span > `USAGE_MAX_RANGE_DAYS` -> 422.
Response `DashboardData` (see [schema](#dashboard)); ETag/304.

### GET /admin/dashboard/activity

`audience`, `limit` 1..50 (20). Response `ActivityFeed {items: ActivityEvent[], generated_at}`; event `type` in
`query|feedback|error|guardrail|document|admin`; summaries are PII-free; auth events never listed. ETag/304.

### GET /admin/users

Visitors grouped by client IP (not accounts). Params: `audience`, `include_test` (legacy; false + overview ->
users, true -> overview), `q` (substring of IP), `status` (ignored), `type` (`Test|Production`), `sort`
(only `asc` vs anything else on `last_active`), `limit` (50, **no upper bound**), `offset` (0), `weekday` 0..6
and `hour` 0..23 (must be sent together, else 422), `from`/`to` (<= 10 chars, days for the slot filter).
Response `{items, total, limit, offset}`; item: `id` (`client-<ip>`), `ip_address`, `is_test`, `type`, `status`,
`sessions`, `conversations`, `queries`, `feedback_count`, `first_seen`, `last_active`.

### GET /admin/users/{user_id}

`client-<ip>` or a user UUID; 404 `User not found`. Returns identity, counts and `conversations_data[]`.

### GET /admin/conversations

Params: `audience`, `q`, `ip`, `sort` (`last_activity_asc` or default desc), `limit` (50, **no upper bound**),
`offset`, `min_turns`/`max_turns` (>= 1; min > max -> 422), `outcome` (`OutcomeName`), `day` (requires
`outcome`, else 422). Response `{items, total, limit, offset}`; item: `id`, `title`, `created_at`,
`last_activity_at`, `expires_at`, `created_ip`, `message_count`, `turn_count`, `outcome_counts`.

### GET /admin/conversations/{conversation_id}

`turn` (>= 0) focuses a turn (echoed as `focus_turn`). Response `{conversation, messages[], message_count,
turns[], focus_turn}`; `turns` carry stage timings, tokens, cost, sources, feedback, insight labels and the
promoted golden case id. 404 `Conversation not found`.

### Entity stats (`backend/app/api/routes/admin_entities.py`, contract `docs/technical/frontend/contracts/entity-stats.md`)

| Route | Params | Response |
|---|---|---|
| `GET /admin/visitors/stats` | `audience`, `from`/`to` (YYYY-MM-DD, inclusive, default last 7 days, <= 400 days) | `VisitorStats {from, to, tz, kpis, new_vs_returning[], heatmap[]}`; `active_now` = last 30 min. ETag. |
| `GET /admin/visitors/{visitor_id}/timeline` | `visitor_id` 1..128 (`client-<ip>` or user UUID), `from`/`to` | `VisitorTimeline {visitor_id, ip_address, sessions[], queries[], cost_series[], feedback[], truncated}`; 404 `Visitor not found`. |
| `GET /admin/conversations/stats` | `audience`, `from`/`to`, `ip` <= 64, `q` <= 200 | `ConversationStats {from, to, totals, turns_histogram[], outcome_by_day[]}`. ETag. |

## 12. Admin: usage and cost

Two modules share the `/admin/usage` prefix: `admin_usage.py` (tabs, summary, export) and `admin.py` (cards,
test IPs, cost breakdown).

| Route | Params | Response / notes |
|---|---|---|
| `GET /admin/usage/audience-counts` | - | Badge counts (queries, sessions, distinct IPs) for overview/users/testing; in-process 15 s cache. |
| `GET /admin/usage/summary` | `audience`, `from_date`, `to_date` (ISO; invalid -> 422; span > 400 days -> 422) | `{audience, as_of, financial{total_cost_usd, llm_cost_usd, embeddings_cost_usd, avg_cost_per_query_usd, stage_costs{...}}, tokens{...}, pricing{...}, accounting{...}, quality{...}, latency{avg_ms,p50_ms,p95_ms}, cost_by_stage_series, forecast, cache}`. ETag + 15 s server cache. |
| `GET /admin/usage/users` | `audience`, `q`, `type`, `sort` in `cost_*, queries_*, tokens_*, sessions_*` (`_asc`/`_desc`; other -> 422), `limit` (10), `offset` | `{items, total, limit, offset, stats{unique_users, active_sessions, queries_driven, top_consumer_share_pct, top_users[], shared_gateway_hint}}`. No limit cap on this route. |
| `GET /admin/usage/sessions` | `audience`, `q`, `ip`, `sort` in `cost_*, queries_*, tokens_*, last_active_*`, `limit` (10), `offset` | `{items, total, limit, offset, ...}`; `limit > USAGE_PAGE_MAX_LIMIT` (200) or `offset > USAGE_MAX_OFFSET` (10000) -> 422 `Pagination limits exceeded.` |
| `GET /admin/usage/queries` | `audience`, `q`, `ip`, `session_id`, `outcome`, `grounded`, `from_date`, `to_date` (ISO datetime or plain date = whole UTC day; from > to -> 422), `sort` in `created_at_*, cost_*, tokens_*, latency_*`, `limit` (10, <= 200), `offset` (<= 10000) | `{items[], total, limit, offset, stats}`; item: `request_id, created_at (Z), query (160 chars), answer (200 chars), ip_address, session_id, session_title, prompt_tokens, reasoning_tokens, output_tokens, embed_tokens, cost_usd, outcome, grounded, latency_ms`. |
| `GET /admin/usage/queries/{request_id}` | - | Served by `admin_usage.get_query_detail`: `request_id, session_id, session_title, query, answer, created_at, ip_address, outcome, grounded, guardrail_flags, cost_usd, tokens{...}, rates{...}, pricing_rate_card{...}, latencies{...}`; 404 `Query record not found.` |
| `GET /admin/usage/export` | perm `usage:export`; `tab` `users|sessions|queries` (default queries), same filters as the tabs, `type` | `text/csv; charset=utf-8`, UTF-8 BOM, `Content-Disposition: attachment; filename="<brand>-<tab>-<audience>-<stamp>.csv"`; at most `USAGE_EXPORT_MAX_ROWS` (10000) rows; cells starting `= + - @ \t \r` prefixed with `'`; audited `usage_export`. |
| `GET /admin/usage/llm` | - | `{provider, model, requests, successful, failed, input_tokens, output_tokens, total_tokens, average_latency_ms, cost: "$x.xxxx", token_accounting}` (30-day latency rows). |
| `GET /admin/usage/embedding` | - | `{provider, model, dimensions, documents, chunks, requests, input_tokens, average_latency_ms, cost}` (token count estimated chars/4). |
| `GET /admin/usage/reranker` | - | `{provider, model, queries, requests, documents_per_query, top_n, average_latency_ms, throughput, cpu_percent, ram_mb, active_requests, errors}` |
| `GET /admin/usage/metadata` | - | Static config: `llm`, `embedding`, `reranker`, `retrieval`, `chunking`. |
| `GET /admin/usage/test-ips` | - | `{default_ips, custom_ips[{id, ip_address, description, is_active, created_at}], effective_ips, classification_note}` |
| `POST /admin/usage/test-ips` | perm `testip:write`; body `TestIpPayload {ip_address: string, description?: string}` | Add or reactivate; audited; clears audience caches. 422 for empty IP, IP > 64 chars, control chars, description > 200 chars, or more than `TEST_IP_MAX_ENTRIES` (500) entries. |
| `DELETE /admin/usage/test-ips/{ip_address:path}` | perm `testip:write` | `{deleted: bool, ip_address}` (200, not 204). |
| `GET /admin/usage/cost-breakdown` | `from_date`, `to_date`, `ip`, `session_id`, `q`, `outcome`, `limit` (clamped 1..200), `offset` | `{overall, by_account, by_ip, by_test_ip, test_ips, sessions, queries, total_queries, limit, offset, token_accounting}` |

**Duplicate route:** `GET /admin/usage/queries/{request_id}` is declared twice. `admin_usage.get_query_detail`
is registered first (via `router.include_router(usage_router)` at the top of `admin.py`) and is the one that
answers; `admin.get_query_usage_detail` (404 `Query with request_id '<id>' not found`) is unreachable, but its
summary ("Get Query Usage Detail") is what `/openapi.json` shows.

## 13. Admin: feedback (RLHF loop)

`backend/app/api/routes/admin_feedback.py`, prefix `/admin/feedback`.

| Route | Guard | Params / body | Response | Errors |
|---|---|---|---|---|
| `GET /admin/feedback` | `dashboard:read` | `audience`, `rating` 1..5, `sort` `created_at_desc|created_at_asc|rating_desc|rating_asc`, `q` <= 200, `triage_status`, `outcome`, `grounded`, `has_comment`, `date_from`/`date_to` (datetime), `message_id` (UUID), `limit` 1..200 (50), `offset` 0..10000, `cursor` <= 512 | `FeedbackPage` | 422 (`date_from must be before date_to`, `Invalid cursor: ...`, audience) |
| `GET /admin/feedback/analytics` | `dashboard:read` | `audience` | `FeedbackAnalytics` (ETag; `by_intent` omitted until insights exist) | 422 |
| `GET /admin/feedback/golden` | `dashboard:read` | `limit` 1..200, `offset` 0..10000 | `GoldenCasePage` | |
| `GET /admin/feedback/export` | `feedback:export` | `format=jsonl`, `triage_status`, `include_dismissed` (false), `complete_only` (false) | `application/x-ndjson` stream, one object per line (`prompt, chosen, rejected, rating, reason_tags, source_ids, complete, missing`); `Cache-Control: no-store`; audited `feedback_exported` | |
| `POST /admin/feedback/golden/{case_id}/run` | `feedback:triage` | `case_id` `^[A-Za-z0-9_\-]{1,64}$` | 202 `GoldenRunResponse {case_id, status: queued|already_queued, job_id}` (idempotent) | 404 `Golden case not found` |
| `POST /admin/feedback/bulk` | `feedback:triage` | `FeedbackBulkRequest` | `FeedbackBulkResponse` | 404 / 409 (all-or-nothing) |
| `PATCH /admin/feedback/{feedback_id}` | `feedback:triage` | `FeedbackTriageRequest` | `FeedbackItem` | 404, 409 (invalid transition or stale `version`) |
| `POST /admin/feedback/{message_id}/promote-to-test` | `feedback:triage` | optional `PromoteRequest {review_note? <= 2000}` | `PromoteResponse {status: "promoted", case_id, query, created, feedback_ids}` | 400 `Invalid message ID format`, 404, 409 |

Triage transitions: `new -> reviewed|promoted|dismissed`; `reviewed -> promoted|dismissed`; `dismissed -> new`.
Keyset paging: pass `next_cursor` as `cursor`; `offset` is then reported as 0.

## 14. Admin: governance

`backend/app/api/routes/admin_governance.py`, all perm `dashboard:read`, all take `audience`. 422 for invalid
filters, cursor or `from > to` (`` `from` must be before `to`. ``).

| Route | Params | Response |
|---|---|---|
| `GET /admin/governance/monitoring` | - | Untyped dict: `window_hours, latency_unit, api_requests, queries_24h, active_users, active_queries, avg_latency_ms, total_latency_ms, p95_latency_ms, error_rate, error_counts, traces_total, *_latency_ms, stage_latency, stage_percentiles, hourly[24], guardrail_flag_breakdown, outcome_mix, counts, process_cpu_percent, process_ram_mb, process_ram_percent, azure_quota_telemetry, azure_quota_series, online_quality_kpis`. Stage averages with no samples are null. |
| `GET /admin/governance/traces` | `limit` >= 1 (100), silently capped at 500 | `GovernanceTraceItem[]` (legacy array); true total in `X-Total-Count`. `trace_id` removed. |
| `GET /admin/governance/traces/page` | `limit` 1..200 (50), `cursor` <= 200, repeatable `status` (`ok|ungrounded|blocked|failed|unknown`), `outcome` (`answered|ungrounded|blocked|conversational|llm_failed`), `latency_bucket` (`fast|moderate|slow|very_slow`, bounds `GOVERNANCE_LATENCY_BUCKETS_MS` = 1000/3000/10000), `q` <= 200, `from`/`to` | `GovernanceTracePage`; keyset on `(created_at, id)`; `total` exact up to `GOVERNANCE_EXACT_COUNT_CAP` (100000) then estimate |
| `GET /admin/governance/traces/{request_id}` | `request_id` `^[A-Za-z0-9._:\-]{1,64}$` | `GovernanceTraceDetail`; 404 `Trace not found.` (also when outside the audience) |
| `GET /admin/governance/logs` | `limit` 1..200 (200), `cursor`, repeatable `level` (`CRITICAL|ALERT|ERROR|WARNING|INFO`), `service` (`auth|query|ingest|feedback|admin|system`), `q`, `from`/`to` | `GovernanceLogPage`; auth events hidden without `audit:auth:read` |
| `GET /admin/governance/logs/export.csv` | same filters as logs | `text/csv` stream (BOM, columns Created At, Level, Service, Action, Summary, User, IP Address, Request ID, Detail), <= `GOVERNANCE_EXPORT_MAX_ROWS` (100000), `no-store`, audited `governance_logs_export` |
| `GET /admin/governance/overview` | - | `GovernanceOverview` (ETag) |
| `GET /admin/governance/stream` | - | SSE |

### SSE: GET /admin/governance/stream

`text/event-stream` via `backend/app/core/sse.py`: first frame `retry: <SSE_RETRY_MS>` (3000); events
`snapshot` (full overview on connect), `health` (status change), `incident` (new CRITICAL/ALERT row, `id` =
audit id), `monitoring` (changed KPI values); `: keepalive` comment every `SSE_HEARTBEAT_SECONDS` (15) when idle.
One shared producer per worker and audience ticks every `GOVERNANCE_STREAM_INTERVAL_SECONDS` (5) or on a NOTIFY.
The stream ends cleanly on client disconnect. Not gzipped. This is the only SSE route; `/query` is not streamed.

## 15. Admin: insights and taxonomy

`backend/app/api/routes/insights.py`, prefix `/admin/insights`, OpenAPI tag `Insights`. Reads use
`read_only_session` (5 s statement timeout -> 503).

Shared filters (all read routes except `/pending` and `/taxonomy`):

| Param | Type | Rule |
|---|---|---|
| `period` | `all|7d|30d|custom` (default `all`) | `custom` needs `from` and `to`, `from <= to`, span < 731 days |
| `from`, `to` | date | |
| `user_type` | string <= 64 | |
| `outcome` | `Served|Partly served|Not served|Greeting only` | |
| `rating` | `Great|Good|Served with friction|Partly served|Dropped|No question asked` | |
| `issue`, `intent`, `quality` | code `^[A-Z][A-Z0-9_]{0,31}$` | |
| `person` | `^[0-9a-f]{16}$` | stable pseudonym |
| `q` | <= 200, no control chars | |
| `audience` | `overview|users|testing` | |
| `tz` | IANA zone <= 64 (default `INSIGHTS_TIMEZONE`) | |

Validation failures are re-raised as normal 422 problem+json with `loc` using the query names (`from`/`to`).

| Route | Extra params | Response |
|---|---|---|
| `GET /summary` | - | `InsightsSummary` (ETag) |
| `GET /trend` | - | `InsightsTrend {range, days[], source}` |
| `GET /hourly` | - | `InsightsHourly {tz, hours[24], total, peak_hour}` |
| `GET /flow` | - | `InsightsFlow {total, user_types[], outcomes[], links[]}` |
| `GET /topics` | - | `InsightsTopics {total, items[], source}` |
| `GET /quality` | - | `InsightsQuality {total, items[], source}` |
| `GET /dropoff` | - | `InsightsDropoff {total, items[]}` |
| `GET /people` | - | `InsightsPeople {total, items[]}` (pseudonyms P01..., no e-mail or IP) |
| `GET /conversations` | `sort` (`conv_id|person|start|user_type|outcome|experience_rating|issue|exchanges`, default `start`), `dir` (`asc|desc`), `limit` 1..100 (25), `offset` 0..10000 | `ConversationList` |
| `GET /conversations/{conv_id}` | UUID; only `audience` and `tz` apply | `ConversationDetail {conversation, turns[]}`; 404 `Conversation not found.` |
| `GET /pending` | - | `PendingClassification {pending, labelled, total, progress_pct}` |
| `GET /taxonomy` | `kind` (`intent|bot_quality|issue`), `include_archived` | `TaxonomyList {items: TaxonomyRead[]}` |
| `POST /taxonomy` | perm `taxonomy:write`; `TaxonomyCreate` | 201 `TaxonomyRead`; 409 duplicate |
| `PATCH /taxonomy/{kind}/{code}` | perm `taxonomy:write`; `TaxonomyUpdate` (at least one field; `label`/`sort_order` not null) | `TaxonomyRead`; 404, 409 |
| `POST /taxonomy/{kind}/{code}/archive` | perm `taxonomy:write` | `TaxonomyRead`; 404, 409 (protected) |

`source` (`rollup|live`) says whether the daily rollup or a live query produced the numbers. Taxonomy writes
are audited; codes are immutable; entries are never deleted.

## 16. Branding

`backend/app/api/routes/branding.py`.

| Route | Guard | Details |
|---|---|---|
| `GET /branding` | public | `BrandingRead`; DB row over config defaults, derived 50-950 palette, asset URLs; public cache 300 s + ETag |
| `GET /branding/assets/{sha}` | public | `sha` `^[0-9a-f]{64}$`; bytes with `image/png|image/webp|image/svg+xml`; strong ETag, immutable 1 year, `nosniff`; SVG also gets `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`; 404 `Brand asset not found.`, 422 malformed id |
| `PUT /admin/branding` | perm `branding:write` | `BrandingUpdate` (partial, `version` required); 409 stale version; 422 validation or WCAG AA contrast failure (primary must give 4.5:1); URLs must be `https://` or relative; audited |
| `POST /admin/branding/assets` | perm `branding:write` | multipart `file`; PNG/WebP/SVG <= `BRANDING_ASSET_MAX_BYTES` (512 KB) -> 201 `BrandAssetRead`; 413 too large, 415 wrong type, 422 invalid image; SVG sanitized; content-addressed id |

## 17. Auth (SSO, session, access requests)

`backend/app/api/routes/auth.py`, prefix `/auth`. Flow detail in [Security](07-security.md).

| Route | Guard | Request | Response | Errors |
|---|---|---|---|---|
| `GET /auth/sso/login` | service key + login limit | - | `SsoLoginRead {authorize_url}`, `Cache-Control: no-store` | 401, 429, 502 `Identity provider unavailable.` |
| `POST /auth/sso/callback` | service key + login limit | `SsoCallbackRequest {code: 1..4096 printable ASCII, state: 16..256 [A-Za-z0-9_-]}`; optional `X-Session` (previous session to rotate) | `SessionIssuedRead {session, cookie_name, cookie_secure, expires_at, me: MeRead}`, `no-store` | 400 `Sign-in link expired or was already used. Start again.`; 401 `Sign-in failed.`; 403 suspended / identity conflict; 422; 429; 502 |
| `GET /auth/me` | signed-in (any status) | - | `MeRead {user{id,email,name}, role, status, permissions[], version, access_contact_email}`; ETag, `max-age=0` | 401 |
| `POST /auth/logout` | signed-in | - | 204; revokes the server-side session; audited | 401, 403 (CSRF) |
| `GET /auth/access-request` | chat | - | `AccessRequestStatusRead {user_status, role, request, access_contact_email}` | 401, 403 |
| `POST /auth/access-request` | chat | `AccessRequestCreate {requested_role?: Role, justification? <= 1000, no control chars}` | 201 `AccessRequestRead` | 409 already active or request pending |

Every sign-in outcome (incl. denials) is audited.

## 18. User management

`backend/app/api/routes/user_management.py`, tag `User Management`, all perm `users:manage` (superadmin).

| Route | Params / body | Response | Errors |
|---|---|---|---|
| `GET /admin/users-accounts` | `search` <= 100 (email or name, literal, case-insensitive), `role`, `status`, `sort` `created_at|last_login_at|email|name`, `order` `asc|desc`, `limit` 1..200 (50), `offset` 0..10000 | `UserAccountPage` | 422 |
| `GET /admin/users-accounts/stats` | `days` 1..90 (30) | `UserStatsRead {days, kpis, logins_per_day[], funnel[], role_mix[], time_to_approve[]}` | |
| `POST /admin/users-accounts/invite` | `UserInviteCreate {email <= 320, role, name? <= 256}` | 201 `UserAccountRead` (active; linked on first SSO) | 409 email exists |
| `GET /admin/users-accounts/{user_id}` | UUID | `UserAccountDetail` (+ `active_sessions[]`) | 404 |
| `PATCH /admin/users-accounts/{user_id}` | `UserAccountUpdate {version, role?, status?: active|suspended}` | `UserAccountRead` | 404; 409 stale version, self-change, last active superadmin |
| `POST /admin/users-accounts/{user_id}/revoke-sessions` | - | `RevokeSessionsRead {revoked}` | 404 |
| `GET /admin/access-requests` | `status` `pending|approved|rejected|all` (pending = oldest first), `limit`, `offset` | `AccessRequestPage` | |
| `POST /admin/access-requests/bulk-approve` | `AccessRequestBulkApprove {ids: 1..100 UUIDs, role}` | `BulkApproveRead {approved, ids}` (all-or-nothing) | 404, 409 |
| `POST /admin/access-requests/{request_id}/approve` | `AccessRequestApprove {role}` | `AccessRequestAdminRead` | 404, 409 already decided |
| `POST /admin/access-requests/{request_id}/reject` | `AccessRequestReject {note: 1..1000}` | `AccessRequestAdminRead` | 404, 409 |

## 19. Mock IdP (development only)

Mounted by `mount_mock_idp()` (`backend/app/auth/mock_idp.py`) only when `AUTH_DEV_MOCK=true`, which settings
refuse unless `ENV=development`. Excluded from OpenAPI. Public by design (stands in for Entra).

| Method | Path |
|---|---|
| GET | `/mock-idp/{tenant}/v2.0/.well-known/openid-configuration` |
| GET | `/mock-idp/{tenant}/discovery/v2.0/keys` |
| GET, POST | `/mock-idp/{tenant}/oauth2/v2.0/authorize` |
| POST | `/mock-idp/{tenant}/oauth2/v2.0/token` |

## 20. Schema reference

Source of truth: `backend/app/schemas/*.py` (or `/openapi.json` in a non-production environment).

### Query and sessions

`ChatSessionSummary`: `id`, `title`, `created_at`, `last_activity_at`, `expires_at`, `is_expired`,
`message_count`. `ChatSessionDetail` adds `messages: ChatMessageOut[]`.

| `ChatMessageOut` field | Type |
|---|---|
| `id`, `role`, `content` | string |
| `created_at` | datetime |
| `request_id` | string or null (assistant turns; links to the governance trace) |
| `grounded` | bool or null |
| `guardrail_flags` | string[] or null |
| `feedback_rating` | int or null |
| `prompt_tokens`, `completion_tokens`, `total_tokens`, `reasoning_tokens`, `embedding_tokens`, `guardrail_tokens` | int or null |
| `cost_usd` | float or null |
| `token_details` | object or null |

`QueryRequest` / `QueryResponse` / `UserFeedbackRequest`: see [section 8](#8-query-and-chat-sessions).

### Feedback

| `FeedbackItem` field | Type |
|---|---|
| `id`, `conversation_id`, `message_id`, `actor`, `ip_address`, `comment` | string |
| `rating` | int |
| `created_at`, `updated_at` | string or null |
| `user_query`, `answer`, `outcome` | string or null |
| `grounded` | bool or null |
| `guardrail_flags`, `reason_tags` | string[] |
| `sources` | `{source_name, section_heading, score, chunk_id}[]` |
| `confidence`, `latency_ms`, `cost_usd` | float or null |
| `corrected_answer`, `reviewed_by`, `reviewed_at`, `review_note`, `promoted_case_id` | string or null |
| `triage_status` | `new|reviewed|promoted|dismissed` |
| `version` | int (optimistic concurrency) |

`FeedbackPage`: `items`, `total` (rows matching filters), `limit`, `offset`, `next_cursor`.

`FeedbackTriageRequest`: `version` >= 1 (required), `triage_status?`, `review_note?` <= 2000, `corrected_answer?`
<= 4000 (PII-masked). Sending a text field as `null` clears it; omitting keeps it.

`FeedbackBulkRequest`: `ids` 1..200 UUIDs (deduped), `action` `reviewed|dismissed|promoted`, `review_note?`.
`FeedbackBulkResponse`: `action`, `updated`, `ids`, `promoted_case_ids`.

`FeedbackAnalytics`: `total`, `total_feedback`, `positive_count`, `negative_count`, `neutral_count`,
`positive_ratio`, `negative_ratio`, `feedback_rate`, `feedback_by_outcome{outcome: {positive, negative, total}}`,
`daily_trend[{day, total, positive, negative, avg_rating}]`, `rating_distribution`, `by_intent?`,
`top_reasons[{key, count}]`, `triage_funnel`, `disagreement{positive_grounded, positive_ungrounded,
negative_grounded, negative_ungrounded}`, `knowledge_gaps[{query, count, avg_rating, avg_confidence, last_seen,
message_id}]`.

`GoldenCase`: `id`, `query`, `case_type`, `expect_grounded`, `expected_outcome`, `source_message_id`,
`feedback_id`, `reference_answer`, `reason_tags`, `promoted_by`, `promoted_at`, `last_run_at`,
`last_run_passed`, `last_run_detail`.

### Documents

`DocumentSummary`: `document_id`, `source_name`, `source_type`, `chunk_count`, `created_at`, `updated_at`,
`original_format`, `active_format` (`MARKDOWN`), `converted_from`, `original_filename`, `content_hash`,
`original_sha256`, `ingest_count`, `warnings[]`, `quarantined`, `content_sha256`, `canonical_format` (`md`),
`standardized`, `converter`, `converter_version`, `original_size_bytes`, `canonical_size_bytes`,
`canonical_available`, `total_chunk_chars`, `chunk_strategies[]`, `chunking_strategy`, `embedding_model`,
`embedding_dim`, `ingested_at`, `ingested_by`, `status` (`active`), `pii_redacted`, `pipeline_version`,
`labels{}`. Fields a document never recorded are null.

`DocumentDetail` = `DocumentSummary` + `history: IngestEvent[]`, `history_truncated`, `chunk_stats`.

`DocumentChunkItem`: `id`, `chunk_index`, `content`, `strategy`, `base_strategy`, `section_heading`,
`char_count`, `word_count`, `content_hash`, `hits`, `low_rated_hits`, `last_hit_at`, `weekly_hits[]`.

`DocumentCitationStats`: `from`, `to`, `tz` (`UTC`), `top_cited[]`, `age_vs_citations[]`, `coverage`.
`CanonicalMarkdown`: `document_id`, `source_name`, `format`, `available`, `content`, `size_bytes`,
`content_sha256`. `LowRatedCitations`: `document_id`, `total`, `items[]`.

### Governance

| `GovernanceTraceItem` field | Type |
|---|---|
| `request_id`, `timestamp` | string |
| `user_id`, `conversation_id`, `ip_address`, `outcome`, `query` (redacted, truncated) | string or null |
| `status` | `ok|ungrounded|blocked|failed|unknown` |
| `guardrail_flags` | string[] |
| `total_ms` | float; `latency_unit` = `ms` |
| `latency_bucket` | `fast|moderate|slow|very_slow` or null |
| `slowest_stage` | `{stage, ms, unit}` or null |
| `tokens` | `{prompt, completion, reasoning, embedding, total}` |
| `cost_usd` | float or null |

`GovernanceTraceDetail` adds `grounded`, `stages {unit, total_ms, stages[{stage, start_ms, duration_ms}],
other_ms}`, `sources[{chunk_id, document_id, source_name, score}]`, `flags[]`.

`GovernanceTracePage` / `GovernanceLogPage`: `items`, `total`, `total_estimate`, `total_is_estimate`, `limit`,
`next_cursor`, `has_more`; logs add `level_counts{}` and `level_counts_capped`.

`GovernanceLogItem`: `id`, `created_at`, `level`, `service`, `action`, `summary`, `detail{}`, `message`
(deprecated, 300-char JSON string), `user_id`, `ip_address`, `request_id`.

`GovernanceOverview`: `audience`, `window_hours`, `hours[]`, `health {status: Healthy|Degraded|Incident,
window_minutes, signals{name: {value, unit, status, degraded_at, incident_at}}}`, `kpis{name: {value, unit,
sparkline[24], window, source}}`, `incidents: GovernanceLogItem[]`, `online_quality_kpis`.

### Insights

`InsightsSummary`: `range{from,to}`, `conversations`, `exchanges`, `handled_exchanges`,
`substantive_questions`, `handled_questions`, `handled_pct`, `served_conversations`, `active_conversations`,
`served_pct`, `people`, `avg_questions_per_conversation`, `pending_classification`, `source`.

`ConversationRow`: `conv_id`, `person`, `person_id`, `start`, `end`, `minutes`, `user_type`, `outcome`,
`experience_rating`, `issue`, `issue_label`, `failing_question`, `exchanges`, `substantive_exchanges`.
`ConversationList` adds `total`, `limit`, `offset`, `sort`, `direction`.

`TurnDetail`: `turn`, `timestamp`, `user_message`, `intent`, `intent_label`, `substantive`, `handled`,
`quality`, `quality_label`, `issue`, `issue_label`, `friction`, `classified_by`, `pending`.

`TaxonomyCreate`: `kind`, `code` (`^[A-Z][A-Z0-9_]{0,31}$`), `label` 1..128, `description?` <= 1000, `color?`
`#RRGGBB`, `group?` 1..64, `sort_order` 0..10000, `substantive` (true), `handled` (false).
`TaxonomyRead` adds `id`, `is_active`, `is_system`, `created_at`, `updated_at`.

### Users and auth

`UserAccountRead`: `id`, `email`, `name`, `role` (`superadmin|admin|operator` or null), `status`
(`pending|active|rejected|suspended`), `last_login_at`, `created_at`, `approved_by_email`, `approved_at`,
`version`. `UserAccountDetail` adds `active_sessions[{id, created_at, last_seen_at, expires_at, ip_address,
user_agent}]`.

`AccessRequestAdminRead`: `id`, `user_id`, `user_email`, `user_name`, `user_status`, `requested_role`,
`justification`, `status`, `created_at`, `decided_at`, `decided_by_email`, `decision_note`, `waiting_seconds`.

`UserStatsRead.kpis`: `total`, `active`, `pending`, `rejected`, `suspended`, `open_requests`, `logins_24h`.

### Branding

`BrandingRead`: `brand_name`, `brand_assistant_name`, `brand_support_email`, `product_name`, `assistant_name`,
`company_name`, `support_email`, `access_contact_email`, `primary_hex`, `accent_hex`, `tint_hex`,
`palette{50..950}`, `logo_light_asset_id`, `logo_dark_asset_id`, `mark_light_asset_id`, `mark_dark_asset_id`,
`favicon_asset_id`, `assets{logo_light, logo_dark, mark_light, mark_dark, favicon}` (URLs), `login_eyebrow`,
`login_headline`, `login_subtext`, `login_description`, `privacy_url`, `terms_url`, `help_url`,
`chat_sample_questions[]`, `version`, `updated_at`.

`BrandingUpdate` (all optional except `version` >= 1): names 1..80 (`company_name` 1..120, no control chars),
`support_email`/`access_contact_email` (email), colours `#RRGGBB`, asset ids 64 hex, `login_eyebrow` <= 60,
`login_headline` <= 160, `login_subtext` <= 400, `login_description` <= 1000, URLs <= 2048,
`chat_sample_questions` <= 8 items.

`BrandAssetRead`: `id`, `mime`, `size_bytes`, `width`, `height`, `url`, `created_at`.

### Dashboard

| `DashboardData` field | Type | Present |
|---|---|---|
| `users`, `active_users`, `sessions`, `conversations`, `queries`, `feedback` | int | always |
| `avg_latency_ms`, `error_rate` | float | always |
| `pipeline`, `governance`, `query_trend`, `latency_breakdown` | panels | always (nullable) |
| `window` | `{from, to, bucket: hour|day, previous_from, previous_to}` | with `from`/`to` |
| `cost_usd` | float | with window |
| `sparklines` | `{kpi: [{t, value}]}` | with window |
| `deltas` | `{kpi: float or null}` | with window |
| `queries_by_outcome` | `[{t, answered, ungrounded, blocked, conversational, llm_failed}]` | with window |
| `latency_series` | `[{t, p50_ms, p95_ms, count}]` | with window |
| `cost` | `{currency: USD, month, mtd_usd, forecast_month_end_usd, budget_usd, days_elapsed, days_in_month}` | with window |
| `satisfaction` | `{thumbs_up, thumbs_down, ratio}` | with window |
| `top_intents` | `[{intent, label, count, share}]` | with window |

Full aggregation semantics: `docs/technical/frontend/contracts/dashboard-aggregations.md`.

## 21. Production contract (hotfix branch)

**In progress (branch `hotfix/query-filters-ip-ingest`) - live in prod, not yet in preview/view.**

Production (askdiq.ethub.co, see [Deployment](08-deployment-operations.md)) runs `main` plus this hotfix. The
hotfix branches from `main` at `1a88798`, **not** from `preview/view`, so the production API is the older
single-key API. Commits: `a795af3`, `27d516c`, `f9d76ed`. Public integration guide:
[Website integration](10-website-integration.md).

### Differences from preview/view

| Aspect | Production (main + hotfix) | preview/view |
|---|---|---|
| Auth on `/query`, `/sessions/*` | `get_current_actor` (`backend/app/governance/auth.py`): `X-API-Key == API_KEY_NAME` else 401 `Access Denied!`; actor = `X-Client-Id` (UUID or `[A-Za-z0-9_\-.@]{1,64}`), invalid or missing -> shared actor `default@local` | SSO session (`X-API-Key` + `X-Session` + CSRF) |
| Error body | Plain FastAPI `{"detail": ...}` (no `app/core/exceptions.py`) | problem+json |
| Routers | health, query, sessions, ingest, documents, metrics, governance, admin only (no auth, branding, insights, user management) | full set in section 6 |
| `/query` LLM capacity gate | none | `llm_capacity` 429 |
| Feedback body | `rating`, `comment`, `ip_address` (no `reason_tags`, `corrected_answer`) | see section 8 |

`preview/view` keeps this contract for the website on the same paths (`require_chat_actor`): `X-API-Key` +
`X-Client-Id`, no session. Errors there use problem+json, which still carries `detail`.

### Hotfix behaviour

| Change | Detail |
|---|---|
| Allow-listed `filters` | `usable_filters()` (`app/retrieval/vector_store.py`) keeps only keys in `RETRIEVAL_FILTER_KEYS` (default `source_type`, `corpus`, `ingestion_source`) with non-empty scalar values; others are dropped and logged `retrieval_filters_ignored`. Swagger's placeholder `{"additionalProp1": {}}` no longer empties answers. |
| `no_sources` flag | When retrieval returns nothing, `guardrail_flags` is `["no_sources"]` (was `["off_topic"]`); the fallback answer text is the same "couldn't find relevant information" message. |
| `ip_address` on `/query` | `resolve_client_ip(request, body.ip_address)`: when `ALLOW_CLIENT_IP_OVERRIDE` is true and the value is a valid IPv4/IPv6 (first hop of a comma list, normalised), it is used; else `get_client_ip()` (TCP peer, or first `X-Forwarded-For` hop with `TRUST_PROXY_HEADERS=true`). Settings default is `true`; `.env.example` ships `false` (Unverified: value set in prod `.env`). |
| `ip_address` on sessions and feedback | New optional field on `CreateSessionRequest` and `UserFeedbackRequest`, resolved the same way; stored against the session / feedback and used for the feedback per-IP limit. |
| Per-IP limit on the end-user IP | `rate_limited_actor` now enforces only the per-user limit; `/query` calls `enforce_ip_rate_limit(client_ip)` after resolving the forwarded IP (30/min, in-process window). |
| Swagger example | `QueryRequest` example `{"query": "What is the Trust Center?", "session_id": "", "ip_address": "203.0.113.10"}` (`""` = no session; null would be dropped from the served OpenAPI) (no `filters`). |

## 22. In-flight route and contract changes

| Branch | State | Route / contract effect |
|---|---|---|
| **In progress (branch `fix/chat-page-fetch-timeouts`)** | uncommitted in `C:\Projects\askdiq-wt\fix-chat-timeouts` | `GET /health` adds `pool: {size, max_overflow, checked_out, idle}` (checked_out > 0 on an idle server means a leak) and releases its probe connection first. `GET /branding` reads through a session factory with `BRANDING_LOAD_TIMEOUT_SECONDS` (5; config defaults served on timeout). `/admin/dashboard` (windowed), `/admin/feedback/analytics`, `/admin/governance/monitoring` release their connection before parallel fan-out. SSE producer snapshots bounded by `GOVERNANCE_STREAM_SNAPSHOT_TIMEOUT_SECONDS` (60). New settings `DB_POOL_TIMEOUT_SECONDS` (120), `AGG_POOL_RESERVE` (5). No path or schema change apart from `/health`.pool. |
| **In progress (branch `perf/dashboard-load-speed`)** | committed (`3bf8142`, `2e6bd62`), unmerged | No path or field changes. `/admin/usage/llm` cached for `USAGE_SUMMARY_CACHE_TTL_SECONDS` with single flight; `/admin/usage/embedding` and `/reranker` aggregate in SQL (30-day latency window); `/admin/usage/summary` per-key single flight and parallel aggregates; faster `/admin/governance/logs` and `/traces/page`. Migration `b2d6e08d82b3` adds a partial index on `chat_messages`. |
| **In progress (branch `fix/shell-nav-logout-theme-dates`)** | committed, unmerged | `DashboardData` gains `active_visitors {definition, active_now, active_now_minutes, dau, wau, mau}` (each `{value, previous, delta, days}`); `active_users` becomes DAU under one shared definition (>= 1 query, test IPs excluded). `VisitorStats.kpis.active_now` becomes last 5 minutes and adds `active_visitors`. `/admin/users` `sort` honours `last_active|first_seen|queries|sessions` + `_asc|_desc`. `/admin/conversations` `sort` adds `created_at_asc|desc` with id tiebreak. `/admin/usage/users` adds `last_active_asc|desc` sorts and a `last_active` field. Feedback JSONL export lines add `triage_status`, `created_at`. Governance overview active-users KPI uses the same definition. |
| **In progress (branch `feat/realistic-demo-data`)** | committed, unmerged | No new routes. Live path fills fields that were empty: `latency_ms` passed into the exchange write, audit `detail.message_id` on query events, feedback analytics audience filter on `chat_sessions.created_ip`. Adds seed scripts. |
| `feat/askdiq-chat-widget`, `chore/prod-deploy-fullstack` | committed, unmerged | No backend route changes. |

## 23. Known gaps and unverified items

- `/admin/users`, `/admin/conversations` and `/admin/usage/users` accept unbounded `limit` values; other lists
  cap at 100-200.
- `GET /admin/usage/queries/{request_id}` is declared twice (section 12); the OpenAPI summary belongs to the
  unreachable handler.
- `/metrics/evaluation` and `/metrics/evaluation/live` take `audience` as a free string (not `audience_scope`).
- `rate_limited_admin_analytics` and `Permission.EVAL_RUN` are defined but unused by routes.
- `app/schemas/filters.py` models (`UserFilterParams`, etc.) are used by `app/db/query_builders.py`, not by any
  route signature.
- `Vary` on private cached responses names `X-Client-Id`, not `X-Session`.
- Unverified: production `.env` values of `ALLOW_CLIENT_IP_OVERRIDE`, `TRUST_PROXY_HEADERS`, `ENABLE_API_DOCS`.
