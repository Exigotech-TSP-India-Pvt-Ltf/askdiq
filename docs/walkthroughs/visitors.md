# Visitors

> Who is using the assistant, as one row per client IP address, with a detail page per visitor and test-IP management.  Route: `/admin/users` and `/admin/users/[id]` · Nav: **Visitors** · Permission: `dashboard:read` (operator, admin, superadmin); marking and managing test IPs needs `testip:write` (admin, superadmin)

## Purpose and who uses it

A **visitor** is one client IP address. Sessions are grouped on `chat_sessions.created_ip`. There is no login or client-id identity on this page today (`backend/app/entities/visitors.py`).

- **Analysts** see how many visitors came in a period, how many were new or returning, and when in the week they are active.
- **Support** finds one visitor's sessions, questions, costs and ratings, and jumps to the full conversation or the query inspector.
- **Admins** keep internal and QA traffic out of the production numbers by marking IPs (or CIDR ranges) as **test IPs**. Every audience-scoped page then counts that traffic under **Testing** instead of **Users**.

This is not the page for managing sign-in accounts and roles. That is **User Management** (`user-management.md`, `users:manage`).

## How to get there

- **Sidebar**: **Visitors** (people icon), shown with `dashboard:read`.
- **Command palette** (Ctrl/Cmd+K): "Visitors", or the keywords `users`, `audience`, `ip` or `test ip`.
- From the Dashboard: the **Total Users** KPI card opens `/admin/users` with the range carried over.
- **URLs**:
  - List: `/admin/users`
  - Detail: `/admin/users/client-<ip>` (for example `/admin/users/client-203.0.113.7`), or `/admin/users/<user-uuid>` for a signed-in user. Ids are URL-encoded (`visitorDetailHref`).

List URL parameters (`frontend/lib/use-table-state.ts`, table key `users`):

| Param | Allowed values | Default | Effect |
|---|---|---|---|
| `users_q` | text | empty | IP search (debounced 300 ms) |
| `users_page` | 1.. | 1 | Page |
| `users_limit` | `10`, `25`, `50`, `100` | `10` | Page size |
| `users_sort` | `last_active_desc`, `last_active_asc`, `queries_desc`, `queries_asc`, `sessions_desc`, `sessions_asc` | `last_active_desc` | Sort (see limitations) |
| `weekday`, `hour` | `0`-`6` (0 = Mon), `0`-`23` | none | Heatmap cell filter (both together) |
| `range` / `from` / `to` | as on every admin page | `7d` | Global date range |
| `audience` | `overview`, `users`, `testing` | `overview` / last used | Audience scope |

The **Status** and **Visitor Type** filters are not stored in the URL. They reset to "All" when you reload.

Detail URL parameters: `inspect=<request_id>` opens the query inspector drawer; `range` / `from` / `to` set the range.

## Layout at a glance

List (`/admin/users`):

```text
+--------------------------------------------------------------------------+
| [#] Visitors          [Overview|Users|Testing] [Last 7 days v] [Manage test IPs]
|     Audience-scoped visitors by client IP: activity, sessions and ...    |
+--------------------------------------------------------------------------+
| Visitors      | New visitors  | Returning visitors | Active now          |
| 120 Last 7 d  | 30            | 90                 | 4  Last 30 minutes  |
+------------------------------------+-------------------------------------+
| New vs returning (stacked bars)    | When visitors are active (heatmap)  |
| Visitors per day                   | Mon..Sun x 00..23 UTC               |
+------------------------------------+-------------------------------------+
| [Search client IP address...] [Status v] [Visitor Type v]                |
| Active in [Mon 09:00-10:00 x]                                            |
| Client IP Address | Status | Sessions | Queries | Feedback | First Seen  |
|   | Last Active | Actions [list] [flask]                                 |
| ...                                                   < 1 2 3 >  10/page |
+--------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/visitors-list.png` — Visitors list on "Last 7 days", Users audience, KPI cards, both charts and the table with a Test badge visible.

