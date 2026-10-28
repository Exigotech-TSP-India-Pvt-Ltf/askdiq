# Dashboard

> The admin landing page: eight KPI cards, six charts and a live activity feed for the selected date range and audience.  Route: `/admin` · Nav: **Dashboard** · Permission: `dashboard:read` (operator, admin, superadmin)

## Purpose and who uses it

The Dashboard answers "how is the assistant doing right now, and compared with the period before?". It shows how many people used it, how many questions they asked, how those questions turned out (answered, ungrounded, blocked and so on), how fast and how expensive the answers were, and what people rated them.

- **Admins and analysts** use it to watch trends and spot regressions. Every card and chart links to the page that owns the number, already filtered.
- **Support** uses the **Live activity** feed to jump straight to a failing query trace or a fresh piece of feedback.
- **New team members**: every number here is computed from the live database. The page never shows sample or placeholder numbers. A widget whose data the server does not send shows an empty state instead (for example "Satisfaction is not reported by the server").

All roles with an approved role can open it (`dashboard:read` is granted to operator, admin and superadmin). Users with no approved role are sent to the access-required screen (`frontend/lib/auth-gate.ts`). Every backend endpoint on this page checks `require_permission(Permission.DASHBOARD_READ)`.

## How to get there

- **Sidebar**: **Dashboard** (first item, layout icon). Shown only with `dashboard:read` (`frontend/components/Sidebar.tsx`).
- **URL**: `/admin`. A fresh sign-in by an active user lands on `/` (`postLoginPath`), which redirects to `/admin` when the user has an approved role and to `/chat` otherwise (`frontend/app/page.tsx`).
- **Command palette** (Ctrl/Cmd+K): type "Dashboard". This nav item has no extra keywords.

URL parameters (shared by every admin page, `frontend/lib/range-context.tsx` and `frontend/lib/audience-context.tsx`):

| Param | Allowed values | Default | Effect |
|---|---|---|---|
| `range` | `24h`, `30d`, `90d` (`7d` is the default and is removed from the URL) | `7d` | Rolling preset ending now |
| `from`, `to` | `YYYY-MM-DD` (local calendar days, both required) | none | Custom range. It is clamped: ends are swapped if reversed, `to` is capped at today, and the span is capped at 365 days |
| `audience` | `overview`, `users`, `testing` | `overview`, or the last value saved in `localStorage` key `askdiq.audience_scope` | Audience scope for every widget |

When you click through from a KPI card, the `range` / `from` / `to` params are carried onto the target page (`useCarryHref`, `frontend/lib/entity-routes.ts`). The audience is global, so it follows you without a URL param.

## Layout at a glance

```text
+--------------------------------------------------------------------------+
| [#] Dashboard            [Overview|Users|Testing] [Last 7 days v] [Healthy]|
|     Monitor users, conversations, RAG performance and governance.        |
+--------------------------------------------------------------------------+
| Total Users   | Active Users  | Sessions      | Queries                  |
| 1,234  +8.0%  | 87   -2.1%    | 456   +3.0%   | 2,345  +5.5%             |
| ~~~sparkline~ | ~~~sparkline~ | ~~~sparkline~ | ~~~sparkline~            |
|---------------+---------------+---------------+--------------------------|
| Feedback      | Average Latency | Error Rate  | Cost                     |
+--------------------------------------------------------------------------+
| Queries by outcome (stacked area, 2/3)       | Satisfaction (gauge)      |
+----------------------------------------------+---------------------------+
| Latency p50 / p95 (band, 2/3)                | Cost vs budget (bullet)   |
+----------------------------------------------+---------------------------+
| Top intents (treemap) | Live activity (feed) | RAG pipeline (stages)     |
+--------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/dashboard-overview.png` — full Dashboard on "Last 7 days", Overview audience, health pill visible, all eight KPI cards with deltas and sparklines.

> Screenshot placeholder: `img/dashboard-24h.png` — same page on "Last 24 hours", showing hourly buckets on the charts.

Below the `xl` breakpoint, the chart rows collapse to one column and the KPI grid drops to two columns (`sm`) or one.

## Layout tour

### Header: audience tabs, date range and health pill

