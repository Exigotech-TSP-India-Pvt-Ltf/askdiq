# AskDIQ plan

The single plan for the AskDIQ system. It replaces the old root plan files
and the archived phase plans, which have been removed.

Status was last checked against the code on 2026-10-20 (after PR #58 and
#59). An item is **Done** only if the code for it exists in the repo.

| Status | Meaning |
|---|---|
| Done | Merged or on the named branch, and present in the code |
| In progress | Being built now on a branch |
| Planned | Agreed, not started |
| Open | Still missing in the code (found during the 2026-10-19 check) |
| Decision | Waiting on an owner decision |

---

## 1. What the system must do

AskDIQ is the retrieval-augmented assistant for the DeployIQ / Exigo Tech
website, plus the admin console that runs it.

1. **Answer website visitors** from the curated knowledge base, with
   citations, guardrails and a grounded fallback. The public website calls the
   API with a service key.
2. **Give staff an admin console** behind Microsoft Entra ID SSO with roles:
   dashboard, chat, knowledge base, visitors, usage and cost, conversation
   logs, feedback, evaluation, governance, analytics, user management and
   settings.
3. **Run safely in production**: one Postgres (with pgvector) for data and
   vectors, OWASP-aligned controls, audit trail, bounded latency and
   predictable cost.

Architecture and features: [README](../README.md). Details:
[technical docs](technical/README.md). Deployment:
[deployment/](deployment/deployment-guide.md).

---

## 2. October 2026 release

Shipped in PR #58 (admin console release) and PR #59 (concurrency gate in
steady state), both merged to `main`.

| # | Work item | Status |
|---|---|---|
| a1 | Settings > Models & Pricing: runtime overrides of the LLM deployment, embedding deployment and token prices, verified with one live Azure call before saving (`backend/app/model_settings/`, `/admin/models`) | Done |
| a2 | Nav renames: Chat -> AskDIQ, Documents -> Knowledge, Evaluation (Live) -> Evaluation | Done |
| a3 | White logo on the login page | Done |
| a4 | Dedupes (shared QueryInspector/Evaluation drawer, permission shims) | Done |
| b | Instant dashboards: precomputed `dashboard_snapshots` kept warm by the jobs worker, stale-while-revalidate cache, persisted client cache, live push over `/admin/live` | Done |
| c | Clarifying questions with options in the RAG pipeline and the chat UI; icon-only chat actions | Done |
| d | Alert catalogue, `alerts` table and email alerts over SMTP; header notification bell | Done |
| d2 | Resource monitoring: host/container/process sampler (`resource_samples`), live gauges and resource alerts | Done |
| d3 | One standard error envelope (coded problem+json), dependency-failure mapping, safe `get_db` close | Done |
| d4 | Swagger exposes only the ServiceKey scheme | Done |
| e1 | Visitors and Conversation Logs merged into one Visitors & Conversations page | Done |
| e2 | Comparable latency units on Governance and every chart's axis titles, units and legends | Done |
| e3 | Evaluation shows "Not measured" and an empty state when online evaluation is off | Done |
| e4 | Blank origin IP shows "Not recorded" with an explanation | Done |
| e5 | Analytics integrity: UTC bucketing, stable probable people, drill-downs and linkages | Done |
| f | Dependency decisions: safe Dependabot minor/patch and Actions updates merged; framework majors tracked in section 3 | Done |
| h | Docs and infra consolidation: one plan, one README, all markdown under `docs/`, deploy files under `infra/` | Done |
| g | Branch cleanup: only `main` remains | Planned |
| i | Foundry free-data integration: planned (local plan) | Planned |

---

## 3. Open items found in the code check

These were marked done, or were never closed, in the old plans, and are
still missing.

### Product and API