Detail (`/admin/users/client-<ip>`):

```text
+--------------------------------------------------------------------------+
| [#] 203.0.113.7   Visitors > 203.0.113.7   [Last 7 days v]               |
|                                         [View sessions] [Conversations]  |
+--------------------------------------------------------------------------+
| Sessions | Queries | Feedback | First seen     | Cost  $0.0123           |
|          |         |          | Last active .. | ~~~sparkline~~~         |
+--------------------------------------------------------------------------+
| Activity timeline (session bars on a time axis)                          |
+--------------------------------------------------------------------------+
| Sessions  [Search sessions...]                                           |
| Session | Started | Last Activity | Messages                             |
+----------------------------------------------+---------------------------+
| Queries (most recent first)                  | Feedback                  |
| Query | Outcome | Turn | Latency | Cost |When| 2 / 5 [wrong] comment...  |
+----------------------------------------------+---------------------------+
```

> Screenshot placeholder: `img/visitors-detail.png` — one visitor with several sessions, the timeline, queries with mixed outcomes and one feedback card.

> Screenshot placeholder: `img/visitors-test-ip-modal.png` — the "Manage Test IP Rules & Audience Segmentation" dialog with one custom rule and the default loopbacks strip.

## Layout tour

### Header (list)

- **Visitors**, "Audience-scoped visitors by client IP: activity, sessions and engagement".
- Audience tabs and date range picker: the same controls as the Dashboard (`dashboard.md`).
- **Manage test IPs** button: shown only with `testip:write` (`useCan("testip:write")`). It opens the test IP dialog (see below).

### KPI cards (list)

Source: `GET /admin/visitors/stats?audience=&from=&to=` (`backend/app/api/routes/admin_entities.py::visitor_stats`, `backend/app/entities/visitors.py::visitor_stats`). The range is sent as `from` / `to` **days** (`YYYY-MM-DD`), taken from the browser's local calendar dates of the selected range (`useRangeParams`). The server reads them as inclusive **UTC** days.

Note: a preset such as **Last 7 days** is sent as "the local date 7 x 24 h ago" through "today". That is **8 calendar days** inclusive.

- **Visitors** (subtitle: the range label). The number of distinct client IPs (`chat_sessions.created_ip`) that were active on at least one day in the range. "Active on a day" means the IP started a session or sent a user message that UTC day (`new_vs_returning`, rollup total).
- **New visitors**: those visitors whose **first-ever** session (all time, `MIN(chat_sessions.created_at)` for the IP) falls inside the range.
- **Returning visitors**: `Visitors - New visitors`.
- **Active now** (subtitle "Last 30 minutes"): the number of distinct `created_ip` with a session whose `last_activity_at` is within the last 30 minutes (`active_now`, `ACTIVE_NOW = 30 min`).
  - It is independent of the range.
  - A session's `last_activity_at` moves only when a message is exchanged, so this means "had an exchange in the last 30 minutes".
- Audience, for all four cards: the filter is on `chat_sessions.created_ip`.
  - **Users** excludes test IPs.
  - **Testing** keeps only test IPs, because NULL IPs are already excluded by the visitor scope.
  - **Overview** counts everything.
- The response also has `kpis.sessions` (sessions started in the range), `kpis.queries` (user messages in the range) and `kpis.avg_sessions_per_visitor`. They are **not displayed**.
- Fallback when the stats endpoint is missing (404/405/501) or errors: three cards built from the current table page instead.
  - **Total filtered visitors**: the table total.
  - **Active on this page**: rows that look active.
  - **Queries on this page**: the sum of the Queries column on the page.

### **New vs returning** (stacked bars)

- Subtitle "Visitors per day". The window is the range label.
- One bar per UTC day of the range (zero-filled).
  - **New**: visitors whose first-ever session started that day.
  - **Returning**: visitors active that day whose first session was on an earlier day.
  - Each visitor counts once per day, so the daily bars do not add up to the **Visitors** KPI. A returning visitor appears on every day they were active.