- **Title / subtitle**: **Dashboard**, "Monitor users, conversations, RAG performance and governance."
- **Audience tabs** (`AudienceScopeTabs`): **Overview** ("Complete platform aggregate including all end users and internal tests"), **Users** ("Authentic production usage filtered to exclude configured test IPs") and **Testing** ("Internal benchmarking, evaluation runs, and queries originating from test IPs"). The tooltip on each tab shows counts from `GET /admin/usage/audience-counts`.
  - **Overview** applies no filter.
  - **Users** keeps rows whose client IP is known and not a test IP.
  - **Testing** keeps rows whose IP is a test IP **or unknown (NULL)**.
  - Test IPs are `DEV_TEST_IPS` from config, plus the `test_ip_configs` table, plus built-in loopbacks (`127.0.0.1`, `::1`, `localhost`, `0.0.0.0`, `unknown`, any `127.*`). See `backend/app/governance/audience.py::build_audience_filter`.
  - The classification is cached in-process for `AUDIENCE_CACHE_TTL_SECONDS` (default 30 s) and is cleared immediately when a test IP rule changes.
- **Date range picker** (`DateRangePicker`): the button shows the range label. The panel ("Choose date range") lists the presets **Last 24 hours**, **Last 7 days**, **Last 30 days** and **Last 90 days**, then a **Custom range** form with **From** / **To** date inputs and an **Apply range** button.
- **Health pill**: shows **Healthy**, **Degraded** or **Incident** and links to `/admin/governance?tab=Overall`.
  - Source: `health.status` of `GET /admin/governance/overview?audience=` (`backend/app/governance/overview.py::health_status`). The status is the worst of three signals over the last `GOVERNANCE_HEALTH_WINDOW_MINUTES` (default 60):
    - error rate: Degraded at 2 %, Incident at 10 %
    - p95 latency: Degraded at 8000 ms, Incident at 20000 ms
    - Azure quota utilisation: Degraded at 80 %, Incident at 95 %
  - The pill renders nothing while loading, on error, or when the value is not one of the three states.
  - Thresholds are config values (`governance_health_*` in `backend/app/core/config.py`). The exact error-rate and p95 inputs are documented in `governance.md`.
  - Not verified here: how the overview computes those inputs.

Source: `frontend/app/(app)/admin/page.tsx`, `frontend/components/admin/DashboardHeader.tsx`, `frontend/components/admin/dashboard/HealthPill.tsx`.

### How the request window is built (applies to every KPI and chart)

- The browser always sends `from` and `to` as ISO 8601 UTC, rounded down to the minute (`rangeParams`, `frontend/lib/dashboard-api.ts`).
  - Presets are **rolling**: "Last 7 days" is `now - 7 x 24 h` to `now`.
  - Custom ranges are whole **local** days. `from` is local midnight of the first day, and `to` is local midnight **after** the last day (the last day is inclusive).
- The server treats `from` as inclusive and `to` as exclusive. `to <= from` returns 422, and so does a span above `USAGE_MAX_RANGE_DAYS` (default 400) (`backend/app/dashboard/window.py::resolve_window`).
- **Previous period**: an equal-length window ending at `from`. It is used for every delta badge (`window.previous_from`, `window.previous_to`).
- **Buckets**: hourly when the window is 48 h or shorter, otherwise daily. Buckets are truncated in **UTC** in SQL (`date_trunc` on naive-UTC columns). Axis labels are formatted in UTC too (`bucketLabel`, `timeZone: "UTC"`). So on "Last 24 hours" the hour labels are UTC hours, not your local hours, and a "day" is a UTC day.
- **Delta badge**: `(current - previous) / previous`, shown as a percent with one decimal.
  - The badge is hidden when the previous value is 0 or unknown (`delta`, `window.py`).
  - It is green when the change is in the good direction (up for counts, down for latency, error rate and cost), red otherwise, and grey with a dash when the change is under 0.05 %.
- **Legacy mode**: if a backend ignores `from` / `to` (no `window` in the response), the cards fall back to their legacy windows. The labels then read "All time", "Last 24 hours", "Since server start" or "Month to date", and the queries sparkline shows the 7-day `query_trend` with the hint "Trend: last 7 days". The current backend always honours the window.

### KPI cards (8)

Each card shows the label, the window label under it (the range label, for example "Last 7 days"), the value, the delta badge and a sparkline with one point per bucket. Clicking the card or its sparkline opens the owning page. Hovering or focusing for 120 ms prefetches that page's first request. A value the server does not report shows "—" with the hint "Not reported by the server".

