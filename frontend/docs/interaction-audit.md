# Interaction audit (owner report: "when clicking most of the things are not working")

Branch `fix/owner-click-audit-analytics`. For every interactive element on the admin pages and in Chat, the audit read the code and checked five things:

- **(a) Handler.** A handler exists and does something.
- **(b) Route.** The target route exists.
- **(c) Params.** The query params it sets are parsed back by the target page into the same state.
- **(d) API.** The API call exists in the backend with matching path, method, param names and enum values.
- **(e) Errors.** Errors surface as an ErrorState or toast, not silently.

Parameterised GETs were also probed in-process against the owner's database. That probe was read-only and ran with superadmin, using realistic ids taken from that database.

**Status values:**
- **Broken:** the click did nothing, or did the wrong thing.
- **Partial:** it worked but lost state, or failed silently.
- **Fixed:** fixed on this branch.
- **fx-data / fx-docs:** owned by the parallel workstream of that name (backend dashboard/visitor/conversation endpoints, and the Documents pages).
- **Open:** still to do (see "Remaining gaps").

## Summary

Counts are table rows. One row can cover several elements of the same kind.

| | Rows |
|---|---|
| Broken or partial interactions found | 49 |
| Fixed on this branch | 38 |
| Owned by fx-data (backend widgets and filters not served yet: dashboard, Visitors, Conversations) | 3 |
| Owned by fx-docs (Documents page params) | 1 |
| Open (minor or data-dependent) | 7 |

Confirmed working without changes: about 150 elements. Highlights:
- Sidebar, palette, bell, profile menu and theme toggle.
- Governance tab switching, Traces filters, cursor paging and the trace drawer.
- All Feedback Overview chart clicks.
- User Management actions.
- Settings (branding and taxonomy).
- Analytics cross-filters and `setDay`.

## Table

