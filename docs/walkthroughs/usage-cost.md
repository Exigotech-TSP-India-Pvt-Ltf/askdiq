# Usage & Cost

> Token volume, spend and turn-level economics of the assistant, split by audience, with drill-down from visitor IP to session to single turn.  Route: `/admin/usage` · Nav: **Usage & Cost** · Permission: `dashboard:read` (operator, admin, superadmin); **Export CSV** needs `usage:export` (admin, superadmin); adding or removing test IPs needs `testip:write` (admin, superadmin)

## Purpose and who uses it

The page answers "what does the assistant cost, where do the tokens go, and who is driving it?".

- **Admins / finance owners** check total spend, the month-end forecast and how much prompt caching saves.
- **Analysts** compare token mix (prompt vs reasoning vs visible output vs embeddings) and find heavy IPs or sessions.
- **Support** finds a single turn by text, IP, session or request id and opens the **Single-Query Economic Inspector** to see exactly what was billed.

All money is **USD**. There is one rate card for the whole deployment (one chat model, one embedding model), not a per-model price table. See [How cost is computed](#how-cost-is-computed).

## How to get there

- **Sidebar**: **Usage & Cost** (dollar-coin icon). Shown to every role with `dashboard:read` (`frontend/components/Sidebar.tsx`).
- **Command palette** (Ctrl/Cmd+K): type "Usage & Cost" (no extra keywords are registered for this item).
- **Links from other pages** (`frontend/lib/entity-links.ts`):
  - a visitor link opens `/admin/usage?tab=sessions&ip=<ip>`;
  - a session link opens `/admin/usage?tab=queries&session=<session-id>`;
  - Dashboard day links send `from` = `to` = `YYYY-MM-DD`.

URL parameters read by the page (`frontend/app/(app)/admin/usage/page.tsx`, `frontend/lib/usage-tabs.ts`):

| Param | Allowed values | Default | Effect |
|---|---|---|---|
| `tab` | `overview`, `users`, `sessions`, `queries` (old aliases `live_telemetry` and `overall` map to `overview`; anything else falls back to `overview`) | `overview` | Active tab |
| `ip` | an IP string | none | IP filter on **All Sessions** and **All Queries** |
| `session` | a session UUID | none | Session filter on **All Queries** |
| `from`, `to` | `YYYY-MM-DD` (other formats are ignored) | none | Day filter on **All Queries** only. Each day is a whole **UTC** day |
| `audience` | `overview`, `users`, `testing` | `overview` / last used | Audience scope (global, also kept in localStorage) |

Per-table params (`frontend/lib/use-table-state.ts`, prefix = table key `users`, `sessions` or `queries`): `<key>_q` (search, debounced 300 ms), `<key>_page`, `<key>_limit` (`10`, `25`, `50`, `100`; default `10`), `<key>_sort`. Dropdown filters (User Type, Groundedness, outcome pills) are **not** stored in the URL.

Note: `from` / `to` are also the global custom-range params that admin links carry between pages (`CARRIED_PARAMS` in `frontend/lib/entity-routes.ts`). If you arrive with a custom global range, the **All Queries** tab is filtered to those days. Presets (`?range=7d` etc.) are not read by this page at all.

## Layout at a glance

```text
+--------------------------------------------------------------------------+
| [$] Usage & Financial Economics   [Overview|Users|Testing] [Configure Test IPs]
|     Audience-segmented cost accounting, token volume telemetry, ...      |
+--------------------------------------------------------------------------+
| [Overview Live] [All Users] [All Sessions] [All Queries]                 |
+-----------------------+-----------------------+--------------------------+
| Cost by stage         | Month-end forecast    | Cache-hit ratio          |
| (stacked area, USD)   | (actual + dashed fcst)| (radial gauge, %)        |
+-----------------------+-----------------------+--------------------------+
| CHATBOT LIVE TELEMETRY COMMAND CENTER        Last Telemetry Sync [Sync]  |
| Turns | Grounded Citations | P50 | Net Platform Spend | Avg Cost / Turn |
| Telemetry Focus: [Full Studio View][Economic Waterfall][...][...]        |
+-----------------------+-----------------------+--------------------------+
| Economic Waterfall    | The Thinking Pipeline | Performance Cockpit      |
| LLM / Embeddings /    | 3 rings + token rows  | speedometer, groundedness|
| Prompt Cache Savings  | [Vol] [%]             | outcome filter buttons   |
+-----------------------+-----------------------+--------------------------+
| Live Chatbot Conversational Stream & Audit Feed (8 most recent turns)    |
+--------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/usage-cost-overview.png` — Overview tab with the three insight charts, the command bar and the three telemetry cards.

```text
All Users / All Sessions / All Queries tabs
+--------------------------------------------------------------------------+
| Stat card | Stat card | Stat card | Stat card                            |
| Top 3 highlight cards (Users, Sessions)  /  outcome pill bar (Queries)   |
+--------------------------------------------------------------------------+
| [Search ...] [filter v] [chips]                          [Export CSV]   |
| table rows ...                                     < 1 2 3 >  10/page    |
+--------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/usage-cost-queries.png` — All Queries tab with a session filter chip and the inspector drawer open.

## How cost is computed

This section applies to every tab. Source: `backend/app/governance/costing.py`, `backend/app/core/config.py`, `backend/app/api/routes/admin_usage.py`.

### Rate card (where prices live)

Prices are USD per 1,000,000 tokens, read from environment variables through `Settings` (`backend/app/core/config.py`, documented in `backend/.env.example`):

| Setting (env var) | Default | Applies to |
|---|---|---|
| `PRICE_PROMPT_PER_MILLION` | `0.25` | Uncached prompt (input) tokens of the chat model |
| `PRICE_CACHED_PER_MILLION` | `0.125` | Prompt tokens served from the Azure prompt cache |
| `PRICE_OUTPUT_PER_MILLION` | `2.00` | Completion tokens, **both** reasoning and visible output |
| `PRICE_EMBED_PER_MILLION` | `0.02` | Query embedding, guardrail (groundedness) embedding and ingestion embedding |
| `MONTHLY_COST_BUDGET_USD` | unset | Optional budget line on the forecast chart |

There is **no per-model table**. The chat model is the single Azure deployment (`AZURE_DEPLOYMENT_NAME`) and the embedding model is `EMBEDDING_MODEL` (default `text-embedding-3-small`). If you change deployments, update the four rates yourself.

### Stored vs computed

- **Stored per turn.** When an answer is produced, `compute_exchange_cost` (`backend/app/governance/costing.py`, called from `backend/app/api/routes/query.py`) computes the turn cost with the rates in force at that moment and writes it to `chat_messages.cost_usd` on the assistant row. The rates used are snapshotted in `token_details.pricing`. Turn cost =
  `(prompt - cached) x prompt_rate + cached x cached_rate + reasoning x output_rate + (completion - reasoning) x output_rate + (query_embed + guardrail_embed) x embed_rate`, all divided by 1,000,000, rounded to 7 decimals.
- **Computed at read time.** The stage breakdowns on this page (LLM vs embeddings, prompt vs reasoning vs visible, cache savings, Cost by stage) are recomputed from **summed token counts x the current rates**.
- Consequence: totals that come from stored `cost_usd` (Net Platform Spend, Avg Cost / Turn, every **Cost (USD)** column, the forecast) keep historical prices. The stage splits use today's prices. If the rates in `.env` changed, the stage parts no longer add up exactly to the total.

### Token definitions

| Term on the page | Source column | Meaning |
|---|---|---|
| Prompt / Input tokens | `prompt_tokens` | Chat-model input tokens, including any cached ones |
| Cached prompt tokens | `token_details.cached_tokens` | Part of the prompt served from cache (capped at prompt tokens) |
| Reasoning tokens | `reasoning_tokens` | Hidden reasoning tokens. They are part of completion tokens and billed at the output rate |
| Output / Visible tokens | `completion_tokens - reasoning_tokens` (never below 0) | Text the user actually sees |
| Embed tokens | `embedding_tokens` | Query-embedding tokens (the **Embed Tokens** columns do not include guardrail tokens) |
| Guardrail embed tokens | `guardrail_tokens` | Answer embedding for the groundedness check |
| Total tokens (tables) | `total_tokens` | Chat-model prompt + completion, as reported by Azure. Embeddings are **not** included |
| Total billed (Overview, inspector) | `total_tokens + embedding_tokens + guardrail_tokens` (+ ingestion embedding tokens on Overview) | Everything that was billed |

A turn is one user message plus its assistant answer, joined on `session_id` + `turn_index`. Turns are dated by the **user message** `created_at` (naive UTC).

## Layout tour

### Header

- Title **Usage & Financial Economics**, subtitle **Audience-segmented cost accounting, token volume telemetry, and turn-level LLM economics**.
- Audience tabs (**Overview / Users / Testing**). They scope every request on the page (`audience=` query param, `backend/app/governance/audience.py::build_audience_filter`):
  - `overview`: no filter;
  - `users`: client IP is not NULL and not a test IP;
  - `testing`: client IP is NULL or a test IP.
  The client IP is `coalesce(user message IP, session created IP)` on Overview and All Queries, the **user message IP** on All Users, and the **session created IP** on All Sessions.
- **Configure Test IPs** button: opens the test-IP dialog ([Test IP dialog](#test-ip-dialog)). Visible to everyone; saving needs `testip:write`.
- There is **no date-range picker** on this page. See [Known limitations](#known-limitations-and-in-flight-changes).

### Tab strip

Labels: **Overview** (with a **Live** badge), **All Users**, **All Sessions**, **All Queries**. Switching tabs rewrites `?tab=` with `history.replaceState`, so tab changes do not add Back-button entries.

### Overview tab: Cost by stage

- Title **Cost by stage**, subtitle **USD per pipeline stage**. Stacked area, one series per stage: **Query embedding**, **Guardrail embedding**, **Prompt (uncached)**, **Prompt (cached)**, **Reasoning**, **Visible output**, **Ingestion**.
- How it's computed:
  - Endpoint: `GET /admin/usage/summary` field `cost_by_stage_series` (`backend/app/dashboard/usage_series.py::cost_by_stage_series`).
  - Per bucket: token sums x current rates, the same formulas as the stage costs below. **Ingestion** = sum of `detail.cost_usd` on `ingest` audit rows.
  - Time window: the page sends no dates, so the series runs from the first day with data to now. It is capped at `USAGE_MAX_RANGE_DAYS` (400). The bucket is **day**. Hour buckets are only used for a bounded range of 48 h or less, which this page never requests.
  - Timezone: buckets are UTC days. Axis labels are formatted in UTC (`bucketLabel`, `frontend/lib/dashboard-api.ts`), for example "16 Oct".
  - Audience: yes.
- Table view columns: **Bucket**, then one column per stage (4 decimals).
- Empty states: **No cost in this window** (series present but empty) or **Cost by stage over time is not reported by the server** (field missing).

### Overview tab: Month-end forecast

- Title **Month-end forecast**, subtitle **Cumulative cost and linear projection from month to date**. The current month (`YYYY-MM`) is shown as the window. A chip **$X forecast** appears top-right when a forecast exists. A dashed reference line **Budget $X** appears when `MONTHLY_COST_BUDGET_USD` is set.
- How it's computed (`backend/app/dashboard/window.py::month_forecast`, `backend/app/dashboard/queries.py::daily_cost`):
  - Endpoint: `GET /admin/usage/summary` field `forecast`.
  - Daily cost = sum of stored `cost_usd` per **UTC** day of the assistant message `created_at`, current UTC calendar month, audience-scoped.
  - **Actual** = running month-to-date total up to today. **Forecast** = `mtd / today.day x days_in_month`, drawn from today to month end. Today carries both values so the lines join.
  - On day 1 of the month the forecast is null (no complete day yet).
  - Ingestion cost is **not** included (exchange cost only), unlike **Net Platform Spend**.
- Table view: **Day**, **Actual (cumulative)**, **Forecast** ("—" where null).
- Empty state: **The month-end forecast is not reported by the server**.

### Overview tab: Cache-hit ratio

- Title **Cache-hit ratio**, subtitle **Share of prompt tokens served from the cache**. Radial gauge (higher is better) with the caption **N of M tokens**.
- How it's computed: `cached_prompt_tokens / prompt_tokens` over the audience scope, all time (`usage_series.cache_stats`). Null (gauge shows no value) when there are no prompt tokens.
- Table view: **Cached prompt tokens**, **Prompt tokens**, **Hit ratio**.
- Empty state: **Cache statistics are not reported by the server**.

### Overview tab: command bar

Heading **Chatbot Live Telemetry Command Center** with a static **Live Production Stream** pill. Under it: **Audience: <scope> • Synchronized across real multi-turn dialogues, reasoning chains, and ground-truth citations.**

- **Last Telemetry Sync**: `as_of` of the summary, shown as a time in your browser's local timezone. The server caches the summary for 15 s, so this can be up to 15 s old.
- **Sync Telemetry** button: refetches the summary and the live feed. This is the only manual refresh on the page.

Five tiles (all from `GET /admin/usage/summary`, `backend/app/api/routes/admin_usage.py::_usage_summary_payload`, all time, audience-scoped):

| Tile | Value | How it's computed |
|---|---|---|
| **Conversational Turns** ("total turns") | `accounting.total_turns` | Count of assistant messages that have a matching user message |
| **Grounded Citations** ("verified") | `quality.groundedness_pass_rate` | `answered / (answered + ungrounded + llm_failed) x 100`, 1 decimal. Shows 100.0 when there are no generation turns |
| **Median Turn Speed (P50)** ("s / query") | `quality.latency.p50_ms / 1000` | `percentile_cont(0.5)` of `latency_records.total_ms` for turns with a latency record |
| **Net Platform Spend** ("USD") | `financial.total_cost_usd` | Sum of stored `cost_usd` + ingestion cost from `ingest` audit rows (`detail.cost_usd`), 4 decimals |
| **Avg Cost / Turn** ("per answer") | `financial.avg_cost_per_query_usd` | Net Platform Spend / total turns (so it includes ingestion overhead) |

**Telemetry Focus:** buttons **Full Studio View**, **Economic Waterfall**, **Cognitive Thinking Pipeline**, **Performance Cockpit**. They highlight one card, dim the others to 60% opacity and scroll the card into view. Nothing is filtered.

### Overview tab: Economic Waterfall card

Header **Economic Waterfall** with a **Live Accounting** pill. Subtitle **Live Capital Outflow (Click to inspect)**. The top-right total, **Total Actual Net**, is the same value as **Net Platform Spend**.

Three clickable rows. Each toggles a **Stage Detail Breakdown** panel with a **Close** button:

- **LLM Generation Pipeline**: `financial.llm_cost_usd` = uncached prompt cost + cached prompt cost + reasoning cost + visible cost, at current rates. The bar under it shows **Prompt: x%**, **Thinking: x%** and **Output: x%** as shares of **total billed tokens** (embeddings included, so the three do not add up to 100%). Detail: **Prompt Context Cost:**, **Reasoning Trace Cost:**, **Visible Output Cost:** (5 decimals). The cached-prompt cost is in the total but has no row of its own here.
- **Vector & Guardrails Embeddings**: `financial.embeddings_cost_usd` = (query embed + guardrail embed tokens) x embed rate + ingestion cost. Shows **Rate: $0.020 / 1M** and the embedding token count (query + guardrail + ingestion). Detail: **Query Embed Cost:**, **Guardrails Embed Cost:**.
- **Prompt Cache Savings** ("Zero duplicate billing"): `stage_costs.cached_savings_usd` = `cached_tokens x (prompt_rate - cached_rate) / 1e6`, shown as **$x saved**. The detail text reads **Cached prompt tokens are billed at 50% discount ($<cached>/1M vs $<prompt>/1M).** "50%" is fixed text. The two rates are the live config values.

Footer: **Avg / Query: $x** (5 decimals) and a static **100% Real Accounting** label.

### Overview tab: The Thinking Pipeline card

Header **The Thinking Pipeline** with a **Token Telemetry** pill. Subtitle **Token Volume Across Stages**. Toggle buttons **Vol** (absolute counts) and **%** (share of total billed).

- Three concentric rings: prompt, reasoning, visible output, each as a share of `tokens.total_billed_tokens`. The centre shows **Billed** = total billed tokens / 1000 with a "k" suffix.
- Rows **Prompt Context**, **Reasoning Trace**, **Visible Output**.
- **Context-to-Answer Synthesis:** `prompt_context_tokens / max(visible_output_tokens, 1)`, shown as **N:1 Ratio**.
- Footer: **Avg Turn Budget: N tok** = `sum(total_tokens) / total_turns`. That is chat-model tokens only, no embeddings. Plus a static **Cognitive Pipeline** label.

### Overview tab: Performance Cockpit card

Header **Performance Cockpit**, subtitle **Speedometer & Citations**, and a chip **N turns** (= total turns).

- **Turn Speedometer**: needles for **P50: x s** and **P95: x s** (`percentile_cont(0.5 / 0.95)` of `latency_records.total_ms`). The dial runs 0 to 4 s. Anything slower pins the needle at the right end.
- **Groundedness** ring with the caption **Verified Citations**: same value as the **Grounded Citations** tile.
- **Click Outcome to Filter Real Query Feed**: buttons **Answered**, **Ungrounded**, **Blocked** and **Chit-chat** (the `conversational` outcome), each with its all-time count from `quality.outcomes`. Clicking one filters the feed below. Clicking it again clears the filter. `llm_failed` has no button.
- Footer: **Accuracy: x% verified** and a static **Cockpit Online** label. Despite the wording, this is `accounting.actual_pct`: the share of turns whose token counts came from the provider (`token_details.is_actual` is not `"false"`) rather than an estimate. It is not an answer-accuracy metric.

### Overview tab: Live Chatbot Conversational Stream & Audit Feed

- Title **Live Chatbot Conversational Stream & Audit Feed**, a chip **All Turns** or **Filtered: <outcome>**, and a **Clear Filter (Show All)** button when filtered. The count reads **N recent turns**.
- Data: `GET /admin/usage/queries?sort=created_at_desc&limit=8[&outcome=...]`, audience-scoped. It always shows the **8 most recent turns**. There is no paging.
- Columns: **User Prompt & Chatbot Answer** (quoted prompt, answer or **No response generated**, then **IP:** and **Session:**), **Outcome**, **Reasoning Tokens**, **Output Tokens**, **Speed** (seconds), **Cost (USD)** (stored, 4 decimals), **Action** (**Inspect Trace**).
- Clicking a row or **Inspect Trace** opens the inspector ([Single-Query Economic Inspector](#single-query-economic-inspector-drawer)).
- States: **Loading real chatbot queries...**, the error text (default **Failed to load real chatbot query feed.**), and the empty state **No chatbot turns found matching the selected outcome.**

### All Users tab

One row per client IP, grouped on the **user message** IP. Turns with no IP are grouped as **Unknown**.

- Endpoint: `GET /admin/usage/users` (`admin_usage.py::get_usage_users`), all time, audience-scoped (on the user message IP).
- Stat cards:
  - **Total Unique Users** (subtitle **Audience: <scope>**) = number of IP rows after filters. It counts IP addresses, not people.
  - **Active Sessions** ("Engaged conversational threads") = sum of distinct sessions per IP row. It is not "currently active".
  - **Total Queries** ("Turns processed") = sum of turns.
  - **Top Consumer Share** ("Tokens consumed by top 3 users") = total tokens of the first three rows **in the current sort order** / total tokens of all IPs that match the search, x 100. It is computed before the User Type filter, and it changes when you change the sort.
- **Top Consumers by Financial Cost (1-Click Drilldown)**: three highlight cards (**Total Cost**, queries, tokens). They are the first three rows of the current sort, so they are only "by cost" under the default sort. Clicking a card opens **All Sessions** filtered to that IP.
- Toolbar: search **Search IP address...** (ILIKE on the IP). Filter **User Type** with **All Users**, **Production Only** and **Test Users Only**. A row is **Test** when its IP is a test IP or **Unknown**. Plus **Export CSV**.
- Columns: **Client IP Address** (with a **Production** / **Test** badge), **Sessions**, **Queries**, **Embed Tokens**, **Input Tokens**, **Reasoning Tokens**, **Output Tokens**, **Total Tokens**, **Cost (USD)**. Sortable: Sessions, Queries, Total Tokens, Cost. Default sort is `cost_desc`.
- Row click: opens **All Sessions** filtered to that IP (`?tab=sessions&ip=`).
- Error default: **Failed to load user analytics.**

### All Sessions tab

One row per chat session that has at least one complete turn.

- Endpoint: `GET /admin/usage/sessions` (`get_usage_sessions`), all time, audience-scoped on the session's **created IP**. `limit` max 200 and `offset` max 10,000 (else 422).
- Stat cards:
  - **Total Sessions** (**Audience: <scope>**);
  - **Avg Turns / Session** ("Conversation depth") = turns / sessions, 1 decimal;
  - **Avg Cost / Session** ("Blended execution cost") = sum of stored cost / sessions;
  - **Max Depth (Turns)** ("Deepest conversation thread").
  All of them cover every session matching the filters, not only the current page.
- **Top Sessions by Financial Cost (1-Click Drilldown)**: the top three sessions by cost (always by cost, whatever the sort). Clicking one opens **All Queries** for that session.
- Toolbar: search **Search session title, ID, or IP...**. When an IP filter is active, a filter labelled **Filtered IP: <ip>** with the option **Clear IP Filter** appears. Plus **Export CSV**.
- Columns: **Session Title & ID** (**Untitled Session** when empty), **Client IP** (click to filter by that IP), **Turns**, **Embed Tokens**, **Input Tokens**, **Reasoning Tokens**, **Output Tokens**, **Total Tokens**, **Cost (USD)**, **Last Active**, **Inspect** (**Queries** button). Sortable: Turns, Total Tokens, Cost, Last Active. The default is `cost_desc` on the client. The server default is `last_active_desc`, but the client always sends its sort.
- **Last Active** is `chat_sessions.last_activity_at`, sent with a `Z` suffix and shown in your browser's local time.
- Row click or **Queries**: opens **All Queries** filtered to the session.
- Error default: **Failed to load session analytics.**

### All Queries tab

One row per turn.

- Endpoint: `GET /admin/usage/queries` (`get_usage_queries`, filters in `backend/app/governance/usage_queries.py::apply_usage_filters`). `limit` max 200, `offset` max 10,000.
- Filters:
  - audience, on `coalesce(user IP, session IP)`;
  - `from_date` / `to_date` on the user message time. Whole UTC days for `YYYY-MM-DD`; the upper bound is inclusive to 23:59:59.999999;
  - `ip` (exact match);
  - `session_id`;
  - `outcome`;
  - `grounded`;
  - `q`: ILIKE over the user query, the answer, the session title, the IP and the request id.
- Stat cards (computed over **all** rows matching the filters, `_query_stats`):
  - **Total Queries** (**Audience: <scope>**).
  - **Groundedness Pass Rate** = generation turns (`answered`, `ungrounded`, `llm_failed`; a NULL outcome counts as `answered`) with `grounded = true`, divided by all generation turns, x 100. The subtitle reads **P / N generation turns**, or **Generation turns only** when there are none (the value is then "—"). This differs slightly from the Overview **Grounded Citations** value, which counts outcome `answered` rather than the `grounded` flag.
  - **Avg Turn Latency**: the **mean** of `latency_records.total_ms` over matching turns that have a latency record, in ms. The subtitle **P50 / P95 response time** is misleading: no percentile is shown.
  - **Avg Token Budget** ("Billed tokens / turn") = mean of prompt + reasoning + output tokens. That equals prompt + completion; embeddings are not included.
- **Filter by Execution Outcome**: pill bar with **All**, **Answered**, **Ungrounded**, **Blocked**, **Conversational** and **LLM Failed**, each with its count under the current filters. Clicking the selected pill again returns to **All**.
- Toolbar: search **Search query, answer, session title, IP, or request ID...**. Filter **Groundedness** with **All Turns**, **Verified Grounded** and **Ungrounded**. Filter chips appear for the active session (**Session: <first 8 chars>...**, option **Clear Session Filter**), IP (**IP: <ip>**, **Clear IP Filter**) and days (**Day: YYYY-MM-DD**, **Days: A – B**, **From A** or **Until B**, option **Clear Date Filter**). Plus **Export CSV**.
- Columns:
  - **User Query Text**: first 160 characters, plus the request id;
  - **Client IP**: click to filter;
  - **Session**: title or the first 8 characters of the id; click to filter;
  - **Status / Outcome**: badges **Answered**, **Ungrounded**, **Blocked**, **Chat** (conversational) and **Failed**;
  - **Input Tokens**, **Reasoning Tokens**, **Output Tokens**;
  - **Cost (USD)**: stored, 4 decimals;
  - **Latency**: `total_ms`, e.g. "1,234 ms";
  - **Inspect**.
  Sortable: Cost, Latency. The default sort is newest first (`created_at_desc`). Rows with no latency sort last.
- Row click or **Inspect**: opens the inspector.
- Error default: **Failed to load queries analytics.**

### Single-Query Economic Inspector (drawer)

Opened from the Overview feed, the All Queries tab, a conversation detail page or a visitor detail page (`frontend/components/admin/usage/QueryInspectorDrawer.tsx`).

- Endpoint: `GET /admin/usage/queries/{request_id}` (`get_query_detail`).
- Title **Single-Query Economic Inspector**, description **Request ID: <id>**. **Open full view** links to Governance Traces (`/admin/governance?tab=Traces&request_id=<id>`). Close with **Close inspector** or Esc.
- Metadata strip: **Client IP**, **Session**, **Turn Cost** (stored `cost_usd`, 4 decimals) and **Groundedness** (**Verified** / **Ungrounded**).
- **User Prompt** with **Copy Prompt**, and **Assistant Response** with **Copy Answer**. The button reads **Copied** for 2 s after you click it.
- **Token Budget Allocation (N billed tokens)** with **100% of Turn Volume**. Segments: **Prompt Context**, **Cached Context** (only when cached > 0), **LLM Reasoning**, **Visible Output** and **Embeddings** (query + guardrail). Bar widths have a minimum so labels fit. They are not exactly proportional; the legend percentages are.
- **Stage-by-Stage Mathematical Pricing (Rate Card)** table, columns **Stage | Tokens | Rate / 1M | Cost (USD)**:
  1. **1. Query Embedding**
  2. **2. Prompt Context (Uncached)**
  3. **2b. Cached Context** (only when cached > 0)
  4. **3. LLM Reasoning**
  5. **4. Visible Output**
  6. **5. Guardrail Embedding**
  7. **Total Exact Turn Billed** (shows the stored `cost_usd`)

  Row costs are recomputed in the browser from the current rate card returned by the endpoint.
- **Turn Latency Stages** would show **Retrieval & Search**, **Guardrails Pre**, **LLM Generation** and **Guardrails Post**. It never renders today. See [Known limitations](#known-limitations-and-in-flight-changes).
- States: **Loading query accounting & traces...**, error **Failed to load query breakdown** (with **Try again**), and a 404 **Query record not found.** for unknown ids.

### Test IP dialog

Opened by **Configure Test IPs** (`frontend/components/admin/usage/TestIpManager.tsx`). Endpoints: `GET /admin/usage/test-ips` (`dashboard:read`), `POST /admin/usage/test-ips` and `DELETE /admin/usage/test-ips/{ip}` (both `testip:write`, `backend/app/api/routes/admin.py`).

- Title **Manage Test IP Rules & Audience Segmentation**. Banner **Audience Re-classification Guarantee** with the server's note.
- Form: **IP / CIDR / Prefix \*** (placeholder **e.g. 192.168.1.0/24 or 10.0.0.1**, max 64 characters), **Description / Team Purpose** (placeholder **e.g. QA team automated runner or Office VPN**) and **Add Rule**.
- **Active Custom Rules (N)** table, columns **IP / CIDR Rule**, **Description & Purpose**, **Configured At** and **Action** (trash icon). The search box reads **Filter custom rules by IP or description...**. Empty: **No custom test IP rules configured.**
- Delete asks **Confirm removal of rule <ip>? History will be re-classified.** with **Cancel** and **Delete Rule**.
- Effective test IPs = `DEV_TEST_IPS` from the environment + built-in loopbacks (`127.*`, `::1`) + active database rules (`backend/app/governance/test_ips.py`). Classification is by IP at query time, so past data moves between **Users** and **Testing** as soon as the rules change. After a change the page refreshes the summary. The server clears its usage caches.

## Common tasks

1. **What did we spend this month, and where will we land?**
   1. Open **Usage & Cost** and select the **Users** audience to exclude internal traffic.
   2. Read **Month-end forecast**: the chip shows the projected month-end total. Turn on **View as table** for daily cumulative figures.
   3. Compare with the **Budget** line, if `MONTHLY_COST_BUDGET_USD` is configured.
   4. Remember the forecast is exchange cost only. Ingestion is not included.

2. **Find who is driving cost.**
   1. Open **All Users**. The default sort is cost, highest first.
   2. Click the top IP (or its highlight card). You land on **All Sessions** filtered to that IP.
   3. Click a session's **Queries** button to see its turns, then **Inspect** the most expensive one.
   4. If the IP is an office or QA range, add it under **Configure Test IPs** (admin) so it moves to **Testing**.

3. **Explain a single expensive answer.**
   1. On **All Queries**, sort **Cost (USD)** descending, or paste the request id into the search box.
   2. Click the row. In the inspector, compare **LLM Reasoning** vs **Visible Output** tokens. Reasoning tokens are billed at the output rate.
   3. Use **Open full view** for the Governance trace (stage timings, retrieval scores).

4. **Export the turns of one day for finance.** Needs `usage:export` (admin).
   1. Follow a Dashboard day link, or open `/admin/usage?tab=queries&from=2026-10-14&to=2026-10-14`.
   2. Add any outcome, grounded or search filter.
   3. Click **Export CSV**. The file contains every matching turn, newest first, up to 10,000 rows.

5. **Check whether prompt caching is working.** On Overview, read **Cache-hit ratio** and the **Prompt Cache Savings** row of the Economic Waterfall. A ratio near 0% means prompts are not being reused.

## Interactions and states

- **Drill-down chain:** All Users row → All Sessions (`ip`) → All Queries (`session`) → inspector → Governance trace. Filter chips clear each step without leaving the tab.
- **Exports** (`GET /admin/usage/export`, `usage:export`). Each **Export CSV** button is a plain download link through the Next proxy.
  - Format: CSV, UTF-8 with BOM (for Excel), streamed.
  - Filename: `<brand-slug>-<tab>-<audience>-YYYYMMDD-HHMMSS.csv` (UTC time).
  - Cells that start with `=`, `+`, `-`, `@`, tab or CR get a leading `'` (formula-injection guard).
  - Row cap: `USAGE_EXPORT_MAX_ROWS` (10,000).
  - Every export writes a `usage_export` audit event with the tab and audience.
  - Columns per tab:
    - **queries**: `Request ID, Created At, User (IP), Session Title, Query, Prompt Tokens, Reasoning Tokens, Output Tokens, Embed Tokens, Cost ($), Status, Grounded`. **Created At** is naive UTC ISO without a `Z`. The full query text is exported, not truncated. **Grounded** is `Yes` / `No`. Filters passed: audience, `q`, `ip`, `session_id`, `outcome`, `grounded`, `from_date`, `to_date`. **User (IP)** is the user-message IP only (no session-IP fallback).
    - **users**: `User (IP), Type, Sessions, Queries, Embed Tokens, Prompt Tokens, Reasoning Tokens, Output Tokens, Total Tokens, Cost ($)`, always sorted by cost. Filters passed: audience, `q`, `type`.
    - **sessions**: `Session ID, Session Title, User (IP), Queries, Total Tokens, Cost ($), Last Active`. Filters passed: audience, `q`, `ip`. See the known bug below.
  - Users without `usage:export` still see the button. The download then fails with a 403 response.
- **Loading:** the Overview shows skeleton cards (**Loading live telemetry**). Tables show row skeletons. The route-level fallback text is **Loading enterprise usage console...**.
- **Errors:** the Overview shows **Failed to load Live Telemetry Studio** with the error message (or **No telemetry found for the selected audience scope.**) and a retry button. Each chart card has its own retry. Tables show the error with retry.
- **Refresh and caching:** no polling.
  - Overview summary: kept in memory per audience for 15 s while you stay on the page (`frontend/lib/use-usage-data.ts`). **Sync Telemetry** forces a refetch.
  - Backend summary: cached in-process for `USAGE_SUMMARY_CACHE_TTL_SECONDS` (15 s) and sent with a weak ETag (`304` when unchanged).
  - Tables: refetch whenever a filter, sort, page or audience changes.
- **Keyboard:** Ctrl/Cmd+K opens the palette. Esc closes the inspector drawer (focus-trapped, `frontend/components/ui/DrawerShell.tsx`). The test-IP dialog has no Esc handler: close it with the X button or a click on the backdrop.

## Light and dark theme

Chart colours come from the chart theme (`frontend/lib/use-chart-theme.ts`). In dark mode the Thinking Pipeline uses a glowing teal trio. In light mode it switches to three distinct palette colours (teal, amber, indigo) without glow, so the rings stay readable. Every chart card has **View as table** for an accessible, colour-free reading.

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/admin/usage/summary` | `dashboard:read` | Overview tiles, cards, Cost by stage, forecast, cache ratio |
| GET | `/admin/usage/users` | `dashboard:read` | All Users tab |
| GET | `/admin/usage/sessions` | `dashboard:read` | All Sessions tab |
| GET | `/admin/usage/queries` | `dashboard:read` | All Queries tab and Overview feed |
| GET | `/admin/usage/queries/{request_id}` | `dashboard:read` | Single-Query Economic Inspector |
| GET | `/admin/usage/export?tab=users\|sessions\|queries` | `usage:export` | Export CSV |
| GET | `/admin/usage/audience-counts` | `dashboard:read` | Audience tab badges (shared header) |
| GET | `/admin/usage/test-ips` | `dashboard:read` | Test IP dialog list |
| POST | `/admin/usage/test-ips` | `testip:write` | Add a test IP / CIDR rule |
| DELETE | `/admin/usage/test-ips/{ip_address}` | `testip:write` | Remove a rule |

## Related pages

- [README.md](README.md), [shell.md](shell.md) (audience switcher, command palette), [dashboard.md](dashboard.md) (cost KPI and day links into this page)
- [visitors.md](visitors.md) (per-IP detail and test IP management), [conversation-logs.md](conversation-logs.md) (full transcripts; its inspector is the same drawer)
- [governance.md](governance.md) (traces opened by **Open full view**), [evaluation.md](evaluation.md), [feedback.md](feedback.md), [analytics.md](analytics.md), [settings.md](settings.md), [user-management.md](user-management.md), [chat.md](chat.md), [documents.md](documents.md), [login.md](login.md)

## Known limitations and in-flight changes

- **No date range.** The Overview summary, All Users and All Sessions are always **all time**. The page sends no `from_date` / `to_date`, even though `GET /admin/usage/summary` accepts them (max 400 days). Only All Queries can be limited to days, and only through the `from` / `to` URL params.
- **One rate card.** There are no per-model prices. Stage splits use the current rates, while totals use the stored per-turn cost (see [Stored vs computed](#stored-vs-computed)).
- **Ingestion cost reads as $0 on the current build.** The summary and Cost by stage read `detail.cost_usd` from `ingest` audit rows, but ingestion writes `embedding_cost_usd` (`backend/app/ingestion/service.py`). Ingestion embedding **tokens** are counted.
- **Inspector latency section never shows.** The drawer reads `detail.latency`, but the endpoint returns `latencies` (`total_ms`, `embedding_ms`, `retrieval_ms`, `llm_ms`). The **Turn Latency Stages** block is therefore always hidden.
- **Inspector row costs double-count the cache.** **2. Prompt Context (Uncached)** multiplies the **full** prompt tokens (cached included) by the prompt rate, and **2b. Cached Context** adds the cached tokens again. The total row is not affected because it shows the stored `cost_usd`. The inspector's fallback cached rate (`0.0625`) and the Overview savings text's fallback also differ from the configured default (`0.125`).
- **Sessions CSV export is likely broken.** `export_usage_csv` calls `get_usage_sessions(limit=USAGE_EXPORT_MAX_ROWS)`, that is 10,000. `get_usage_sessions` rejects any `limit` above `USAGE_PAGE_MAX_LIMIT` (200) with a 422. This happens after the CSV header has been streamed, so the download is cut off after the header. Found by code reading, not run.
- **Labels that overstate:**
  - **Accuracy: x% verified** is token-accounting coverage, not answer accuracy.
  - **Avg Turn Latency** is a mean, despite its "P50 / P95" subtitle.
  - **Total Unique Users** counts IPs.
  - **Top Consumer Share** and the "Top Consumers by Financial Cost" cards follow the current sort.
  - **100% Real Accounting**, **Cockpit Online** and **Live Production Stream** are static text.
- **Hard caps:**
  - table pages of 10/25/50/100 (server max 200 for sessions and queries, and offset max 10,000);
  - Overview feed fixed at 8 rows;
  - export capped at 10,000 rows;
  - the All Users list is built and sorted in Python over every IP (no server-side `limit` cap).
- **Operators see buttons they cannot use:** **Export CSV** and **Add Rule** / **Delete Rule** are not hidden for roles without `usage:export` / `testip:write`. The server returns 403.
- Coming in branch `perf/dashboard-load-speed`:
  - the summary hook moves to the app-wide query cache (`useApiQuery`, 15 s stale-while-revalidate), shared with the Dashboard's prefetch, so revisits render instantly;
  - the backend summary runs its six aggregates in parallel and uses a per-key single flight instead of one global lock. Formulas are unchanged;
  - a partial index speeds up the All Queries list.
- Coming in branch `fix/shell-nav-logout-theme-dates`:
  - all timestamps render through `RelativeTime` ("3h ago", full local time with timezone in the tooltip, naive server times read as UTC);
  - All Queries gains a sortable **Asked** column;
  - All Users gains a sortable **Last Active** column (latest user message), and its first card is renamed **Unique client IPs** with the subtitle **All time, audience: <scope>**;
  - test IP **Configured At** shows relative time.
- Coming in branch `feat/realistic-demo-data`:
  - ingestion audit rows also write `cost_usd`, so ingestion cost appears in **Net Platform Spend**, **Embeddings** and **Cost by stage**;
  - a demo seeder fills every chart with realistic data.