- Empty: "Visitor stats are not available yet" when the endpoint is missing, otherwise the default "No data for this period".

### **When visitors are active** (heatmap)

- Subtitle "Sessions by weekday and hour (UTC); select a cell to filter the table".
- Rows are **Mon** to **Sun** and columns are hours **00** to **23**, in **UTC**. A cell is the number of sessions **started** in that slot within the range (`session_heatmap`, Postgres `isodow` and `hour` of `chat_sessions.created_at`). Audience as above.
- **Click a cell**: sets `?weekday=&hour=` and resets the table to page 1. The table then keeps only visitors with a session started in that UTC slot within the range (`ips_in_slot`). A chip "Active in **Mon 09:00–10:00**" appears above the table, and its **x** ("Clear filter: ...") removes the filter.

### Visitor table

Source: `GET /admin/users?audience=&q=&status=&type=&sort=&limit=&offset=[&weekday=&hour=&from=&to=]` (`backend/app/api/routes/admin.py::list_users`). The table is **not limited by the date range**: all counts are all time. The range only applies to the heatmap filter.

Toolbar:
- Search box "Search client IP address…": a case-insensitive substring match on the IP.
- **Status** filter: **All Statuses**, **Active Only**, **Inactive Only** (see limitations: it has no effect today).
- **Visitor Type** filter: **All Visitors**, **Production**, **Test**.

Columns:

| Column | Meaning (all time unless noted) | Source |
|---|---|---|
| **Client IP Address** | The IP, as a link to the visitor detail, plus a badge: **Test** (amber) or **Production** (green). | `created_ip`; Test = matched by the test-IP rules, or a literal `127.0.0.1`, `::1`, `localhost`, `0.0.0.0` |
| **Status** | **Active** / **Inactive** badge, computed in the browser as "server status is `active`, or last active under 30 minutes ago". The server sends `"Active"` for every row, so this always shows **Active**. | `isActive()` in the page |
| **Sessions** (sortable) | Sessions created from this IP | `COUNT(chat_sessions)` by `created_ip` |
| **Queries** (sortable) | Messages stored with this IP. Only user messages carry an IP, so this is the number of questions. | `chat_messages.ip_address` |
| **Feedback** | `audit_log` rows from this IP whose action contains "feedback". This includes `user_feedback` and `negative_feedback_spike`, and also admin actions such as `feedback_triaged` / `feedback_exported` done from that IP. | `AuditLog.action ILIKE '%feedback%'` |
| **First Seen** | First session start, shown as a local date | `MIN(chat_sessions.created_at)` |
| **Last Active** (sortable) | Latest session activity, shown as a local date-time | `MAX(chat_sessions.last_activity_at)` |
| **Actions** | **View sessions** icon (list), plus the **Mark as test IP** flask icon (with `testip:write`, non-test rows only) | see below |

- Clicking a row (or the IP link) opens the visitor detail. The **View sessions** icon opens `/admin/usage?tab=sessions&ip=<ip>` (Usage & Cost, Sessions tab filtered to this IP).
- Pagination: 10 / 25 / 50 / 100 per page. The server loads every IP group, then filters, sorts and slices in Python.
- Audience: rows are kept or dropped in Python by the Test/Production classification. **Users** hides Test rows, **Testing** shows only Test rows, and **Overview** shows all. Visitors with no `created_ip` never appear.
- Empty: "No visitors found matching current audience and filters." Error: "Failed to load visitors." with retry.

### Mark as test IP (row action)

- Flask icon, tooltip "Mark as test IP" (aria "Mark <ip> as test IP"). Shown only with `testip:write`, and only on rows that are not already Test.
- It calls `POST /admin/usage/test-ips` with `{ "ip_address": "<ip>", "description": "Marked from Visitors" }` (`testip:write`).
  - The row flips to **Test** immediately (optimistic).
  - A success toast "<ip> marked as a test IP", "Its traffic now counts under Testing." offers **Undo**.
  - **Undo** calls `DELETE /admin/usage/test-ips/<ip>`, then shows "<ip> counts as a production visitor again".
  - On failure: "Couldn't mark <ip> as a test IP".