All windowed KPI values come from `GET /admin/dashboard?audience=&from=&to=`, built by `backend/app/dashboard/service.py::window_payload` from single-statement aggregates in `backend/app/dashboard/queries.py`. The aggregates run in parallel, one DB session each.

#### **Total Users** -> `/admin/users`

What it shows: the number of distinct people (as client IPs) who asked at least one question in the window.

How it's computed:
- Endpoint: `GET /admin/dashboard`, field `users`.
- Definition: the count of distinct **client IPs** that sent at least one user message (`chat_messages.role = 'user'`) in `[from, to)`. The client IP is `COALESCE(chat_messages.ip_address, chat_sessions.created_ip)` (`queries.py::messages_split`, measure `visitors`). There is no client-id or signed-in-user identity, so the IP is the only identity.
- Test IPs are excluded only on the **Users** audience. On Overview they count, and on Testing only test or unknown IPs count.
- Sparkline: cumulative. Each point is the number of distinct IPs whose first message in the window fell in that bucket or earlier, so the last point equals the card (`first_seen_per_bucket`, `service.py::cumulative`).
- Despite the label, this is not "all users ever". It covers only the selected window.

#### **Active Users** -> `/admin/usage?tab=users`

What it shows: distinct client IPs that asked something in the **last 24 hours of the window**.

How it's computed:
- Endpoint: field `active_users`.
- Definition: distinct `COALESCE(message IP, session IP)` with at least one user message in `[to - 24 h, to)` (`messages_split`, measure `active`, `ACTIVE_SPAN = 24 h`).
  - The previous-period value uses the last 24 h of the previous window.
  - On "Last 24 hours" this equals **Total Users**.
  - For a custom range it is the last 24 h before the local midnight after your last day.
- The card's window label still shows the range label (for example "Last 30 days") even though the value covers only the final 24 h.
- Audience: as Total Users.
- Sparkline: distinct IPs per bucket (not cumulative).

#### **Sessions** -> `/admin/usage?tab=sessions`

- Definition: the number of `chat_sessions` **created** in the window (`sessions_split`, by `chat_sessions.created_at`). The response also returns `conversations` with the same value.
- Audience: filtered on `chat_sessions.created_ip`.
- Sparkline: sessions created per bucket.

#### **Queries** -> `/admin/usage?tab=queries`

- Definition: the number of user messages (`chat_messages.role = 'user'`) created in the window (`messages_split`, measure `queries`).
- Audience: `COALESCE(message IP, session IP)`.
- Sparkline: user messages per bucket.

#### **Feedback** -> `/admin/feedback`

- Definition: the number of ratings in `message_feedback` created in the window (`feedback_split`). Every rating 1-5 counts.
- Audience: `COALESCE(session IP, message IP, 'Unknown')`, the same scope as the Feedback page (`backend/app/feedback/repository.py::CLIENT_IP`).
- Legacy mode is different: it counted `audit_log` rows whose action contains "feedback", all time.

#### **Average Latency** -> `/admin/governance?tab=Monitoring`

- Definition: the SQL `AVG(latency_metrics.total_ms)` of requests recorded in the window (`latency_split`). It is displayed in seconds with two decimals (`formatLatencyMs`, for example "2.40 s").
- Audience: on Users or Testing, latency rows are joined to their `audit_log` row with `action = 'query'` on `request_id` and filtered on that row's IP (`backend/app/governance/timeseries.py::scope_latency`). On Overview, every latency row counts.
- Good direction: down. A window with no requests reports 0.0, and its delta badge is hidden.

#### **Error Rate** -> `/admin/governance?tab=Monitoring`

- Definition, as a percent (0-100, shown with two decimals):
  `(failed assistant turns + server errors) / (assistant turns + server errors) x 100` (`service.py::error_rate`).
  - A failed turn is an assistant message with `outcome IN ('llm_failed', 'empty_llm_output')` (`timeseries.FAILED_OUTCOMES`).
  - Server errors are `audit_log` rows with `action = 'server_error'`.
- Audience: turns go through their `query` audit row (`scope_turns`), and server errors use `audit_log.ip_address` (`scope_audit`).
- Legacy mode used the in-memory request counter "Since server start".
- The card uses the danger (red) sparkline tone.

#### **Cost** -> `/admin/usage`

