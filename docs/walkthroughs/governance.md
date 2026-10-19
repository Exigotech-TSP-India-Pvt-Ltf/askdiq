# Governance

> System health, pipeline latency, per-request traces and the audit log in one page with four tabs, kept current by a live stream.  Route: `/admin/governance` (tabs via `?tab=`) · Nav: **Governance** · Permission: `dashboard:read` (operator, admin, superadmin)

## Purpose and who uses it

Governance is where you check whether the assistant is healthy and find out why a single answer was
slow, blocked or wrong.

- **Operators and support** watch the **Overall** tab for the health banner and recent incidents. They
  open a trace from a user's complaint ("the answer took ages") and read the stage waterfall.
- **Admins and analysts** use **Monitoring** for latency percentiles, the outcome mix and guardrail
  flags. They use **Logs** to export audit evidence as CSV.
- **Superadmins** also see authentication events in **Logs**. Every other role has them filtered out on
  the server (see [Logs tab](#logs-tab)).

Every endpoint on this page requires `dashboard:read`, and nothing on the page writes data. The one
side effect is the CSV export, which writes an audit row.

Source: `frontend/app/(app)/admin/governance/page.tsx`, `backend/app/api/routes/admin_governance.py`
(`read_guard = require_permission(Permission.DASHBOARD_READ)` on every route).

## How to get there

- **Sidebar:** **Governance** (shield icon). The item needs `dashboard:read`
  (`frontend/components/Sidebar.tsx`).
- **Command palette** (Ctrl / Cmd + K): type "governance". The item has no extra keywords, so "logs" or
  "traces" do not find it.
- **URL:** `/admin/governance`. The four tabs are one page. The tab lives in the `tab` query parameter:

| `tab` value | Tab | Old route that redirects here |
|---|---|---|
| (absent) or `Overall` | **Overall** | – |
| `Monitoring` | **Monitoring** | `/admin/governance/monitoring` |
| `Traces` | **Traces** | `/admin/governance/traces` |
| `Logs` | **Logs** | `/admin/governance/logs` |

The values are case-sensitive. Any unknown value falls back to **Overall**. The sub-routes in the table
are server redirects (`redirect("/admin/governance?tab=...")`), so old bookmarks still work.

**Deep-link parameters** (`frontend/lib/governance-links.ts`). They are read from the URL and checked
against allowed values; anything else is ignored:

| Param | Tab | Allowed values | Effect |
|---|---|---|---|
| `request_id` | Traces | any request id | Opens the trace drawer for that request |
| `status` | Traces | `ok`, `ungrounded`, `blocked`, `failed`, `unknown` | **Status** filter |
| `outcome` | Traces | `answered`, `ungrounded`, `blocked`, `conversational`, `llm_failed` | **Outcome** filter |
| `latency_bucket` | Traces | `fast`, `moderate`, `slow`, `very_slow` | **Latency** filter |
| `q` | Traces, Logs | free text, trimmed to 200 chars | Search box |
| `level` | Logs | comma list of `CRITICAL`, `ALERT`, `ERROR`, `WARNING`, `INFO` | Level chips |
| `service` | Logs | `auth`, `query`, `ingest`, `feedback`, `admin`, `system` | **Service** filter |
| `at` | Logs | ISO timestamp of an hour start | Shows only that hour (overrides the date range) |

Switching tabs clears all of these (`TAB_PARAMS`). The global date-range params (`range`, `from`, `to`)
and the audience are kept.

Other pages link here:

- **Chat:** **View trace** under an answer (shown only with `dashboard:read`;
  `frontend/components/chat/TraceLink.tsx`).
- **Conversation Logs:** the **Trace** link on each turn of a conversation
  (`frontend/app/(app)/admin/conversations/[id]/page.tsx`).
- The Dashboard activity feed and the Usage & Cost query inspector ("full view").
- Every one of these builds `entityHref({ kind: "trace", id })`, which gives
  `/admin/governance?tab=Traces&request_id=<id>` (`frontend/lib/entity-links.ts`). The **Request**
  column in Logs uses the same link.

## Layout at a glance

```text
+--------------------------------------------------------------------------+
| [shield] Admin Governance         [Overview|Users|Testing]  [Date range*] |
|          <subtitle for the active tab>                                    |
+--------------------------------------------------------------------------+
| [Overall] [Monitoring] [Traces] [Logs]                                   |
+--------------------------------------------------------------------------+
| OVERALL                                              (o) Live . 10:42:05 |
| +----------------------------------------------------------------------+ |
| | (v) All systems healthy           [Error rate][p95 latency][Azure q.] | |
| +----------------------------------------------------------------------+ |
| [Requests ] [Error rate ] [Latency p50] [Latency p95]                    |
| [Active u.] [Groundedn. ] [Blocked/fl.] [Cost today ]                    |
| Status timeline   (2) (1)                                                |
| ##########.....###########################  24 hourly cells             |
| +-- Recent incidents ----------------------+ +-- At a glance ---------+  |
| | [CRITICAL] summary ... service . action  | | Traces          12,345 |  |
| | [ALERT]    summary ...                   | | Logs            98,765 |  |
| +------------------------------------------+ +------------------------+  |
+--------------------------------------------------------------------------+
  * The date range picker shows on Traces and Logs only.
```

> Screenshot placeholder: `img/governance-overall.png` — Overall tab: health banner with three signal chips, eight KPI cards with sparklines, status timeline with one incident marker, Recent incidents and At a glance.

> Screenshot placeholder: `img/governance-monitoring.png` — Monitoring tab: 4 x 2 stat tiles, Pipeline latency bars, Stage percentiles band, Latency over time, Requests and errors, resource gauges.

> Screenshot placeholder: `img/governance-traces-drawer.png` — Traces tab with the trace drawer open showing the Stage waterfall and Sources.

> Screenshot placeholder: `img/governance-logs.png` — Logs tab with the level chips (counts), Service filter, an "Hour of" chip and one row with **Show event detail** expanded.

## Layout tour

### Page header and tabs

- Title **Admin Governance**. The subtitle changes with the tab:
  - Overall: "Health at a glance: status, KPIs, incidents"
  - Monitoring: "Pipeline latency percentiles, traffic, resources and quality, live"
  - Traces: "Request journeys across the RAG pipeline"
  - Logs: "Audit, security and pipeline events"
- **Audience tabs** (Overview / Users / Testing) apply to all four tabs. The client sends nothing for
  `overview`, and `audience=users|testing` otherwise. The backend classifies client IPs against the
  test-IP rules (`backend/app/governance/audience.py`). An invalid value returns `422`.
- **Date range picker** shows on **Traces** and **Logs** only (`RANGED_TABS`). Overall and Monitoring
  always cover a fixed 24-hour window. The picker ignores the date range.
  - Presets are **Last 24 hours**, **Last 7 days** (default), **Last 30 days** and **Last 90 days**.
  - A preset sends only `from` (open-ended), so auto-refresh keeps picking up new rows.
  - A custom range sends `from` plus `to` = the end of its last day (browser local time), converted to
    UTC (`rangeBounds` in `governance-links.ts`).
- Tab bar: **Overall**, **Monitoring**, **Traces**, **Logs** (`GOVERNANCE_TABS`). The tab you pick is
  pushed onto browser history, so Back returns to the previous tab.

### Timezones (applies to every tab)

- The backend stores and buckets everything in **UTC**. Hourly buckets are UTC hours
  (`date_trunc('hour', created_at)`). "Today" for **Cost today** is the **UTC** day.
- The browser shows times in **your local timezone**. It reads timestamps as UTC (`parseServerDate`) and
  formats them with `Intl.DateTimeFormat("en-AU")`: hours as `HH:mm`, date-times as `16 Oct, 10:42:05`.
- If your timezone is offset by a non-whole number of hours (for example India, UTC+5:30), hourly
  labels show as `05:30`, `06:30` and so on, because each bucket is a UTC hour.

---

### Overall tab

Source: `frontend/app/(app)/admin/governance/overall/OverallView.tsx`,
`backend/app/governance/overview.py`, `backend/app/governance/timeseries.py`.

**Data and refresh.** One request, `GET /admin/governance/overview`, fills the whole tab.

- The response has a weak ETag; a repeat request with a matching ETag gets `304`.
- The client caches it for 60 s (`staleTime: 60_000`, no refetch on window focus).
- After the first load, the live SSE stream keeps it current (see [Live stream](#live-stream-sse)).

The **At a glance** totals come from two extra `limit=1` calls to `/traces/page` and `/logs`.

#### Live indicator (top right)

A chip that shows the stream state (`LiveIndicator` in `components/admin/governance/badges.tsx`):

- **Live**: green, pulsing.
- **Connecting…**
- **Reconnecting…**
- **Paused (tab hidden)**
- **Live updates unavailable**: the browser has no `EventSource`.

After the label comes "· <local time>". That is the time of the last stream event or, failing that, the
time of the last fetch.

#### Health banner

This is the `HealthBanner` component. It has a title, a body and three signal chips. The background
animates between status colours.

| Status | Title | Body |
|---|---|---|
| `Healthy` | **All systems healthy** | "Error rate, p95 latency and Azure quota are within their thresholds." |
| `Degraded` | **Service degraded** | "At least one signal crossed its degraded threshold. Check the signals below." |
| `Incident` | **Incident in progress** | "A signal crossed its incident threshold. Recent incidents are listed below." |

The body ends with "Window: last 60 min." (the value comes from config).

There are exactly three states. Nothing is labelled "down": the worst state is **Incident**.

**How it's computed** (`overview.py::health_status`, `classify`):

- Each signal is classified on its own:
  - value `>= incident_at` → `Incident`
  - else value `>= degraded_at` → `Degraded`
  - else, or if the value is null (no data), → `Healthy`
- The banner status is the **worst** of the three signals.
- Signal chips are labelled **Error rate**, **p95 latency** and **Azure quota used**. Each chip shows
  its value and "degraded ≥ X · incident ≥ Y".

| Signal | Value | Window | Degraded at | Incident at | Setting (env) |
|---|---|---|---|---|---|
| **Error rate** | (failed assistant turns + `server_error` audit rows) ÷ (assistant turns + `server_error` rows) × 100. Failed means outcome `llm_failed` or `empty_llm_output`. | last `GOVERNANCE_HEALTH_WINDOW_MINUTES` (60 min) | 2 % | 10 % | `GOVERNANCE_HEALTH_ERROR_RATE_DEGRADED_PCT` / `_INCIDENT_PCT` |
| **p95 latency** | `percentile_cont(0.95)` of `latency_metrics.total_ms` | last 60 min | 8 000 ms (shown "8 s") | 20 000 ms ("20 s") | `GOVERNANCE_HEALTH_P95_DEGRADED_MS` / `_INCIDENT_MS` |
| **Azure quota used** | worst of (100 − TPM headroom %) and (100 − RPM headroom %) across the chat and embedding deployments | Azure rolling 60 s window, **this API process only** | 80 % | 95 % | `GOVERNANCE_HEALTH_QUOTA_DEGRADED_PCT` / `_INCIDENT_PCT` |

- Error rate and p95 latency are audience-scoped. Quota is not: it is process-local telemetry
  (`quota_series.py::utilization_pct`).
- With several API workers or replicas, each process sees only its own quota window. Two requests can
  therefore get different banners. **Not verified** in a multi-replica deployment.

#### KPI cards (8)

Each card shows a value, a 24-point hourly sparkline and a caption in the form "<window> · <source>".
The caption is built by `windowSourceLabel`. Each card links to the rows behind it.

All cards share these rules:

- The window starts at the beginning of the UTC hour 23 hours ago, so it is 23 h plus the current
  partial hour. Every caption still says "24h".
- The audience filter joins each row to its `query` audit row's IP.

| Card | How it's computed (`overview.py::build_kpis`) | Caption shown | Link |
|---|---|---|---|
| **Requests** | assistant turns (`chat_messages.role='assistant'`) + `server_error` audit rows | "24h · chat turns + audit log" | Monitoring tab |
| **Error rate** | (turns with outcome `llm_failed`/`empty_llm_output` + `server_error` rows) ÷ Requests × 100; 0 when there are no requests | "24h · chat messages.outcome + audit log.server error" | Traces, `status=failed` |
| **Latency p50** | `percentile_cont(0.5)` of `latency_metrics.total_ms` over the window | "24h · latency records" | Monitoring tab |
| **Latency p95** | `percentile_cont(0.95)` of `latency_metrics.total_ms` | "24h · latency records" | Traces, `latency_bucket=slow` |
| **Active users** | `COUNT(DISTINCT chat_messages.ip_address)` over **assistant** rows. See the bug note below. | "24h · chat messages.ip address" | `/admin/usage?tab=users` |
| **Groundedness** | turns with `grounded = true` ÷ turns where `grounded` is not null × 100; "—" when no turn has a value | "24h · chat messages.grounded" | Traces, `status=ungrounded` |
| **Blocked or flagged** | turns whose derived status is `blocked` **or** whose `guardrail_flags` array is non-empty | "24h · chat messages.outcome + guardrail flags" | Traces, `status=blocked` |
| **Cost today** | `SUM(chat_messages.cost_usd)` of assistant turns since **00:00 UTC today**. The sparkline is hourly cost over the full 24 h. | "24h · chat messages.cost usd" | `/admin/usage?tab=queries` |

Number formats (`governance-format.ts`):

- Percentages: one decimal ("2.4%").
- Latency: ms below 1 s, then seconds ("850 ms", "1.2 s").
- Cost: `$0.0123` (4 decimals below $1), `$12.34` otherwise.
- Counts: `en` grouping, compact from 10 000 ("12.3K").

**Known issue (Active users):** the IP is stored only on the **user** message row, not the assistant
row (`backend/app/core/sessions.py::record_exchange`). As a result this card counts no IPs from rows
written by the current code and reads 0. See [Known limitations](#known-limitations-and-in-flight-changes)
for the fix branches.

#### Status timeline

Heading **Status timeline**, with the text "Hourly error rate against the health thresholds, last 24 h.
Markers are incidents: select one to open Logs for that hour."

- There are 24 cells, one per UTC hour, oldest first. Each cell is coloured by that hour's error rate
  (same formula as the KPI) against the **error-rate** thresholds:
  - green: Healthy
  - amber: Degraded
  - red: Incident
  - grey: no traffic, that is 0 requests and no incidents
- An hour that contains a `CRITICAL` incident is always red.
- An hour with incidents gets a red round marker with the count ("9+" above 9). The marker links to
  Logs with `level=CRITICAL,ALERT` and `at=<that hour>`.
- Hovering a cell shows "HH:mm: <state>, error rate X%, N incidents".
- Source: `components/admin/governance/StatusTimeline.tsx::buildTimeline`.

#### Recent incidents

Heading **Recent incidents**, with the caption "CRITICAL and ALERT · newest first".

- The rows are audit-log rows whose derived level is `CRITICAL` or `ALERT` (levels are explained under
  [Logs tab](#logs-tab)).
- The server sends rows from the last `GOVERNANCE_INCIDENTS_WINDOW_HOURS` (24 h), at most
  `GOVERNANCE_INCIDENTS_LIMIT` (**10**) rows.
- Each row shows a level badge, the summary, and "service · action · time".
- Each row links to Logs with: that level, `q` = the row's request id (or its action if there is no
  request id), and `at` = the start of the hour in your local time.
- A row that arrives over the live stream is highlighted amber for 4 s (`FRESH_HIGHLIGHT_MS`).
- Empty state: **No incidents**, "No CRITICAL or ALERT events in the incident window."

#### At a glance

| Row | Detail text | Value |
|---|---|---|
| **Traces** | "All recorded request journeys" | `total` from `/traces/page?limit=1`. All time, audience-scoped, "≈ " prefix when estimated. |
| **Logs** | "Audit and security entries, all time" | `total` from `/logs?limit=1`. Excludes auth events unless you are a superadmin. |
| **Incidents** | "CRITICAL / ALERT in the incident window" | number of rows in the incident list |
| **Health window** | "Signals are evaluated over" | "60 min" |
| **Online quality** | "N answers sampled" | "X.X% violations", or "—" |

Online quality comes from the in-process online-evaluation tracker
(`backend/app/governance/online_eval.py::quality_tracker`). It counts from **process start**, is
**not** audience-scoped, and stays empty unless `ONLINE_EVAL_ENABLED` is true (default `false`, sample
rate 10 %).

---

### Monitoring tab

Source: `frontend/app/(app)/admin/governance/monitoring/MonitoringView.tsx`,
`backend/app/api/routes/admin_governance.py::governance_monitoring`, `backend/app/governance/timeseries.py`,
`backend/app/governance/monitoring.py`.

**Data and refresh.** One request, `GET /admin/governance/monitoring`.

- The page has no polling timer. It opens the same SSE stream as Overall and uses each event only as a
  "something changed" signal.
- On a stream event it refetches `/monitoring`, at most once every **5 s** (`MONITORING_REFETCH_MIN_MS`).
- The first `snapshot` event after connecting does not trigger a refetch.
- The stream only emits when the overview changes: health status, a new incident, or a changed KPI
  value. If nothing changes, Monitoring does not refetch, and the CPU / RAM gauges and quota stay as
  they were. Switch tabs or reload to force fresh data.

The intro line reads: "Rolling 24 h snapshot. Latency is measured in milliseconds; each count shows its
window and source." The **Live** chip sits on the right.

#### Stat tiles (4 x 2)

Each tile is a link to the rows behind it. The caption under the value is built the same way as on
Overall.

| Tile | How it's computed | Caption | Link |
|---|---|---|---|
| **API requests** | Audience `overview`: the in-memory HTTP request counter of **this process since it started** (`METRICS["requests"]`). Audience users / testing: number of `latency_metrics` rows in the last 24 h. | "since restart · process counter", or "24h · latency records" | Logs tab |
| **Queries** | `latency_metrics` rows with `created_at >= now − 24h` (audience-scoped) | "24h · latency records" | Traces tab |
| **Active users** | `COUNT(DISTINCT chat_sessions.created_ip)` for sessions with `last_activity_at >= now − 24h` (audience-scoped). This definition differs from the Overall card. | "24h · chat sessions" | `/admin/usage?tab=users` |
| **Traces** | all `latency_metrics` rows (audience-scoped), all time | "all time · latency records" | Traces tab |
| **Error rate** | `monitoring.py::persisted_error_counts`: (assistant turns with outcome `llm_failed`/`empty_llm_output` + `server_error` audit rows) ÷ (assistant turns + `server_error` rows) × 100, rolling 24 h. Two decimals. | "N errors · 24h · chat messages.outcome + audit log.server error" | Traces, `status=failed` |
| **Average latency** | mean of `latency_metrics.total_ms`, rolling 24 h (`total_latency_ms`) | "end to end · 24h · latency records" | Traces tab |
| **p95 latency** | `stage_percentiles.total.p95_ms`: `percentile_cont(0.95)` of `total_ms`, rolling 24 h | "N samples · 24h · latency records" | Traces, `latency_bucket=slow` |
| **Active queries** | in-memory gauge of `/query` requests in flight in **this process**, right now | "right now · process counter" | Traces tab |

#### Percentiles: exactly how they are computed

All latencies are milliseconds, stored per request in `latency_metrics`. That table has one row per
`/query`, with columns `total_ms`, `embedding_ms`, `retrieval_ms`, `reranker_ms`, `llm_ms`,
`guardrail_pre_ms` and `guardrail_post_ms`.

- **Function:** PostgreSQL `percentile_cont` (continuous, linear interpolation between the two nearest
  samples) for p50, p95 and p99. It is one ordered-set aggregate per column, in a single SQL statement
  (`timeseries.py::stage_percentiles`).
- **Samples:** every row in the window where that column is not null. A stage that did not run, such
  as the reranker when it is disabled, is null and not counted. `sample_count` is the number of
  non-null values.
- **Empty stage:** if a stage has 0 samples, all of its percentiles are `null` (never `0`). The UI then
  says "no data".
- **Window for the per-stage table, the band and the p95 tile:** rolling, `created_at >= now − 24h`
  (UTC).
- **Window for the hourly charts:** 24 UTC hour buckets, from the start of the hour 23 hours ago through
  the current partial hour. It uses `GROUP BY ROLLUP(date_trunc('hour', created_at))`, which also
  returns the whole-window total in the same statement. Empty hours are zero-filled: `requests = 0`,
  latency `null` (`timeseries.py::latency_rollup`, `hourly_series`).
- **Audience:** a `latency_metrics` row belongs to an audience through its `query` audit row's client
  IP (`scope_latency`). With audience `overview`, no join is made.
- Every percentile is computed over raw rows, not from averages. The 24 h p95 is therefore **not** the
  average of the hourly p95s.

#### Pipeline latency (chart)

- Title **Pipeline latency**, subtitle "Average per stage", window badge "24h".
- Bars show the **mean** of each stage column over the rolling 24 h. Stages appear in this order:
  **Embedding**, **Retrieval**, **Reranker**, **LLM**, **Guardrail (pre)**, **Guardrail (post)**.
- A stage without samples gets the axis label "<Stage> (no data)".
- **View as table** shows the columns Stage / Average / Samples.
- Empty state: "No stage timings recorded in this window".
- Computed in Python over the loaded rows (`monitoring.py::stage_latency_summary`).

#### Stage percentiles (chart)

- Title **Stage percentiles**, subtitle "p50 to p95 band with dashed p99, per stage".
- One point per stage with a shaded p50–p95 band and a dashed p99 line.
- Stages with no p50 are listed below as "No data: …".
- **View as table** shows the columns Stage / p50 / p95 / p99 / Samples.
- Empty state: "No stage samples in this window".

#### Latency over time (chart)

- Title **Latency over time**, subtitle "End-to-end p50 to p95, hourly".
- Shows the hourly `total_ms` p50 and p95 per UTC hour (labels in local time). Hours without samples are
  left out.
- **View as table** shows the columns Hour / p50 / p95.
- Empty state: "No data for this period".

#### Requests and errors (chart)

- Title **Requests and errors**, subtitle "Assistant turns plus server errors, hourly".
- A stacked area with two series:
  - **Succeeded** = requests − errors
  - **Errors** (red)
- Per hour, requests = assistant turns + `server_error` rows, and errors = failed turns + `server_error`
  rows.
- **View as table** shows the columns Hour / Succeeded / Errors.

#### Resources and Azure quota

- Heading **Resources and Azure quota**, with the caption "This API process · Azure rolling 60 s window".
  When the backend reported HTTP 429 responses from Azure in the last 60 s, " · N throttled (429)" is
  added.
- There are four radial gauges:

| Gauge | Value | Amber at | Red at |
|---|---|---|---|
| **CPU** | `psutil.cpu_percent(None)` | 70 | 90 |
| **RAM** (caption "RAM · N MB") | `psutil.Process().memory_percent()`; MB = process RSS | 75 | 90 |
| **Azure TPM used** | 100 − the lower TPM headroom of chat / embedding | 70 | 90 |
| **Azure RPM used** | 100 − the lower RPM headroom of chat / embedding | 70 | 90 |

- If `psutil` is unavailable, the gauges are empty.
- `psutil.cpu_percent(None)` measures **system-wide** CPU since the previous call, not just this
  process, although the caption says "This API process".
- These gauges ignore the audience filter.

#### Guardrail flags and Outcome mix

**Guardrail flags**

- Subtitle "Flags raised on assistant turns", window "24h".
- Counts each flag in `chat_messages.guardrail_flags` across assistant turns in the rolling 24 h. A turn
  with two flags counts once for each flag.
- Bars are sorted by count, with "(N%)" of all flags.
- **Every** row links to Traces with `status=blocked`, even quality flags such as
  `low_retrieval_confidence`, whose turns usually have status `ungrounded`.
- Empty state: "No guardrail flags in this window."
- Flags come from `backend/app/guardrails`:
  - `pre_checks.py`: `unsafe_input_detected`, `spam_detected`, `query_too_long`, `empty_query`
  - `post_checks.py`: `groundedness_check_failed`, `possible_hallucination`,
    `unsupported_numeric_claim`, `low_retrieval_confidence`, `no_retrieved_sources`, `empty_answer`
  - Other flags appear too, such as `llm_call_failed`, `secret_leak_detected` and `pii_leak_detected`.

**Outcome mix**

- Subtitle "Assistant turns by outcome".
- Shows each turn's outcome normalised through `LEGACY_OUTCOME_MAP`: `fallback` → `ungrounded`,
  `empty_llm_output` → `llm_failed`. An unmapped or missing outcome shows as **Unknown**.
- Each known outcome links to Traces with that `outcome`. **Unknown** has no link.
- Empty state: "No assistant turns in this window."

#### Online quality

- Heading **Online quality**, text "Sampled online evaluation of live answers, since process start."
- Six mini stats: **Sampled**, **Violation rate**, **Mean faithfulness** ("X.XX / 5"), **Unfaithful**,
  **PII leaks**, **Secret leaks**.
- The values are process-local and not audience-scoped.
- **Mean faithfulness shows 5.00 when nothing has been sampled.** That is the default in `get_kpis`, not
  a measurement.

#### AI Usage panel

Heading **AI Usage**, text "Model usage from the usage records.", with two inner tabs:
**LLM Usage** and **Embedding Usage**.

- This panel calls the older `GET /admin/usage/llm` and `GET /admin/usage/embedding`
  (`backend/app/api/routes/admin.py`).
- They ignore the audience filter and use **mixed windows**, so the numbers do not reconcile with the
  rest of the tab.

LLM Usage:

| Stat | Definition |
|---|---|
| **Requests** | `latency_metrics` rows in the last 30 days |
| **Successful** | Requests − Failed |
| **Failed** | all-time count of exchanges with `grounded = false`. These are ungrounded answers, not LLM call failures. |
| **Average Latency** | mean `llm_ms`, last 30 days (rounded to whole ms) |
| **Input Tokens** / **Output Tokens** / **Total Tokens** | all-time sums. Actual Azure usage where it was recorded, otherwise a length-based estimate. |
| **Success Rate** | computed in the browser: Successful ÷ Requests |

Embedding Usage:

| Stat | Definition |
|---|---|
| **Dimensions** | the `EMBEDDING_DIM` setting |
| **Documents** / **Chunks** | table counts |
| **Requests** | user messages + chunks, all time |
| **Input Tokens** | estimate: chunk characters ÷ 4 + estimated tokens of user messages |
| **Average Latency** | mean `embedding_ms`, last 30 days |

---

### Traces tab

Source: `frontend/app/(app)/admin/governance/traces/TracesView.tsx`,
`frontend/components/admin/governance/TraceDrawer.tsx`, `backend/app/governance/traces.py`.

A **trace** is one `latency_metrics` row, which means one `/query` request, keyed by `request_id`. Two
other records are attached to it with `LEFT JOIN LATERAL … LIMIT 1`:

- the assistant turn (outcome, flags, tokens, cost, conversation id)
- the `query` audit row (user, client IP, redacted query, sources)

Because of `LIMIT 1`, duplicate rows never multiply a trace.

#### Request traces table

Card title **Request traces**, with the description "Every /query request with its derived status,
latency, tokens and cost. Select a row for the stage waterfall." A badge shows the total, for example
"1,234 traces", with "≈ " when estimated.

Toolbar:

- Search: "Search query text or request ID…". Debounced 300 ms. It matches case-insensitively
  (`ILIKE`) against the redacted query text in the audit row and the request id.
- **Status**: **All statuses**, **Ok**, **Ungrounded**, **Blocked**, **Failed**, **Unknown**.
- **Outcome**: **All outcomes**, **Answered**, **Ungrounded**, **Blocked**, **Conversational**,
  **Llm failed**.
- **Latency**: **Any latency**, **Fast**, **Moderate**, **Slow**, **Very slow**.
- "Range: <preset label>": the active global date range, applied to `latency_metrics.created_at`
  (inclusive `to`).

How **status** is derived (`monitoring.py::derive_trace_status`, mirrored in SQL by `traces.py::status_sql`):

1. `failed`: the outcome is `llm_failed` / `empty_llm_output`, or the flags include `llm_call_failed`
   or `empty_llm_output`.
2. `blocked`: the outcome is `blocked`.
3. `ungrounded`: the outcome is `ungrounded` or `fallback`.
4. `ok`: the outcome is `answered` or `conversational`.
5. `unknown`: no assistant turn was persisted for the request, for example an error before generation.
6. Legacy rows that have no outcome fall back to the flags: a block flag gives `blocked`, a groundedness
   flag gives `ungrounded`, anything else gives `ok`.

**Latency buckets** (`GOVERNANCE_LATENCY_BUCKETS_MS`, default `[1000, 3000, 10000]`):

| Bucket | `total_ms` range | Pill colour |
|---|---|---|
| **Fast** | < 1 000 | green |
| **Moderate** | 1 000 – < 3 000 | blue |
| **Slow** | 3 000 – < 10 000 | amber |
| **Very slow** | ≥ 10 000 | red |

Table columns:

| Column | Content |
|---|---|
| **Request** | first 12 characters of `request_id`. Click to open the drawer. |
| **Time** | local date-time of the `latency_metrics` row |
| **User** | the audit row's actor (email), first 12 characters |
| **IP** | client IP from the `query` audit row |
| **Query** | redacted query text, at most 200 characters, truncated in the cell |
| **Status / outcome** | status badge, plus the raw outcome underneath |
| **Latency** | `total_ms` in a pill coloured by its bucket |
| **Slowest stage** | the stage with the largest duration, for example "LLM · 2.4 s" |
| **Tokens** | `chat_messages.total_tokens` |
| **Cost** | `chat_messages.cost_usd` |

Paging and states:

- 50 rows per page (`TRACES_PAGE_SIZE`; the backend allows at most 200). Paging uses a keyset cursor on
  `(created_at, id)`, newest first.
- The pager reads "Showing N traces" and has **Newer** / **Older** buttons.
- The total is exact up to `GOVERNANCE_EXACT_COUNT_CAP` (100 000). Above that it is the Postgres
  planner estimate, shown with "≈ ".
- **Auto-refresh off / on / paused** toggle: when on, the page refetches every 15 s
  (`AUTO_REFRESH_MS`). It runs only on the first (newest) page and only while the browser tab is
  visible; otherwise it shows **paused**.
- Empty: "No request traces match these filters."
- Error: "Couldn't load traces." with a retry button.

#### Trace drawer and waterfall

Clicking a row or its **Request** id sets `request_id` in the URL (pushed onto history) and opens a
right-hand drawer. The drawer calls `GET /admin/governance/traces/{request_id}`.

Drawer header:

- "Trace <request id>" with a **Copy request id** button.
- Subtitle "<local date-time> · <total latency>".
- The status badge.
- **Previous record** / **Next record** buttons. They move through the rows on the **current page**
  only.
- Escape or the backdrop closes the drawer (focus is trapped while it is open).

Drawer body:

- **Fields:**
  - **Outcome**
  - **Total latency**
  - **Slowest stage**
  - **Tokens**
  - **Cost**
  - **Grounded** (Yes / No / —)
  - **User**
  - **Client IP**
  - **Time**
- **Conversation:**
  - **Open conversation** links to `/admin/conversations/<conversation_id>`.
  - Otherwise: "No conversation was persisted for this request."
- **Query (redacted):** the redacted query text.
- **Stage waterfall:** see below.
- **Sources (N):** retrieved chunks, each with its document name and "score 0.873".
  - A chunk with a document id links to the chunk inside that document.
  - Missing document names are filled in from the `chunks` / `documents` tables.
  - Empty: "No sources were retrieved."
- **Flags:**
  - the guardrail flags as amber chips
  - empty: "No guardrail flags."

**Stage waterfall** (`traces.py::waterfall`, `TraceDrawer.tsx::waterfallSteps`):

- **Stages, in order:** **Guardrail (pre)** → **Embedding** → **Retrieval** → **Reranker** → **LLM** →
  **Guardrail (post)**.
- **Duration:** each bar's length is that stage's recorded duration in ms.
- **Start offset:** the backend **assumes the stages ran one after another**. Each stage starts where
  the previous ones ended (a cumulative sum). These are not measured start times, so any overlap or
  concurrency would not show.
- **Missing stages:** a stage with a null duration is left out of the chart and listed under it as
  "No data: Reranker, …". The offset does not advance for it.
- **Other:** a final bar (blue/info) = `total_ms` − sum of stage durations, when that is above 0. It is
  time spent outside the timed stages: request handling, persistence, serialization and so on.
- **Empty state:** "No stage timings were recorded."

Drawer errors:

- `404`: **Trace not found**, "It may be outside the selected audience." Unknown ids and ids that belong
  to a different audience get the same answer.
- Other errors: **Couldn't load this trace** with a retry button.
- The request id must match `^[A-Za-z0-9._:\-]{1,64}$`. Anything else gets `422`.

---

### Logs tab

Source: `frontend/app/(app)/admin/governance/logs/LogsView.tsx`, `backend/app/governance/logs.py`,
`backend/app/governance/log_summary.py`.

The rows are entries of the `audit_log` table. **Level** and **Service** are not stored: they are
derived from each row's action and detail, both in Python (for display) and in a mirrored SQL `CASE`
(for filtering and counting).

Card title **Governance logs**, with the description "Audit, security and pipeline events, filtered and
paged on the server." The badge shows "N entries" (with "≈ " above 100 000).

#### Level chips

There are five toggle chips: **Critical**, **Alert**, **Error**, **Warning**, **Info**.

- Each chip shows a count. "–" means not loaded yet; a "≥" prefix means the count was capped.
- Selecting several chips ORs them together.
- The chip counts ignore the level filter itself but respect every other filter. They are computed over
  at most the newest 100 000 matching rows (`level_counts_capped`).

How a level is assigned (`log_summary.py::level_for`). The first rule that matches wins:

| Rule | Level |
|---|---|
| `detail.guardrail_flags` contains `llm_call_failed`, `groundedness_check_failed` or `secret_leak_detected` | **CRITICAL** |
| … contains `unsafe_input_detected`, `spam_detected`, `pii_leak_detected` or `online_unfaithful` | **ALERT** |
| … contains `empty_llm_output`, `empty_answer`, `no_retrieved_sources` or `unsupported_numeric_claim` | **ERROR** |
| … contains `low_retrieval_confidence`, `possible_hallucination`, `query_too_long` or `empty_query` | **WARNING** |
| `detail.grounded` is exactly `false` | **WARNING** |
| action is `server_error` or `llm_call_failed` | **ERROR** |
| action is `llm_retry` or `rate_limited` | **WARNING** |
| action is `negative_feedback_spike` | **ALERT** |
| anything else | **INFO** |

**CRITICAL** and **ALERT** rows are the "incidents" shown on Overall.

#### Hour chip

When `at` is in the URL, for example after clicking an incident, a chip "Hour of HH:mm" appears with a
**Clear hour filter** button. While it is set, the date range is replaced by `[at, at + 1 h)`, and the
"Range: …" label is hidden.

#### Search and Service filter

- Search: "Search summary, action or request ID…". Debounced 300 ms, at most 200 characters. It matches
  case-insensitively against the **action**, the **raw `detail` JSON as text** and the **request id**.
  The generated summary sentence itself is not searched (see limitations).
- **Service**: **All services**, **Authentication**, **Query pipeline**, **Ingestion**, **Feedback**,
  **Admin**, **System**. The mapping lives in `log_summary.py::SERVICE_BY_ACTION`:
  - actions starting with `auth`, `login`, `logout`, `token` or `register` → Authentication
  - unknown actions → System
- **Clear all** clears the level chips and the hour filter (`onClearAll`).

#### Table

| Column | Content |
|---|---|
| **Time** | local date-time (`created_at`, stored UTC) |
| **Level** | level badge |
| **Service** | service label |
| **Event** | one-line summary; raw action underneath in mono; **Show event detail** / **Hide event detail** expands the pretty-printed JSON (with a copy button) |
| **User** | `actor` |
| **Client IP** | `ip_address` |
| **Request** | first 8 characters of `request_id`, linking to the trace drawer |

Paging and states:

- 50 rows per page. The backend default is 200, and 200 is also its maximum.
- Keyset cursor on `(created_at, id)`, newest first, with **Newer** / **Older**.
- **Auto-refresh**: same behaviour as Traces (15 s, first page only, pauses while the browser tab is
  hidden).
- Empty: "No log entries match these filters."
- Error: "Couldn't load logs." with a retry button.

#### Who sees what

Authentication audit events are hidden from anyone without `audit:auth:read`, which only superadmins
have. The server filters them out (`hidden_actions = AUTH_AUDIT_ACTIONS`), so they are missing from the
rows, the chip counts, the totals and the CSV.

#### Export CSV

The **Export CSV** button downloads `GET /admin/governance/logs/export.csv` with the **current filters**
(levels, service, search, date range or hour, audience).

- **Permission:** `dashboard:read` only. Operators can export too.
- **Format:**
  - UTF-8 with a BOM.
  - Columns: `Created At, Level, Service, Action, Summary, User, IP Address, Request ID, Detail`.
    Detail is a JSON string.
  - Newest first.
  - At most `GOVERNANCE_EXPORT_MAX_ROWS` (100 000) rows.
  - Cells that start with `=`, `+`, `-`, `@`, a tab or CR are prefixed with `'` to block spreadsheet
    formula injection.
- **File name:** `<brand>-governance-logs-<audience>-<YYYYMMDD-HHMMSS>.csv` (UTC stamp).
- **Audit:** each export writes a `governance_logs_export` audit row (service **Admin**, level
  **INFO**) recording the filters.
- **Errors:** a failure shows the toast **Couldn't export the logs** with the server's message.

#### Retention

No code in the backend deletes or archives `audit_log` rows. The only retention setting
(`JOBS_RETENTION_DAYS`) applies to the job queue. As far as the application is concerned, logs are kept
indefinitely. **Not verified:** any database-level retention or backup policy in the deployment.

---

### Live stream (SSE)

Source: `frontend/lib/use-governance-stream.ts`, `backend/app/governance/stream.py`,
`backend/app/core/sse.py`, `frontend/app/api/proxy/[...path]/route.ts`.

**Endpoint:** `GET /admin/governance/stream?audience=…`, which returns `text/event-stream` and requires
`dashboard:read`.

- The browser opens it as a same-origin `EventSource` on `/api/proxy/admin/governance/stream`.
- `EventSource` cannot set headers, so authentication relies on the session cookie. The Next proxy turns
  that cookie into `X-Session` for the backend.

**Event types:**

| Event | When | Payload | What the UI does |
|---|---|---|---|
| `snapshot` | On connect: the latest snapshot if the producer has one, otherwise the producer's first tick | the full overview (same as `GET /overview`) | Overall replaces its data. Monitoring ignores the first one. |
| `health` | The health status changed since the previous tick | `{status, previous, health}` | Overall swaps the banner and chips |
| `incident` | A `CRITICAL` / `ALERT` row that was not in the previous tick's list; SSE `id` = audit row id | one log item | Overall prepends it (deduped by id, kept to 50 in the browser) and highlights it for 4 s |
| `monitoring` | One or more KPI **values** changed | `{kpis: {name: value}}` | Overall updates the card values (sparklines wait for the next full fetch) |

Any event makes Monitoring refetch, at most once every 5 s.

**Server side** (`GovernanceStreamHub`):

- **One producer per audience per API worker**, however many browsers are connected.
- Every `GOVERNANCE_STREAM_INTERVAL_SECONDS` (default **5 s**) the producer computes one overview
  snapshot, compares it with the previous one, and sends only the differences to every subscriber.
- An incident published on the Postgres `NOTIFY` event bus (`TOPIC_GOVERNANCE_INCIDENT`) wakes the
  producer at once, on every replica. A burst of notifications becomes one snapshot.
- The producer stops when its last subscriber disconnects.
- Database load is therefore about one overview computation per audience per worker every 5 s, never
  one per client.
- Each subscriber has a queue of 100 events. A slow client loses its **oldest** queued event rather than
  blocking the producer.
- If a snapshot fails, a warning is logged and the next tick tries again. Clients see no event for that
  tick.

**Wire details** (`core/sse.py::event_stream`):

- The first frame is `retry: 3000` (`SSE_RETRY_MS`).
- After `SSE_HEARTBEAT_SECONDS` (**15 s**) of silence the server sends a `: keepalive` comment line.
  The browser ignores it; it keeps proxies and load balancers from closing an idle connection. The same
  idle check also detects a disconnected client.
- Response headers are `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- `text/event-stream` responses are excluded from gzip (`core/compression.py`).

**Client reconnect** (`useGovernanceStream`):

- On any error the client closes the `EventSource` and reopens it with exponential backoff:
  1 s, 2 s, 4 s, 8 s, 16 s, then 30 s (cap). A successful open resets the delay.
- The server's `retry:` hint is effectively unused, because the client closes the source and runs its
  own backoff.
- While the browser tab is hidden, the stream is **closed** and the chip shows **Paused (tab hidden)**.
  It reconnects as soon as the tab is visible again. Overall and Monitoring each open their own stream,
  and only the active tab is mounted.
- Changing the audience closes the stream and opens a new one.

**Behind the proxy:**

- The Next proxy streams the backend body straight through (`new NextResponse(res.body)`) and passes on
  `content-type` and `cache-control`. It does **not** pass on `X-Accel-Buffering`.
- The repo's nginx config (`infra/scale-out/askdiq-api.conf`) has a dedicated SSE location for paths ending in `/stream` or
  `/events`, with `proxy_buffering off`, `gzip off` and a 1 h read timeout. That location covers the
  API host.
- **Not verified:**
  - whether a reverse proxy in front of the **Next.js** app buffers `/api/proxy/.../stream`. Buffering
    there would delay events until the buffer fills or the 15 s keepalive flushes it.
  - whether closing the browser tab promptly closes the proxy's upstream connection on the current
    base. Disconnects are detected at the next keepalive at the latest. See the in-flight branch below.

## Common tasks

### Investigate a slow answer via the trace waterfall

1. Find the request.
   - From **Chat**, click **View trace** under the answer.
   - From **Conversation Logs**, open the conversation and click **Trace** on the turn.
   - Or go to **Governance → Traces**, set **Latency** to **Slow** or **Very slow**, and search for a
     phrase from the question.
2. In the drawer, check **Total latency** and **Slowest stage**.
3. Read the **Stage waterfall**:
   - A long **LLM** bar points at model latency, so check Monitoring → **Resources and Azure quota** for
     throttling ("N throttled (429)").
   - A long **Retrieval** or **Embedding** bar points at the database or embeddings.
   - A large **Other** bar means time spent outside the timed stages.
4. Check **Sources (N)**. Many sources or low scores often go with an ungrounded or slow answer.
5. Compare with normal behaviour: open **Monitoring → Stage percentiles** (or **View as table**) and
   see whether this request's slowest stage is above that stage's p95.
6. Use **Copy request id** to share the trace. The URL
   `/admin/governance?tab=Traces&request_id=<id>` opens the same drawer for any `dashboard:read` user
   with the same audience selected.

### Watch live traffic

1. Open **Governance → Overall** and keep the browser tab in the foreground. The chip should read
   **Live**.
2. The health banner and KPI values update within about 5 s of a change. New CRITICAL / ALERT events
   slide into **Recent incidents** with an amber highlight.
3. For per-stage latency, switch to **Monitoring**. It refetches at most every 5 s when the stream
   reports a change.
4. For individual requests as they arrive, open **Traces**, stay on the first page, and switch
   **Auto-refresh** on (every 15 s).
5. If the chip stays on **Reconnecting…**, the stream endpoint or the proxy is failing. Reload the page,
   and check the backend logs for `governance_stream_snapshot_failed`.

### Find errors in logs for a time window

1. Open **Governance → Logs**.
2. Set the window:
   - For a whole period, use the date picker (custom range or preset).
   - For one hour, click an incident marker on Overall's **Status timeline**, or a row in **Recent
     incidents**. That sets the "Hour of" chip.
3. Select the **Critical**, **Alert** and **Error** chips. The counts on the chips tell you how many
   rows each level holds before you filter.
4. Optionally narrow by **Service** (for example **Query pipeline**) or search for a request id or an
   action such as `server_error`.
5. Expand **Show event detail** on a row for the raw JSON. Click the **Request** id to open its trace.
6. Click **Export CSV** to save exactly the filtered rows for a report.

### Check why the banner says "Service degraded"

1. Read the three signal chips. The coloured one is the one that crossed its threshold. Each chip shows
   its own thresholds.
2. **Error rate**: open **Traces** with **Status** = **Failed**, or click the **Error rate** KPI card.
3. **p95 latency**: click the **Latency p95** card, which opens Traces filtered to **Slow**.
4. **Azure quota used**: open **Monitoring** and look at the **Azure TPM used** / **Azure RPM used**
   gauges.

## Interactions and states

- **Keyboard:**
  - Ctrl / Cmd + K opens the command palette.
  - In the trace drawer, Escape closes it and Tab cycles within it.
  - The drawer has no arrow-key shortcuts; use **Previous record** / **Next record**.
- **Drill-downs:** every KPI card, stat tile, timeline marker, incident row, guardrail row, outcome row
  and Logs request id is a link. The targets are listed in each section above. Links from Monitoring
  tiles and share bars copy the global range params (`range`, `from`, `to`) onto the target
  (`useCarryHref`). The audience is global and always carries over.
- **Loading:** Overall shows skeleton cards ("Loading Requests" and so on). Charts show their own
  skeletons. Tables show a loading state, and the pager spinner appears while a page is fetching.
- **Errors:**
  - Overall: **Couldn't load the governance overview**, "The overview endpoint did not respond. Try again
    in a moment." with a retry button.
  - Monitoring: **Couldn't load monitoring data**, "The monitoring endpoint did not respond."
  - If data is already on screen, a later failed refresh keeps showing it.
- **Charts:** every chart card has **View as table** / **View as chart** for an accessible table view.
- **Missing data:**
  - "—" means no value. For example, groundedness with no graded turns, or latency with no samples.
  - "no data" / "No data: …" means a stage never ran in the window.
  - A count of 0 is a real zero.
  - If Overall's **Active users** reads 0 while traffic exists, that is the known bug, not a lack of
    users.

## Light and dark theme

Status colours (ok / warn / danger / info) come from the theme tokens. The health banner animates
between token colours, and both themes have matching values. Chart series use the categorical palette
from `useChartTheme`, and the **Errors** series always uses the danger colour. Nothing on this page is
theme-specific beyond that.

## API endpoints used

All paths are under `/admin`. The browser calls them as `/api/proxy/admin/...`.

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/admin/governance/overview` | `dashboard:read` | Overall: health, KPIs, sparklines, incidents (ETag / 304) |
| GET | `/admin/governance/stream` | `dashboard:read` | Live SSE updates (Overall, Monitoring) |
| GET | `/admin/governance/monitoring` | `dashboard:read` | Monitoring tab snapshot |
| GET | `/admin/governance/traces/page` | `dashboard:read` | Traces table, and the Overall "Traces" total (`limit=1`) |
| GET | `/admin/governance/traces/{request_id}` | `dashboard:read` | Trace drawer (waterfall, sources, flags) |
| GET | `/admin/governance/traces` | `dashboard:read` | Legacy array (max 500, `X-Total-Count` header); not used by this page |
| GET | `/admin/governance/logs` | `dashboard:read` (auth events need `audit:auth:read`) | Logs table, level chips, and the Overall "Logs" total |
| GET | `/admin/governance/logs/export.csv` | `dashboard:read` | **Export CSV** (streamed, audited) |
| GET | `/admin/usage/llm` | `dashboard:read` | Monitoring → AI Usage → LLM Usage |
| GET | `/admin/usage/embedding` | `dashboard:read` | Monitoring → AI Usage → Embedding Usage |

Common query params: `audience` (`overview` | `users` | `testing`). Traces and logs also take `limit`
(1–200), `cursor`, `from`, `to` and `q`. An invalid filter, cursor or date range (`from` after `to`)
returns `422`.

## Related pages

- [README.md](README.md): index of walkthroughs
- [shell.md](shell.md): sidebar, audience tabs, date range picker, command palette
- [chat.md](chat.md): **View trace** under answers
- [conversation-logs.md](conversation-logs.md): per-turn **Trace** links; the drawer's **Open conversation** target
- [usage-cost.md](usage-cost.md): token and cost accounting; **Active users** / **Cost today** link targets
- [dashboard.md](dashboard.md): the health pill (reads `/governance/overview`) and the activity feed
- [evaluation.md](evaluation.md): offline and online evaluation (online quality numbers)
- [visitors.md](visitors.md): test-IP rules that define the audiences
- [user-management.md](user-management.md): roles, and who can see auth events
- [documents.md](documents.md), [feedback.md](feedback.md), [analytics.md](analytics.md), [settings.md](settings.md), [login.md](login.md)

## Known limitations and in-flight changes

**Limitations and inconsistencies found in code**

- **Overall "Active users" is 0 for live traffic.** It counts distinct `ip_address` on assistant rows,
  but only user rows store the IP.
- **"Active users" means different things on Overall and Monitoring.** Overall counts distinct
  `chat_messages.ip_address` over assistant rows; Monitoring counts distinct
  `chat_sessions.created_ip` by `last_activity_at`.
- **"24h" captions are approximate.**
  - Overall's window starts at the top of the UTC hour 23 hours ago.
  - Monitoring's tiles and per-stage percentiles use a rolling `now − 24h`.
  - **Cost today** is captioned "24h" but its value runs from 00:00 UTC.
- **Some numbers are per-process and reset on restart:**
  - **API requests** (audience Overview)
  - **Active queries**
  - CPU / RAM gauges
  - Azure quota and the quota health signal
  - all of **Online quality**
  With several workers, each request reads whichever process served it.
- **CPU gauge is system-wide.** It uses `psutil.cpu_percent(None)`, though the caption says "This API
  process".
- **Mean faithfulness** shows 5.00 when nothing has been sampled. Online evaluation is off by default
  (`ONLINE_EVAL_ENABLED=false`).
- **AI Usage panel:**
  - **Failed** counts ungrounded answers (`grounded = false`), not LLM failures.
  - Requests and latency cover 30 days; tokens and Failed are all-time.
  - It ignores the audience filter.
- **Guardrail flag rows link too narrowly.** Every row links to `status=blocked`, even for flags whose
  turns are `ungrounded` or `ok`.
- **The waterfall assumes the stages ran in sequence.** Start offsets are cumulative, not measured.
- **Logs search does not search the summary.** The placeholder says "Search summary, …", but the server
  searches the action, the raw detail JSON and the request id. Words that appear only in the generated
  summary sentence do not match.
- **Monitoring can show stale gauges.** It refreshes only when the overview stream reports a change, so
  with steady traffic its CPU / RAM / quota gauges can go stale.
- **`azure_quota_series` is unused.** `/monitoring` returns it (60 samples, one per minute), but no chart
  displays it.
- **Incident rows from the stream accumulate.** The server snapshot holds 10 rows, but rows added by the
  stream pile up in the browser to 50 until the next full load.
- **Two hour definitions for incident links.** Incident-row links use the **local** hour start, while
  timeline markers use the **UTC** bucket hour. In timezones with a half-hour offset, these give
  different hour windows.
- **Hard caps:**
  - page size 200 on the backend (50 in the UI)
  - exact totals up to 100 000, then an estimate
  - CSV export at most 100 000 rows
  - recent incidents 10 (config, maximum 100)
  - search text 200 characters
  - legacy `/traces` at most 500
- **No retention** job exists for `audit_log` in the code.
- **No live-incident notifications.** The header notification bell does not yet show governance
  incidents (`TODO(F3/F4)` in `frontend/lib/use-shell-notifications.ts`).

**In-flight changes**

- **Coming in branch `fix/shell-nav-logout-theme-dates`:**
  - Overall's **Active users** card and Monitoring's tile are renamed **Active visitors**, with an info
    tooltip.
  - Both use one shared definition (`app/dashboard/active_visitors.py`): distinct end users with at
    least one query in the period. A visitor is identified by client id, or by IP when the client id is
    missing; test IPs are excluded. The source label is
    "chat_messages.role=user (visitor identity, test IPs excluded)".
  - This fixes the zero on Overall and the mismatch between the two tabs.
  - The **Time** columns on Traces and Logs, and the incident and drawer timestamps, switch to relative
    times ("3h ago") with the full local date-time in a tooltip.
- **Coming in branch `feat/realistic-demo-data`** (commit `734bf26`, "populate dashboard fields the live
  path left empty"):
  - The Overall turn rollup counts active IPs from `chat_sessions.created_ip`, so **Active users** is no
    longer 0. This overlaps with the branch above, which replaces the metric altogether.
  - `query` audit rows also carry `message_id`.
  - The branch also adds a demo seeder that fills every Governance chart with 120 days of synthetic
    traffic. Seeded request ids start with `seed-`.
- **Coming in branch `perf/dashboard-load-speed`:**
  - Monitoring, Traces and Logs become code-split chunks. They are preloaded when the browser is idle,
    and a "Loading tab" skeleton shows if you switch before they arrive.
  - The Logs level-chip query extracts only the flags and grounded fields instead of casting the whole
    `detail` to JSONB (faster chip counts).
  - An unfiltered trace count skips the lateral joins.
  - The AI Usage endpoints aggregate in SQL and cache their results briefly.
  - The numbers do not change.
- **Coming in branch `fix/chat-page-fetch-timeouts`:**
  - The SSE producer gets a per-snapshot timeout (`GOVERNANCE_STREAM_SNAPSHOT_TIMEOUT_SECONDS`, default
    60 s). A stuck query is cancelled and the next tick retries, instead of the stream going silent.
  - `/monitoring` releases its DB connection before its parallel queries.
  - The Next proxy waits at most `BACKEND_TIMEOUT_MS` (60 s) for response **headers**. After that it
    answers `504` "The backend did not respond within N seconds. Please try again."
  - Once headers arrive, the SSE body is never cut off.
  - Reads are retried up to `BACKEND_RETRIES` (2) times on a connection error or a 502/503/504.
  - A browser disconnect is now passed on to the backend, so a closed tab ends its stream immediately.
  - Failed queries back off (1 s up to 60 s) before a mount or focus refetches them again.