- Effect: the backend writes `test_ip_configs` and an `audit_log` row (`test_ip_added`, or `test_ip_updated` if the rule existed). It then clears the audience classification cache in this process and publishes a cache-invalidate event to other workers. All historical data for that IP is re-classified, because the classification is computed at query time and not stored per row.

### Manage test IPs (dialog)

Opened with **Manage test IPs** (`testip:write`). Title: **Manage Test IP Rules & Audience Segmentation** (`frontend/components/admin/usage/TestIpManager.tsx`). The same dialog is used on Usage & Cost.

- Banner **Audience Re-classification Guarantee**, with the server's note: "Historical query metrics, sessions, and cost distributions are dynamically re-classified when test IP rules change."
- Form **Add New Test IP / CIDR Subnet Rule**:
  - **IP / CIDR / Prefix \*** (placeholder "e.g. 192.168.1.0/24 or 10.0.0.1", max 64 characters)
  - **Description / Team Purpose** (placeholder "e.g. QA team automated runner or Office VPN", max 200 characters)
  - **Add Rule** button
  - Success message: `Test IP rule "<ip>" added successfully.`
  - The server accepts an IPv4 or IPv6 address, a CIDR block, `localhost`, or a `client-...` value. Anything else returns 422: "'<x>' is not a valid IPv4/IPv6 address or CIDR network block." There is a limit of `TEST_IP_MAX_ENTRIES` rules (default 500) (`backend/app/governance/test_ips.py::validate_test_ip_string`).
- Table **Active Custom Rules (N)**: filter box "Filter custom rules by IP or description...". Columns **IP / CIDR Rule**, **Description & Purpose**, **Configured At** and **Action** (trash icon, aria "Delete <ip>").
  - Deleting asks "Confirm removal of rule **<ip>**? History will be re-classified." with **Cancel** / **Delete Rule**. Success: `Rule "<ip>" removed.`
  - Empty: "No custom test IP rules configured."
- Strip **Default System Loopbacks & Non-Removable Test Subnets**: `DEV_TEST_IPS` from config plus the built-in `127.0.0.1`, `::1`, `localhost`, `0.0.0.0` and `unknown`. These cannot be removed from the UI.
- **Close** / **Close modal** closes the dialog. Changes refresh the Visitors queries (`invalidate("visitors/")`).

### Visitor detail (`/admin/users/[id]`)

Header: the title is the IP (or "Visitor <first 8 chars of id>" when there is no IP), with the breadcrumb **Visitors** > <ip>. Audience tabs are hidden on this page, and nothing on it is audience-filtered. The date range picker is shown.

Header actions (when the IP is known):
- **View sessions**: `/admin/usage?tab=sessions&ip=<ip>`
- **Conversations**: `/admin/conversations?ip=<ip>` (Conversation Logs filtered to this IP)

Stat cards, from `GET /admin/users/{id}` (`admin.py::get_user`), all time:
- **Sessions**: sessions created from the IP.
- **Queries**: messages with this IP (user questions).
- **Feedback**: the same `audit_log` "feedback" count as the list.
- **First seen**: the first session start, with the subtitle "Last active <latest activity>".
- **Cost**: the sum of the timeline's daily cost series, which is exchange `cost_usd` (same source as the Dashboard Cost KPI) for this visitor's sessions, **in the selected range**. A sparkline shows cost per UTC day, and the hint is the range label. It shows "Per-visitor cost is not available yet" when the timeline endpoint is missing.

For a user-UUID visitor, the cards count sessions where `chat_sessions.user_email` equals the user's email, and **First seen** is the account creation time.