| Item | Evidence |
|---|---|
| `GET /admin/search` (global search behind the command palette) | No route; the palette has a stub (`frontend/components/ui/CommandPalette.tsx`) |
| One `<DateTime>` component for every timestamp | Only `components/ui/RelativeTime.tsx` exists |
| Retire the old Python usage path behind `/admin/usage/cost-breakdown` and `/usage/llm` (deprecated in favour of `/admin/usage/summary` and related endpoints) | `_query_usage_rows()` still in `backend/app/api/routes/admin.py` |
| Delete the unused legacy evaluation report (it duplicates `ChartCard`) | `frontend/components/admin/evaluation/LegacyEvaluationReport.tsx`; nothing imports it |
| Document citation daily rollup and its stats test | No `document_citation_daily`; citations are computed live (`backend/app/documents/citations.py`) |
| Usage hourly rollup (pre-aggregation for the usage pages) | Only `insight_daily_rollup` exists |
| `X-Page-Path` capture (which website page a chat started on) | Not built |
| `/health` readiness: report Azure reachability | Reports DB, reranker and pool only (`backend/app/api/routes/health.py`) |
| Streaming answers (time to first token) | `/query` returns one JSON response |
| Remove the unused `mammoth` dependency | `frontend/package.json`; no importer |
| Streaming CSV usage export with `yield_per` | `admin_usage.py` streams but does not use `yield_per` |

### Quality and tests

| Item | Evidence |
|---|---|
| OpenAPI snapshot and backend contract test (and frontend type sync from it) | No `tests/snapshots/` or contract test |
| Platform integration test | `backend/tests/test_platform_integration.py` missing |
| Concurrency correctness test | `test_concurrency_correctness.py` missing |
| End-to-end journeys against a seeded DB (`backend/tests/e2e/`, `scripts/seed_e2e.py`) | Missing; `scripts/smoke_journeys.py` covers sign-in only |
| OWASP verification: one test class per Top 10 row | `test_owasp_compliance.py` covers A01, A03, A04, A07, A08 |
| CI runs the sign-in smoke journey | CI calls `smoke_e2e.py` without `--journey signin` |
| CI perf-smoke: remove the dead "no `--smoke` flag" fallback | `.github/workflows/ci.yml` |
| Coverage gate from 78 % to 80 % | `COV_FAIL_UNDER: "78"` in CI |
| Pay down ignored lint rules (C901, PLR0911, FAST001/FAST002 on about 50 handlers) | `backend/pyproject.toml` |
| Accessibility checks (axe) and visual-regression baselines | None in the repo |
| Remove `@playwright/test` (Playwright was ruled out) | Still in `frontend/package.json` devDependencies |
| Dashboard speed baseline script | `scripts/measure_dashboard_speed.py` missing |

### Platform and operations

| Item | Evidence |
|---|---|
| Next.js 14.2.35 -> 15.5.24+ (or 16) and FastAPI 0.115 -> 0.133+ with Starlette 1.x | Advisories sit on CI allow-lists that expire **2026-12-31** (`.github/audit/`); CI fails after that |
| Stable pydantic | `pydantic==2.14.0b2` (pre-release) in `backend/requirements.txt` |
| Split dev tools out of the production image | ruff, pylint, bandit, pip-audit and pytest are in `backend/requirements.txt` |
| `CODEOWNERS` and branch protection on `main` | Not in the repo / not recorded |
| Real Entra go-live sign-in on `askdiqui.ethub.co` | Owner step; see [environment-and-sso.md](deployment/environment-and-sso.md) |
| Performance runs (50 and 100 concurrent, failover, soak) and the admin first-load JS budget | No recorded results |
| Migration rehearsal on a copy of production before the next release | Not recorded |

---

## 4. Decisions needed

| Decision | Context |
|---|---|
| `ALLOW_CLIENT_IP_OVERRIDE` default | Code default is `true` (the website forwards the visitor IP in the `/query` body); any holder of the API key can then choose the stored and rate-limited IP. Keep `true` only while the website needs it. |
| Insights user-type rule | Code uses ">= 60 %"; the reference HTML used plurality |
| Insights taxonomy seed | Needs owner confirmation |
| External logo hosts | The CSP `img-src` blocks logos hosted on other domains; branding assets must be uploaded |

