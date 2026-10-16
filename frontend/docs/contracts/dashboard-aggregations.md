# Contract: Dashboard, Usage and Evaluation aggregations

Owner: frontend PR-21 (`feat/ui-dashboard-interactive-charts`). Implemented by backend PR-14
(`feat/dashboard-chart-aggregations`, planchanges4 B7.1 and B7.4).
TypeScript source of truth: `frontend/lib/dashboard-api.ts` and `frontend/lib/usage-insights-api.ts`.
Keep this file and those types in sync.

## 0. Rules for every field below

- **Additive and backward compatible.** Every field the endpoints return today stays, with the same
  name, type and meaning. Every new field is **optional**: the UI renders today's data and an
  honest empty state ("... is not reported by the server") when a field is absent. Never send
  placeholder or sample numbers; omit the field or send `null` as specified.
- **Units.** Durations in milliseconds (`*_ms`, number). Money in US dollars (`*_usd`, number,
  not rounded below 6 dp server side; the UI formats). Ratios and shares are fractions `0..1`
  (`ratio`, `share`, `hit_ratio`, `pass_rate`, `delta`, dimension scores). Exception kept for
  compatibility: the legacy `error_rate` is a percentage `0..100`.
- **Time.** All timestamps ISO 8601 UTC (`2026-10-16T13:00:00Z`). Day buckets are `YYYY-MM-DD`
  (UTC day). Series are ordered oldest first.
- **`null` vs absent vs `0`.** Absent field = not implemented. `null` = implemented, but no
  measurement exists (no data in the bucket, budget not configured, ratio with a zero
  denominator). `0` = measured and zero.
- **Audience.** Every endpoint keeps the existing `audience=overview|users|testing` parameter
  (default `overview`) and applies it to every new field.
- **Guards and caching.** Same guard as the existing route (`require_permission(DASHBOARD_READ)`
  for `/admin/*`). New aggregation endpoints use `gather_scoped` (one session per task) and
  `cached_response` (ETag), per newplan §13.2.

## 1. `GET /admin/dashboard?audience=&from=&to=` (extended, B7.1)

### Query parameters (new)

| Param | Type | Notes |
|---|---|---|
| `from` | ISO 8601 UTC datetime | Inclusive window start. The UI rounds to the minute. |
| `to` | ISO 8601 UTC datetime | Exclusive window end. For custom day ranges the UI sends the next local midnight. |

Both optional; when both are missing the server keeps today's behaviour. `to <= from` or a span
over `usage_max_range_days` (config, default 400) returns **422** (problem+json). The
DateRangePicker presets are 24 h, 7 d (default), 30 d and 90 d; custom ranges are capped at 365
days client side.

### Existing fields (unchanged)

| Field | Type | Meaning today |
|---|---|---|
| `users` | int | Active visitors (all time; see "Active visitors" below) |
| `active_users` | int | Active visitors in the last 24 h (= `active_visitors.dau.value`) |
| `sessions`, `conversations` | int | Chat sessions (all time) |
| `queries` | int | User messages (all time) |
| `feedback` | int | Feedback audit entries (all time) |
| `avg_latency_ms` | number | Mean `total_ms` (all time) |
| `error_rate` | number | **Percent** 0..100 since process start |
| `pipeline` | object | 24 h mean `embedding_ms`, `retrieval_ms`, `reranker_ms`, `llm_ms` (number or null), `reranker_enabled` |
| `governance` | object<string,string> | Display strings (not used by the new UI) |
| `query_trend` | `[{date, queries}]` | 7 daily points |
| `latency_breakdown` | `[{stage, ms}]` | 24 h stage means |

**When `window` is present** (see below) the server declares that the KPI totals above
(`users`, `active_users`, `sessions`, `conversations`, `queries`, `feedback`, `avg_latency_ms`,
`error_rate`) are computed **for the requested window**. Without `window` the UI labels them with
today's windows ("All time", "Last 24 hours", "Since server start").

### New fields (all optional)

| Field | Type | Meaning |
|---|---|---|
| `window` | `DashboardWindow` | Echo of the applied window; its presence means `from`/`to` were honoured |
| `window.from`, `window.to` | ISO datetime | Applied window (`to` exclusive) |
| `window.bucket` | `"hour" \| "day"` | Bucket for every series: `hour` when the window is 48 h or less, else `day` |
| `window.previous_from`, `window.previous_to` | ISO datetime | The equal-length window immediately before, used for `deltas` |
| `cost_usd` | number | Total cost (USD) in the window, for the Cost KPI |
| `sparklines` | `{ [kpi]: SeriesPoint[] }` | One series per KPI key across the window, one point per bucket |
| `deltas` | `{ [kpi]: number \| null }` | `(current - previous) / previous` as a fraction; `null` when previous is 0 |
| `queries_by_outcome` | `OutcomePoint[]` | Per bucket counts per outcome |
| `latency_series` | `LatencyPoint[]` | Per bucket end-to-end percentiles |
| `cost` | `DashboardCost` | Current calendar month (UTC), independent of `from`/`to` |
| `satisfaction` | `DashboardSatisfaction` | Ratings in the window |
| `top_intents` | `TopIntent[]` | At most 8, count descending, from the insights rollup |
| `active_visitors` | `ActiveVisitors` | DAU / WAU / MAU (+ previous equal window and delta) and active now (5 min), as of the request, independent of `from`/`to`; also sent without a window |