**Activity timeline** (waterfall):
- Subtitle "Sessions on a time axis; select a bar to open the conversation".
- One bar per session, from its start to its last activity (minimum about 3 minutes wide). Bars are placed in hours from the first session. Only the **20 most recent** sessions are drawn.
- Data: sessions **active in the range** from `GET /admin/visitors/{id}/timeline` (`timeline_sessions`, max 200). If that endpoint is missing, it falls back to all sessions from the detail endpoint.
- Clicking a bar opens `/admin/conversations/<session id>`. The table view lists **Session**, **Started** and **Duration**.
- Empty: "No sessions for this visitor".

**Sessions** table ("Conversations started from this visitor."):
- Rows: every session for the visitor, all time, newest activity first. Search "Search sessions…" matches the title or session id. Paged 10 / 25 / 50 / 100 in the browser.
- Columns: **Session** (title, or "Untitled Conversation", with the id underneath), **Started**, **Last Activity** and **Messages**.
- A row click opens the conversation.
- Empty: "No sessions found for this visitor."

**Queries** ("Most recent first; select one to open the query inspector."):
- From the timeline endpoint: up to **100** questions asked in the range (`timeline_queries`), newest first.
- Columns:
  - **Query** (first 500 characters)
  - **Outcome** chip: answered (green); ungrounded (amber); blocked or llm_failed (red); others, or "unknown" when not recorded (grey)
  - **Turn** (`#n`, a link to that turn in the conversation)
  - **Latency** (end-to-end `latency_metrics.total_ms`, "ms" or "s")
  - **Cost** (USD, up to 4 decimals)
  - **When**
- A row click sets `?inspect=<request_id>` and opens the **query inspector** drawer (token, cost and latency breakdown). Closing it clears the param.
- States: "Couldn't load queries." (retry), "Per-visitor queries are not available yet" (endpoint missing), "No queries in this range."

**Feedback** ("Ratings this visitor gave."):
- Up to **100** `message_feedback` ratings created in the range, newest first.
- Each card shows:
  - a rating chip "<n> / 5" (4-5 green, 3 amber, 1-2 red)
  - reason tags
  - the time and the comment
  - links **Open in Feedback** (`/admin/feedback?message_id=<assistant message id>`) and **View turn #n**
- States: "Per-visitor feedback is not available yet", "No feedback in this range".

Not found: "Visitor not found", "The requested visitor could not be found." with **Back to Visitors**. Other errors show "Couldn't load this visitor." with retry.

### Time and timezone

- Day buckets (charts, heatmap, cost series) are **UTC**.
- Timeline timestamps (`started_at`, query and feedback `created_at`) carry a `Z` and display correctly in your local time.
- **First Seen** and **Last Active** (list) and **First seen**, **Last active**, **Started** and **Last Activity** (detail stat card and Sessions table) come from `/admin/users` endpoints as **naive** timestamps without `Z`. The browser reads them as local time, so they are shifted by your UTC offset (for example 10 hours early in AEST). See limitations.

### Refresh and caching

- Client cache (`useApiQuery`): data is fresh for 30 s and revalidated on window focus / tab visible. There is no polling and no refresh button.
- `GET /admin/visitors/stats` returns a weak ETag with `Cache-Control: private, max-age=15`.
- The list, the detail and the timeline are not HTTP-cached.
- Test-IP classification is cached in-process for 30 s (`AUDIENCE_CACHE_TTL_SECONDS`) and invalidated on every rule change.

## Common tasks

1. **Find a visitor's conversations**
   1. Open **Visitors** and pick the audience (**Overview** to be sure you see everyone).
   2. Type part of the IP in "Search client IP address…".
   3. Click the row. The **Sessions** table lists every session from that IP (all time). Click one to open the full conversation in Conversation Logs.
   4. Alternatively, use **Conversations** in the detail header for the Conversation Logs list filtered to `ip=<ip>`, or **View sessions** for Usage & Cost sessions with token and cost columns.
   5. To read a single question's pipeline details, set the range to cover it and click it in **Queries** to open the inspector.