- Definition: the sum of `chat_messages.cost_usd` over **exchanges** in the window (`exchanges_split`). An exchange is an assistant row joined to the user row with the same session and turn. It is counted by the assistant row's `created_at`, and the audience filter uses `COALESCE(user IP, session IP)`. The value is formatted as US dollars with two decimals.
- Where the cost comes from: `cost_usd` is written once per exchange at answer time by `backend/app/governance/costing.py::compute_exchange_cost`, using the rates configured at that moment (USD per 1M tokens):
  - `PRICE_EMBED_PER_MILLION`, default 0.02, applied to query and guardrail embeddings
  - `PRICE_PROMPT_PER_MILLION`, default 0.25
  - `PRICE_CACHED_PER_MILLION`, default 0.125
  - `PRICE_OUTPUT_PER_MILLION`, default 2.00, applied to completion and reasoning tokens

  The rates live in `backend/app/core/config.py`. Changing them does not re-price old exchanges.
- Ingestion (document embedding) cost is **not** included here. It appears only on Usage & Cost.
- Sparkline points are `null` (gap) for buckets with no costed exchange. If the previous window had no cost, the delta is hidden.

### **Queries by outcome** (stacked area)

- Subtitle: "Click a day to open those queries". The window label is the range label.
- Series and colours: **Answered**, **Ungrounded** (warning colour), **Blocked** (danger colour), **Conversational** and **LLM failed**.
- How it's computed:
  - Endpoint field `queries_by_outcome`: for each bucket, the count of exchanges per canonical outcome (`exchanges_split`).
  - Legacy names are mapped (`fallback` becomes ungrounded, `empty_llm_output` becomes llm_failed), and a NULL or unknown outcome counts as **answered** (`queries.py::outcome_sql`).
  - Exchanges are counted by assistant time, so a user message that never got an answer row is in **Queries** but not here. The two totals can differ slightly.
- Click: any point opens `/admin/usage?tab=queries&from=YYYY-MM-DD&to=YYYY-MM-DD` for that bucket's **UTC** day (for hourly buckets, the day of the hour). The Usage page reads `from` / `to` as the global custom range in local days, so near midnight the day you land on can differ by one from the bucket you clicked.
- Fallback: without `queries_by_outcome`, the chart shows "All queries" from the 7-day `query_trend`, with the subtitle "Outcome split not reported by the server; total queries shown. Click a day to open them" and the window "Last 7 days".
- Empty: "No queries in this window".

### **Satisfaction** (radial gauge)

- Subtitle: "Thumbs-up share of rated answers. Click to open Feedback".
- How it's computed (`service.py::satisfaction` over `feedback_split`):
  - thumbs up = ratings `>= 4`
  - thumbs down = ratings `<= 2`
  - **ratings of 3 are ignored**
  - `ratio = up / (up + down)`, `null` when there are no up or down ratings
- The gauge shows `round(ratio x 100)%` and the caption "N ratings" (up + down). It turns warning below 70 % and danger below 50 %.
- Window and audience: as the Feedback KPI. A click opens `/admin/feedback`.
- Empty: "Satisfaction is not reported by the server".

### **Latency p50 / p95** (percentile band)

- Subtitle: "End-to-end response time per bucket. Click to open Monitoring".
- How it's computed: SQL `percentile_cont(0.5)` and `percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_metrics.total_ms)` per bucket (`latency_split`). These are computed in PostgreSQL, not in Python, and interpolate between samples. Buckets with no requests are dropped from the chart.
- Audience: as Average Latency.
- Empty: "No requests in this window" (or "Latency percentiles are not reported by the server").

### **Cost vs budget** (bullet)

- Subtitle: "Month to date, linear month-end forecast and budget". The window label is the month, `YYYY-MM`.
- This widget **ignores the date range**. It always shows the current **UTC calendar month** (`window.py::month_forecast`, `queries.py::daily_cost`).
  - **Month to date**: the exchange cost (same source as the Cost KPI) from the 1st of the month 00:00 UTC up to now.
  - **Forecast**: `mtd / day_of_month x days_in_month`, a linear projection. It shows "—" on the 1st of the month.
  - **Budget**: `MONTHLY_COST_BUDGET_USD` from config. It shows "Not set" when unset (default unset).
- The audience applies. A click opens `/admin/usage`.

### **Top intents** (treemap)