---

## 5. Risks

1. **Latency.** A full answer takes about 10 s; most of it is the LLM. The
   200 ms budget covers retrieval and guardrails only, and even that is
   missed. Streaming (section 3) is the main lever.
2. **Dependency advisories** on the CI allow-list expire 2026-12-31.
3. **Groundedness variance.** `gpt-5-mini` only accepts `temperature=1.0`,
   and there is one generation retry, so an unlucky answer can fall back.
4. **Off-topic gate** must stay on the cosine score, never the reranked score
   (`MIN_RETRIEVAL_SCORE=0.15`).
5. **Postgres connection budget** is shared with other et-platform apps:
   `replicas x workers x (DB_POOL_SIZE + DB_MAX_OVERFLOW) <= max_connections - 20`.
6. **Deploy safety.** Never ship compose files, nginx confs or `.env` files to
   the server (the 2026-10-16 outage). The entrypoint applies every pending
   migration, so the pre-deploy backup is mandatory.
7. **Per-process state.** Some caches and counters (audience and summary
   caches, metrics) live in one process and reset on restart.
8. **Pricing.** Cached-input pricing defaults to 10 % of input; check it
   against the Azure rate card (now editable in Settings > Models & Pricing).
9. **Audit table** is append-only; demo-data purges must never bypass the
   immutability trigger.
10. **Breaking API changes already shipped**: 422 `detail` is a string,
    `trace_id` was removed, `service` is a category.

Deferred on purpose: MMR diversity, reranker warm-up inference, retracting a
feedback rating, Redis, Playwright, Lighthouse.

---

## 6. Completed

Everything below exists in the code (checked 2026-10-20).

- **RAG pipeline**: structure-aware chunking; pgvector recall with dedupe and
  a cross-document quota; rule-based query decomposition; cross-encoder rerank
  (reorder only, loaded at startup); pre-checks (length, spam, prompt
  injection); semantic groundedness post-check with a generation retry;
  grounded fallback; token-budgeted prompts; per-stage latency and token
  accounting (`embedding_ms` split out).
- **Ingestion**: canonical Markdown only, server-side `.docx` conversion with
  upload security checks, hash-gated upserts under an advisory lock, PII
  redaction with Australian detectors, reconcile and rescan scripts.
- **Evaluation**: offline harness (retrieval, answer, citation, abstention,
  safety, robustness, operational metrics) and sampled online evaluation.
- **Auth**: Entra ID OIDC with PKCE, server-side sessions, RBAC
  (superadmin, admin, operator; pending and suspended states), access
  requests, user management API and page, mock IdP for development,
  `verify_sso_config.py`.
- **Platform**: security headers and nonce CSP; RFC 9457 errors; circuit
  breaker and timeouts; Postgres job queue (`SKIP LOCKED`) and worker; shared
  state (rate limits, OIDC state) and LISTEN/NOTIFY event bus; gzip, ETag and
  SSE; async-only and 50-concurrency gates; connection-release fixes.
- **Admin console**: interactive dashboard with precomputed snapshots and live push; Visitors & Conversations
  with detail pages and deep links; Usage & Cost tabs with inspector and test
  IPs; Feedback triage workspace (review queue, golden cases, preference data,
  knowledge gaps); Evaluation; Governance (overview, monitoring, traces, logs,
  live stream, CSV export); Analytics (insights classifier and taxonomy);
  User Management; Settings (Branding, Insights Taxonomy, Models & Pricing);
  light and dark themes; chart kit; guided tours; AskDIQ chat widget.
- **Delivery**: CI (lint, tests with Postgres, coverage, audits, build,
  full-stack smoke, perf smoke), Dependabot, PR template, deploy kit, server
  deploy/rollback scripts, autoheal watchdog, production deployment guide.