2. **Mark an IP as test** (needs `testip:write`, admin or superadmin)
   1. Open **Visitors**, find the row (search by IP) and click the flask icon **Mark as test IP**.
   2. Check the toast "<ip> marked as a test IP". Click **Undo** within the toast if it was a mistake.
   3. For a whole office or QA subnet, click **Manage test IPs**, enter a CIDR such as `10.20.0.0/16` and a description, then **Add Rule**.
   4. Switch to the **Users** audience. The IP (or subnet) no longer appears, and Dashboard and Usage numbers exclude it within about 30 s (immediately on this server process).
3. **Un-mark a test IP**
   1. **Manage test IPs**, find the rule in **Active Custom Rules**, click the trash icon, then **Delete Rule**.
   2. Rules shown under **Default System Loopbacks & Non-Removable Test Subnets** come from config (`DEV_TEST_IPS`) and need a deployment change.
4. **See who is active on Monday mornings**
   1. Pick **Last 30 days**.
   2. In **When visitors are active**, click the Monday 09 cell. Hours are UTC, so convert from your local time first.
   3. The table now shows only IPs with a session started in that slot. Clear the "Active in ..." chip to reset.
5. **Check one visitor's spend**
   1. Open the visitor and choose the range. The **Cost** card shows the total and its daily sparkline.
   2. The **Queries** list shows the cost per question.

## Interactions and states

- **Permissions**:
  - Read is `dashboard:read`, checked on every endpoint here (`require_permission(Permission.DASHBOARD_READ)`).
  - `POST` and `DELETE /admin/usage/test-ips` require `testip:write`. Operators do not see **Manage test IPs** or the flask icon, and the API returns 403 if called directly.
  - `GET /admin/usage/test-ips` needs only `dashboard:read`.