- Subtitle: "Click an intent to open it in Analytics".
- How it's computed (`queries.py::top_intents`):
  - The data is the classifier labels in `turn_insights`, joined to their message and counted by `chat_messages.created_at` in the window, per intent.
  - At most 8 intents are shown, highest count first.
  - Share = count / all classified turns in the window.
  - The label comes from the taxonomy (`insight_taxonomy`, kind `intent`), or falls back to the raw code.
  - The audience filter uses `chat_sessions.created_ip`.
- Click: `/admin/analytics?intent=<code>`.
- Empty: "No classified queries in this window". Classification runs in a background job, so the newest turns can be missing.

### **Live activity** (feed)

- Shows the newest 20 events from `GET /admin/dashboard/activity?audience=&limit=20` (`backend/app/dashboard/activity.py`). Each row has an icon by type, a one-line summary and the time.
- It is **independent of the date range** and refreshes every 15 s while the browser tab is visible (`ACTIVITY_REFRESH_MS`). A green **Live** dot shows once data has loaded.
- Event types, from an allow-list of `audit_log` actions (auth events are never shown):

| Type | Source actions | Summary text |
|---|---|---|
| query | `query` (normal outcome) | "Query answered in 2.1 s", "Query handled as small talk", "Query answered without grounding" |
| error | `query` with outcome `llm_failed` or failure flags; `server_error`; `llm_call_failed` | "Query failed ...", "Server error 500", "LLM call failed" |
| guardrail | `query` blocked; `online_eval` | "Query blocked", "Online evaluation flagged N issues" |
| feedback | `user_feedback`, `negative_feedback_spike` | "Feedback received: 2/5 with a comment", "Negative feedback spike detected" |
| document | `ingest`, `document_delete` | "Document ingested", "Document deleted" |
| admin | test-IP changes, feedback triage/export, golden-case runs, usage/log exports, branding and taxonomy changes | "Admin action: test ip added" |

- The summaries are built only from typed fields, never from query text, emails or IPs.
- Click target (`activityHref`), most specific first:
  - feedback with a message id opens `/admin/feedback?message_id=`
  - otherwise a request id opens the trace (`/admin/governance?tab=Traces&request_id=`)
  - otherwise a conversation id opens `/admin/conversations/{id}`
  - otherwise a document id opens `/documents/{id}`
  - otherwise the row is not clickable
- The audience filters on `audit_log.ip_address`.
- Time format: hour and minute in your **browser's local** timezone (`en-AU`, for example "02:35 pm"). The date is not shown.

### **RAG pipeline** (stage list)

- Subtitle: "Average time per stage". The window is fixed at **Last 24 hours**, whatever range is selected. A **Monitoring** link sits in the header.
- Rows: **Embedding**, **Retrieval**, **Reranker** (with a **Disabled** chip when `ENABLE_RERANKER` is off) and **LLM synthesis**. Each row shows the Python-side mean of `latency_metrics.<stage>_ms` over rows from the last 24 h, rounded to 0.1 ms, or "No data". A thin bar shows each stage's share of the total (`admin.py::_pipeline_status`).
- This widget is **not audience-scoped**. It averages all traffic.
- Empty: "No pipeline timings yet".

### Not on this page

Groundedness rate and per-dimension quality scores are not Dashboard widgets. Ungrounded answers appear only as the **Ungrounded** outcome series. See `governance.md` (Overall / Monitoring) and `evaluation.md`.

## Refresh and caching

| Request | Client cache (`frontend/lib/query-cache.ts`) | Polling | Server cache |
|---|---|---|---|
| Summary `GET /admin/dashboard` | key = audience + from + to; fresh for 30 s | none; revalidates on window focus / tab visible when stale | weak ETag (304 on match), `Cache-Control: private, max-age=15` (`HTTP_CACHE_MAX_AGE_SECONDS`) |
| Activity `GET /admin/dashboard/activity` | key = audience; fresh 15 s | every 15 s while visible; stops if the endpoint is 404/405 | weak ETag |
| Health `GET /admin/governance/overview` | key = audience; fresh 30 s | none | weak ETag |

There is no manual refresh button. To force fresh numbers, change the range or audience and back, or reload the page. Preset windows are rounded to the minute, so the summary key changes at most once a minute.

## Common tasks