| Page | Element | Expected | Status before | Fix |
|---|---|---|---|---|
| Dashboard | "Incident / Degraded" health pill | Opens the governance overview | Broken: opened Monitoring | Links to `/admin/governance?tab=Overall` |
| Dashboard | KPI cards | Whole card opens the owning page with the same range | Partial: sparkline area dead; range dropped | Sparkline click navigates; href carries `range/from/to` |
| Dashboard | "Queries by outcome" day click | Usage → Queries limited to that day | Broken: `from`/`to` ignored by page and API | Usage page parses `from`/`to`; API `from_date`/`to_date` (a date covers the whole day); clearable "Day" chip |
| Dashboard | Latency, cost, satisfaction, top-intents widgets; activity feed; date picker | Charts with drill-downs | Broken: fields and `/dashboard/activity` not served | fx-data |
| Usage | Link to `/admin/usage?tab=…` while on Usage | Switches tab and filters | Broken: URL read only on mount | Re-reads the URL on every client navigation |
| Usage | IP / session filter chip, pill X, Reset | Clears the filter | Broken: an effect put it straight back | IP, session and days are owned by the URL; clear writes the URL |
| Usage | Overview "Blocked" outcome pill | Filters the feed; shows the count | Broken: sent `blocked_guardrail` (API uses `blocked`) | Uses `blocked` |
| Usage | Queries "Latency" sort | Sorts by latency | Broken: key mismatch, no API sort | Column key `latency`; API `latency_desc/asc` |
| Usage | Queries ascending sorts (cost, tokens, date) | Ascending order | Partial: API ignored `*_asc` | API orders by the column and direction |
| Usage | Sessions "Total Tokens" sort | Sorts | Broken: 422 | API accepts `tokens_*` |
| Usage | Users "Sessions" sort | Sorts by sessions | Partial: sorted by cost | API sorts by sessions |
| Usage | Users / Sessions "Export CSV" | Downloads rows | Broken: server crashed mid-stream (stray `actor=`) | Fixed call; export honours `type` and the date range |
| Usage | Users tiles and top-3 cards | Values; clickable cards | Broken: read `total_queries` / `top_3_users` | Reads `queries_driven` / `top_users` |
| Usage | Sessions tiles and top-3 cards | Values; clickable cards | Broken: wrong keys; no `tokens` | Reads `avg_cost_per_session` / `max_depth` / `top_sessions`; API adds `tokens` |
| Usage | Queries stat tiles and outcome pill counts | Real numbers | Partial: API sent only `total_queries` | API returns outcome counts, groundedness pass rate, average latency and average tokens over the filtered set |
| Usage | Client-IP click → IP filter | That IP's queries | Partial: filtered on the message IP only | Filters on the displayed IP (message IP, else session IP) |
| Usage | Delete a CIDR test-IP rule | Deletes it | Broken: proxy rejected `%2F` | Path encoded per segment |
| Usage | "Telemetry Focus" buttons | Focus one domain | Partial: only a ring | Dims the other cards, scrolls into view, `aria-pressed` |
| Usage | Table page / sort / search in the URL | Shareable, back-safe | Partial: never written | `useTableState` writes the URL |
| Usage | Test-IP change → open table refresh | Table reflects the change | Partial | Open |
| Usage | "Prompt cache savings" | Real savings | Partial: API hard-codes 0 | Open (data) |
| Entity links | Session link | That session's turns | Partial: Sessions tab has no session filter | Opens `tab=queries&session=` |
| Evaluation | Pass-rate run marker | Opens that run | Broken: `/admin/evaluation?run=` not parsed | Links to `/admin/evaluation-live?run=`; the page reads `?run=` (banner) and `?tab=` |
| Evaluation | Error banner after a successful refetch | Clears | Partial | Clears on success |
| Evaluation | Inspector drawer and compare matrix | Real values or "—" | Broken: invented numbers; errors swallowed | "—" for unreported values; load error alert |
| Governance | Toolbar "Reset" / "Clear all" (Traces, Logs, Review Queue) | Clears every filter | Broken: only the last filter cleared (stale URL per write) | `useDeepLink.set` composes writes in one tick |
| Governance | Monitoring count tiles (8) | Open the rows behind the number | Broken: hover lift, no link | Each tile links to Traces / Logs / Usage (range carried) |
| Governance | Monitoring guardrail-flag and outcome-mix rows | Open matching traces | Broken: no handler | Rows link to `status=blocked` / `outcome=<x>` traces |
| Governance | Overall "Error rate", "Active users", "Cost today" | Rows behind the KPI | Partial: wrong targets | Failed traces; Usage users; Usage queries |
| Governance | Logs "Export CSV" on 403 / 422 | Error toast | Partial: saved a JSON error body | Fetch + blob with an error toast |
| Governance | Logs "Reset" with only level chips active | Reset button shown | Partial | Open |
| Governance | "View trace" for answers without a latency record (283 of 3,901 locally) | Trace drawer | Partial: drawer shows "Trace not found" | Open (data) |
| Feedback | Tabs while `?message_id=` is set | Switch tab | Broken: snapped back to the queue | Switching tab drops `message_id` |
| Feedback | `?message_id=` deep link | Opens that rating | Partial: only searched `status=new` | Searches all statuses |
| Feedback | "Save correct answer" | Stored | Broken: API dropped the field (422) | API accepts `corrected_answer` (PII-masked) |
| Feedback | Clearing a review note | Cleared | Broken: 422 | Explicit `null` clears |
| Feedback | Row, bulk and keyboard triage failures | Error toast | Partial: silent rollback | Toast on every non-409 failure |
| Feedback | Sort / filter change on page ≥2 | Page 1 of the new order | Partial: 422 flash | Cursor reset in the same render |
| Feedback | Golden cases beyond 100 | Reachable | Partial | Open |
| Feedback | Knowledge gaps "Open document" / "Add document" | Documents filtered / upload | Broken: `q` / `action` not read | fx-docs |
| Visitors | Heatmap, stat cards, new vs returning, sorts, status filter, detail timeline | Data and drill-downs | Broken: endpoints and params missing | fx-data |
| Conversations | Stats (500 via `/{id}` route), turn and outcome filters, date range, detail turns | Data and drill-downs | Broken: endpoints and params missing | fx-data |
| User Management | Audit trail tab and filters | Auth events | Partial: 500 newest rows of all actions, filtered client-side | `GET /governance/audit-log?category=auth` |
| Chat | New / rename / delete / open chat failures | Error toast | Partial: silent | Toasts |
| Chat | "View trace" on saved answers | Link after reload | Partial: live answers only | Session detail returns `request_id` |
| Chat | Rating with no saved chat | Error | Partial: false "Thanks" | Throws, so the error shows |
| Chat | Delete exchange failure | Error and rollback | Partial | Open |
| Analytics | Page while the classifier backfill runs | Charts for labelled data plus a progress banner | Broken: full-page empty state | Banner over the charts; polls every 30 s and refreshes the charts as turns are labelled |
| Analytics | Overview layout vs reference HTML | KPIs → trend → hour + flow → topics + quality → drop-off + people → table | Partial: table missing | Conversations table added to the end of Overview |

## Remaining gaps

These items are still open:

- **Test-IP change refresh:** changing test IPs does not refresh an open Users / Sessions / Queries table.
- **Prompt cache savings:** the API hard-codes the cache savings to 0.
- **Logs "Reset" visibility:** Reset is hidden when only level chips are active.
- **"View trace" without a latency record:** these answers 404; this is a data gap.
- **Golden cases:** no paging beyond 100.
- **Chat delete-exchange:** no rollback when the delete fails.
- **Mobile:** below `md` there are no audience tabs or date picker (shell).
- **Analytics tour:** the reference HTML opens the guide tour automatically on first load. Here the tour lives on the Guide tab, per newplan §6.4.