**Active visitors** (backend `app.dashboard.active_visitors`, the only place it is computed):
distinct end-user identities with at least one user query in the period. Identity = the session
actor (client id / signed-in user) when present and not the default/anonymous actor
(`default@local`, `anonymous`), else the query's client IP. Queries from test IPs
(`DEV_TEST_IPS` + `test_ip_configs`) are excluded on every audience except `testing`, where only
test traffic is counted. With a window, `users` is active visitors in the window and
`active_users` active visitors in its last 24 h. The same count feeds the Visitors page
(`active_now`, `active_visitors`) and the governance `active_users` KPIs. Counts only: no IPs or
ids are returned.

```ts
ActiveVisitorsPeriod = { value: int; previous: int; delta: number | null; days: int }
ActiveVisitors = { definition: string; active_now: int; active_now_minutes: int;
                   dau: ActiveVisitorsPeriod; wau: ActiveVisitorsPeriod; mau: ActiveVisitorsPeriod }
```

KPI keys: `users`, `active_users`, `sessions`, `queries`, `feedback`, `avg_latency_ms`,
`error_rate` (percent, like the KPI), `cost_usd`.

Types:

```ts
SeriesPoint   = { t: string; value: number | null }            // value null = no data in bucket
OutcomePoint  = { t: string; answered: int; ungrounded: int; blocked: int; conversational: int; llm_failed: int }
LatencyPoint  = { t: string; p50_ms: number | null; p95_ms: number | null; count: int }
DashboardCost = {
  currency: "USD"; month: "YYYY-MM";
  mtd_usd: number;
  forecast_month_end_usd: number | null;   // mtd_usd / days_elapsed * days_in_month; null before day 1 completes
  budget_usd: number | null;               // MONTHLY_COST_BUDGET_USD (new optional setting); null = not set
  days_elapsed: int; days_in_month: int;
}
DashboardSatisfaction = { thumbs_up: int; thumbs_down: int; ratio: number | null }  // ratio = up / (up + down)
TopIntent = { intent: string; label: string; count: int; share: number }           // share of classified queries
```

Outcome keys are the same set as `UsageSummary.quality.outcomes`.

### Example (7-day window, trimmed to 2 buckets)

```json
{
  "users": 150, "active_users": 42, "sessions": 320, "conversations": 320,
  "queries": 1200, "feedback": 85, "avg_latency_ms": 2400.0, "error_rate": 0.5,
  "pipeline": {"embedding_ms": 110.0, "retrieval_ms": 180.0, "reranker_enabled": true, "reranker_ms": 140.0, "llm_ms": 1900.0},
  "governance": {"monitoring": "1,200 requests since start"},
  "query_trend": [{"date": "2026-10-15", "queries": 130}, {"date": "2026-10-16", "queries": 140}],
  "latency_breakdown": [{"stage": "LLM", "ms": 1900.0}],

  "window": {"from": "2026-10-09T13:00:00Z", "to": "2026-10-16T13:00:00Z", "bucket": "day",
             "previous_from": "2026-10-02T13:00:00Z", "previous_to": "2026-10-09T13:00:00Z"},
  "cost_usd": 41.5,
  "sparklines": {"queries": [{"t": "2026-10-15", "value": 130}, {"t": "2026-10-16", "value": 140}],
                 "cost_usd": [{"t": "2026-10-15", "value": 5.5}, {"t": "2026-10-16", "value": null}]},
  "deltas": {"queries": 0.08, "error_rate": 0.5, "cost_usd": -0.1, "feedback": null},
  "queries_by_outcome": [{"t": "2026-10-16", "answered": 120, "ungrounded": 5, "blocked": 2, "conversational": 12, "llm_failed": 1}],
  "latency_series": [{"t": "2026-10-16", "p50_ms": 1860.0, "p95_ms": 4020.0, "count": 106}],
  "cost": {"currency": "USD", "month": "2026-10", "mtd_usd": 96.4, "forecast_month_end_usd": 186.78,
           "budget_usd": 250.0, "days_elapsed": 16, "days_in_month": 31},
  "satisfaction": {"thumbs_up": 68, "thumbs_down": 17, "ratio": 0.8},
  "top_intents": [{"intent": "mfa_reset", "label": "MFA reset", "count": 120, "share": 0.3}]
}
```