1. **Check whether yesterday was worse than usual**
   1. Pick **Last 24 hours** in the date range menu.
   2. Read the delta badges: they compare with the 24 h before. Red on **Error Rate**, **Average Latency** or **Cost** means the number went up.
   3. Open **Latency p50 / p95** with **View as table** to see the hourly UTC values.
2. **Report real-user activity only (exclude QA and internal tests)**
   1. Select the **Users** audience tab.
   2. Choose the period, for example **Last 30 days**.
   3. Read **Total Users** (distinct client IPs that asked something in the period) and **Queries**. If test traffic still appears, add its IP under **Settings > Test IPs**, or mark it from a Visitors row (`visitors.md`); the numbers re-classify within about 30 s.
3. **Investigate a spike in failed or blocked answers**
   1. In **Queries by outcome**, click the day with the spike. Usage & Cost opens on **Queries** for that day.
   2. Or watch **Live activity**: red (error) and amber (guardrail) rows open the exact trace.
4. **Check spending against budget**
   1. Read **Cost vs budget**: month to date, forecast and budget for the current UTC month.
   2. If **Budget** shows "Not set", ask an operator to set `MONTHLY_COST_BUDGET_USD`.
   3. Click the bullet for the per-stage cost breakdown on Usage & Cost.
5. **Find what people ask about most**
   1. Read **Top intents** and click a tile to open Analytics filtered to that intent.

## Interactions and states

- **Drill-downs**: every KPI card (and its sparkline), the outcome chart, the latency band, the cost bullet, the satisfaction gauge, the intent tiles, the health pill and the activity rows navigate to the owning page (see the API and link tables). Card clicks carry `range` / `from` / `to`.
- **View as table / View as chart**: every chart card has this toggle, an accessible table of the plotted values (`ChartCard`). There is no CSV export on this page; use the exports on Usage & Cost (`usage:export`).
- **Keyboard**: cards and links are focusable (focus ring). Ctrl/Cmd+K opens the palette and `?` shows shortcuts. There are no page-specific shortcuts.
- **Loading**: eight skeleton cards labelled "Loading <KPI label>", chart skeletons, and five skeleton rows "Loading activity". The summary, activity and health requests load independently, so one slow source never blocks the others.
- **Errors**:
  - KPI row: "Couldn't load the key metrics" with **Retry**.
  - Charts: the error text with **Retry** (the default text is "Couldn't load this chart").
  - Activity: "Couldn't load activity" with retry.
  - Once data has loaded, a failed background refresh keeps the old numbers on screen.
- **Empty states**: listed per widget above. The activity feed shows "No recent activity", or "No activity feed on this server" ("The server does not provide recent events.") when the endpoint is missing.
- **When data looks missing**:
  - Check the audience first. **Testing** hides real traffic, and **Users** hides test and unknown-IP traffic.
  - Top intents need the classifier job.
  - Cost is 0 for exchanges with no LLM call (blocked or conversational).
  - The pipeline card needs `latency_metrics` rows from the last 24 h.

## Light and dark theme