- **Exports**: none on this page. Per-IP usage export lives on Usage & Cost (`usage:export`).
- **Keyboard**: everything is reachable by Tab; chart cards have **View as table**; Esc closes drawers.
- **Loading**: table skeleton rows; chart skeletons; "Loading visitors…" and "Loading visitor…" fallbacks; "Loading queries" and "Loading feedback" skeletons on the detail page.
- **Missing data**:
  - A visitor with no `created_ip` cannot appear.
  - A user-UUID visitor with no IP has no header actions.
  - Charts show "Visitor stats are not available yet" only when the backend lacks `/admin/visitors/stats`.

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/admin/users?audience=&q=&status=&type=&sort=&limit=&offset=&weekday=&hour=&from=&to=` | `dashboard:read` | Visitor table |
| GET | `/admin/visitors/stats?audience=&from=&to=` | `dashboard:read` | KPI cards, new vs returning, heatmap |
| GET | `/admin/users/{id}` | `dashboard:read` | Detail stat cards and Sessions table (`id` = `client-<ip>` or user UUID) |
| GET | `/admin/visitors/{id}/timeline?from=&to=` | `dashboard:read` | Timeline bars, Cost card, Queries, Feedback |
| GET | `/admin/usage/test-ips` | `dashboard:read` | Test IP dialog contents |
| POST | `/admin/usage/test-ips` | `testip:write` | Mark as test IP, Add Rule |
| DELETE | `/admin/usage/test-ips/{ip_address}` | `testip:write` | Undo, Delete Rule |
| GET | `/admin/usage/audience-counts` | `dashboard:read` | Audience tab tooltips |
| GET | `/admin/usage/queries/{request_id}` | `dashboard:read` | Query inspector drawer (see `usage-cost.md`) |

The browser calls these through `/api/proxy/...` (`frontend/lib/entity-stats-api.ts`, `frontend/lib/api.ts`). The contract is `docs/technical/frontend/contracts/entity-stats.md`.

## Related pages

[README](README.md) · [Shell](shell.md) · [Login](login.md) · [Dashboard](dashboard.md) · [Chat](chat.md) · [Documents](documents.md) · [Usage & Cost](usage-cost.md) · [Conversation Logs](conversation-logs.md) · [Feedback](feedback.md) · [Evaluation](evaluation.md) · [Governance](governance.md) · [Analytics](analytics.md) · [User management](user-management.md) · [Settings](settings.md)

## Known limitations and in-flight changes

- **Status column and filter do nothing useful.** `GET /admin/users` returns `"status": "Active"` for every row and ignores the `status` query parameter. Every row shows **Active**, and **Inactive Only** still returns all rows.
- **Sorting is by last activity only.** The **Sessions** and **Queries** headers are clickable, but the server sorts every request by `last_active` (it only honours asc/desc).
- **Times on the list and the detail stat card are shifted.** Naive UTC timestamps are parsed as local time (see Time and timezone).
- **Messages column is always 0** for IP visitors (`client-<ip>`). The detail endpoint hard-codes `message_count: 0`. User-UUID visitors get real counts.
- **Feedback count is not ratings.** It counts `audit_log` actions containing "feedback" from that IP, which includes admin triage and export actions. Use the detail **Feedback** section (real `message_feedback` ratings) for what the visitor gave.
- **The table is all time.** Only the heatmap filter uses the date range, and the KPI cards and charts use the range. The table therefore does not "add up" to the cards.
- **Off-by-one range.** Presets are sent as local calendar days and read as UTC days, so "Last 7 days" covers 8 days, and the edges can shift by your UTC offset.
- **Hard caps on the timeline**: 200 sessions, 100 queries and 100 feedback items per request. The response flags `truncated`, but the UI does not show it. The waterfall draws at most 20 sessions.
- **Scale**: `/admin/users` loads every IP group and the per-IP counts, then filters and pages in Python. `limit` has no server maximum.
- **API bug**: sending only one of `weekday` / `hour` should return 422. The handler's `status` query parameter shadows FastAPI's `status` module, so that path raises an error (500) instead. The UI always sends both.
- **Mark as test IP** updates the row optimistically but only invalidates the stats query. On the **Users** audience the row stays visible (with a Test badge) until the list refetches.
- **Contract drift** (`docs/technical/frontend/contracts/entity-stats.md`): it states every timestamp carries a UTC offset. That holds for the new stats and timeline endpoints but not for `GET /admin/users` and `GET /admin/users/{id}`.
- Coming in branch `fix/shell-nav-logout-theme-dates`:
  - **Active now** becomes **Active visitors now**, "Last 5 minutes", with an info tooltip. The definition: distinct end users with at least one **query** in the last 5 minutes. The identity is the client id / signed-in user, otherwise the IP, and test IPs are always excluded except on Testing (`backend/app/dashboard/active_visitors.py`).
  - A new **Active visitors** panel, "As of now, independent of the date range", appears under the KPI cards with **Daily active visitors**, **Weekly active visitors** and **Monthly active visitors** (last 24 h / 7 d / 30 d), each with "+x % vs previous ...".
  - The server honours the **Sessions**, **Queries**, **Last Active** and new **First Seen** sorts.
  - All dates on both pages (list, Sessions table, Queries **When**, Feedback, test-IP **Configured At**) use the shared relative-time display ("3h ago", with the full local date-time and timezone in the tooltip) and read naive server timestamps as UTC. This fixes the timezone shift.
- Coming in branch `perf/dashboard-load-speed`: no change to this page's numbers. The admin shell paints in the first response, so the page skeleton appears sooner.
- Coming in branch `feat/realistic-demo-data`: the demo seeder (`backend/scripts/seed_realistic_activity.py`) creates about 2,000 visitors over 120 days (mostly public IPv4/IPv6 addresses). About 4 % of traffic comes from test IPs, covered by two seeded rules, `10.20.0.0/16` ("seed: QA automation subnet") and `192.168.50.0/24` ("seed: Developer sandbox"). On a seeded database the **Testing** audience and the **Test** badge are populated. `--purge` removes the seeded sessions and rules.