## 2. `GET /admin/dashboard/activity?audience=&limit=20` (new, B7.1 live feed)

Separate from the summary so the feed refreshes on its own (the UI polls every 15 s while the tab
is visible) and a slow summary never blocks it. `limit` 1..50, default 20. A 404 / 405 is shown
as "No activity feed on this server".

```ts
ActivityFeed  = { items: ActivityEvent[]; generated_at: string }
ActivityEvent = {
  id: string;
  type: "query" | "feedback" | "error" | "guardrail" | "document" | "admin";
  at: string;                  // ISO UTC, newest first
  summary: string;             // one line, PII-masked server side (no emails, IPs, query text)
  request_id: string | null;
  conversation_id: string | null;
  message_id: string | null;
  document_id: string | null;
}
```

Deep link per event (UI, `lib/entity-links.ts`): feedback with `message_id` ->
`/admin/feedback?message_id=`; else `request_id` -> `/admin/governance?tab=Traces&request_id=`;
else `conversation_id` -> `/admin/conversations/{id}`; else `document_id` -> `/documents/{id}`.

```json
{"generated_at": "2026-10-16T13:00:00Z",
 "items": [{"id": "e1", "type": "query", "at": "2026-10-16T12:59:00Z", "summary": "Query answered in 2.1 s",
            "request_id": "req-123", "conversation_id": "conv-9", "message_id": null, "document_id": null}]}
```

## 3. Health pill: `GET /admin/governance/overview?audience=` (PR-08, unchanged)

Only `health.status` (`"Healthy" | "Degraded" | "Incident"`) is read. Any other value, an error or
a missing endpoint hides the pill.

## 4. `GET /admin/usage/summary` (extended, B7.4)

Existing fields unchanged. New optional fields, scoped by the existing `audience`, `from_date`,
`to_date` parameters:

```ts
cost_by_stage_series = {
  bucket: "hour" | "day";
  points: { t: string; query_embed: number; guardrail_embed: number; prompt: number;
            cached_prompt: number; reasoning: number; visible: number; ingestion: number }[];  // USD per bucket
}
forecast = {
  currency: "USD"; month: "YYYY-MM"; method: "linear";
  mtd_usd: number; forecast_month_end_usd: number | null; budget_usd: number | null;
  daily: { t: "YYYY-MM-DD"; actual_usd: number | null; forecast_usd: number | null }[];
  // one point per day of the month; actual = cumulative MTD (null for future days);
  // forecast = linear projection (null before today); today carries both so the lines join
}
cache = { cached_prompt_tokens: int; prompt_tokens: int; hit_ratio: number | null }  // null when prompt_tokens = 0
```

Stage keys map to the existing `financial.stage_costs`: `query_embed_cost`, `guardrail_embed_cost`,
`prompt_uncached_cost`, `cached_prompt_cost`, `reasoning_cost`, `visible_cost`,
`ingestion_overhead_cost`. The month forecast uses the current UTC month regardless of the range.

## 5. `GET /metrics/evaluation/live` (extended, B7.4)

Existing fields unchanged. New optional fields:

```ts
quality_dimensions = {
  current_run:  { run_id: string; started_at: string; n_cases: int } | null;
  previous_run: { run_id: string; started_at: string; n_cases: int } | null;
  dimensions: { key: string; label: string; current: number | null; previous: number | null }[];  // 0..1
}
pass_rate_trend = { run_id: string; started_at: string; pass_rate: number; n_cases: int }[];  // oldest first, <= 30 runs
```

Suggested dimensions (keys): `retrieval_recall`, `groundedness`, `faithfulness` (judge score / 5),
`citation_precision`, `safety`, `pass_rate`. Without `quality_dimensions` the UI draws the
current run only, from the existing measured fields, and only when `n_cases > 0`.
A pass-rate marker opens `/admin/evaluation?run={run_id}`.

## 6. Deep links produced by the Dashboard (newplan §1)

| Element | Link |
|---|---|
| Active visitors card | `/admin/users` |
| Active visitors (24h) card | `/admin/usage?tab=users` |
| Sessions card | `/admin/usage?tab=sessions` |
| Queries card | `/admin/usage?tab=queries` |
| Feedback card | `/admin/feedback` |
| Average Latency, Error Rate cards, latency chart, health pill | `/admin/governance?tab=Monitoring` |
| Cost card, cost bullet | `/admin/usage` |
| Queries-by-outcome bucket | `/admin/usage?tab=queries&from=YYYY-MM-DD&to=YYYY-MM-DD` |
| Satisfaction gauge | `/admin/feedback` |
| Intent tile | `/admin/analytics?intent={intent}` |

On hover or focus (120 ms dwell) each card warms its target page's first GET:
`/users`, `/usage/summary`, `/feedback/analytics`, `/governance/monitoring` (admin-relative,
audience-scoped).