Chart colours come from `useChartTheme`:
- **Answered** and **Conversational** use the first two categorical colours, and **LLM failed** the fifth.
- **Ungrounded** and **Blocked** use the theme's warning and danger status colours, so they stay recognisable in both themes.
- Delta badges use the status-ok and status-danger token pairs, which are contrast-checked per theme (see `settings.md` and `shell.md`).

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/admin/dashboard?audience=&from=&to=` | `dashboard:read` | KPIs, sparklines, deltas, all chart series |
| GET | `/admin/dashboard/activity?audience=&limit=20` | `dashboard:read` | Live activity feed (limit 1-50) |
| GET | `/admin/governance/overview?audience=` | `dashboard:read` | Health pill (only `health.status` is read) |
| GET | `/admin/usage/audience-counts` | `dashboard:read` | Audience tab tooltips |
| GET | `/admin/users`, `/admin/usage/summary`, `/admin/feedback/analytics`, `/admin/governance/monitoring` (`?audience=`) | `dashboard:read` | Hover prefetch of each KPI card's target page |

The browser calls these through the Next proxy, `/api/proxy/admin/...` (`frontend/app/api/proxy/[...path]/route.ts`). `audience` is omitted when it is `overview`.

## Related pages

[README](README.md) · [Shell](shell.md) · [Login](login.md) · [Chat](chat.md) · [Documents](documents.md) · [Visitors](visitors.md) · [Usage & Cost](usage-cost.md) · [Conversation Logs](conversation-logs.md) · [Feedback](feedback.md) · [Evaluation](evaluation.md) · [Governance](governance.md) · [Analytics](analytics.md) · [User management](user-management.md) · [Settings](settings.md)

## Known limitations and in-flight changes

- **Identity is the IP address.** "Total Users" and "Active Users" count distinct client IPs. People behind one NAT or VPN count as one user, and one person on two networks counts as two. The IP is the proxy's peer address unless `TRUST_PROXY_HEADERS` is on (`backend/app/core/request_meta.py`).
- **Test IPs count on Overview.** Only the **Users** audience excludes them.
- **"Active Users" window label is misleading.** It shows the range label, but the value covers only the last 24 h of the range.
- **UTC everywhere on the charts.** Buckets and axis labels use UTC days and hours, while the date picker's custom range uses local days. Activity times are local.
- **Fixed windows.** **Cost vs budget** always shows the current UTC month, and **RAG pipeline** always the last 24 h, with no audience filter.
- **Feedback sources differ between modes.** Windowed numbers use `message_feedback`. Legacy (no-window) mode counted `audit_log` rows containing "feedback", which also matched admin actions such as `feedback_triaged`.
- **Contract doc drift** (`docs/technical/frontend/contracts/dashboard-aggregations.md`):
  - Section 6 lists the health pill under `/admin/governance?tab=Monitoring`, but the code links to `?tab=Overall`.
  - It says top intents come "from the insights rollup", but the code reads `turn_insights` directly.
  - It does not state that windowed **Feedback** counts `message_feedback` rows.
- **No manual refresh**, and the summary does not poll.
- Coming in branch `fix/shell-nav-logout-theme-dates`: **one active-visitors definition**.
  - **Total Users** is relabelled **Active visitors** and **Active Users** becomes **Active visitors (24h)** with the window label "Last 24 hours of the range". Both get an info tooltip.
  - The new definition: "Active visitors are distinct end users with at least one query in the period. A visitor is identified by their client id, or by their IP address when the client id is missing or the default anonymous value. Traffic from test IPs is excluded."
  - **Test IPs are excluded from visitor counts on Overview too.** Only the Testing audience counts test traffic.
  - The identity is the session actor (`chat_sessions.user_email`) unless it is empty or the default/anonymous actor; otherwise it is the query's IP (`backend/app/dashboard/active_visitors.py`).
  - A new **Active visitors** panel, "As of now, independent of the date range", sits under the KPI row. It shows **Active visitors now** (last 5 minutes) and **Daily active visitors** / **Weekly active visitors** / **Monthly active visitors** (last 24 h / 7 d / 30 d), each with "+x % vs previous ..." against the previous equal window.
  - The legacy (no-window) KPIs and Governance Monitoring use the same definition.
- Coming in branch `fix/shell-nav-logout-theme-dates`: **Live activity** times switch to the shared relative-time display ("5m ago", with the full local date-time and timezone in the tooltip), so the date is no longer missing.
- Coming in branch `perf/dashboard-load-speed`: no metric definitions change.
  - The admin shell and page skeletons paint in the first HTML response instead of a full-screen spinner.
  - Hovering a KPI card now fills the target page's own cache (Usage & Cost summary, Governance Monitoring, Feedback analytics), so those pages open instantly after a hover.
  - The cached-token read behind Usage & Cost is about 3x cheaper.
  - The Usage & Cost and Governance endpoints that the cards link to load several times faster.
- Coming in branch `feat/realistic-demo-data`: `backend/scripts/seed_realistic_activity.py` seeds about 2,000 visitors and 5,200 sessions over 120 days (dry run by default; `--apply`, `--scale`, `--days`, `--purge`). It also seeds about 4 % test traffic from the test-IP rules `10.20.0.0/16` and `192.168.50.0/24` (descriptions start with `seed:`), so every widget has data on a demo database.
  - Seeded rows have `request_id` starting with `seed-`.
  - `--purge` keeps seeded `audit_log` rows, because the audit log is append-only. The **Live activity** feed and the legacy feedback counts can therefore still show seeded events after a purge.
  - The same branch fixes live-path gaps: query audit rows now carry `message_id`, and ingest audit rows carry `cost_usd` and `document_id`, which the activity feed links use.
