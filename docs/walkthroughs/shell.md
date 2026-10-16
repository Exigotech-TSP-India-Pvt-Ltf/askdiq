# App shell (navigation, header, global controls)

> Everything around the pages: sidebar, mobile bar, page header, audience tabs, date range, command palette, shortcuts, notifications, profile menu, theme, toasts, drawers, tables and the shared caching/refresh rules.  Route: every page under `/admin`, `/documents`, `/chat`, `/evaluation-live` · Nav: all items · Permission: per item (see the role table)

## Purpose and who uses it

The shell is the frame every signed-in user sees. Knowing how it behaves explains most "why does this page show X" questions:

- **Which pages you see** depends on your role (sidebar and palette are permission-filtered).
- **Which data you see** depends on two global filters shared by most dashboard pages: the **audience** (Overview / Users / Testing) and the **date range**.
- **How fresh the data is** depends on the shared request cache and polling rules described at the end.

Source: `frontend/app/(app)/layout.tsx`, `frontend/app/(app)/admin/layout.tsx`, `frontend/app/layout.tsx`.

## How to get there

The shell wraps every page under the `(app)` route group once you are signed in (see [login.md](login.md)). There is nothing to open; this page documents shared controls.

Global URL parameters understood across pages:

| Param | Values | Set by | Notes |
|---|---|---|---|
| `audience` | `users`, `testing` (absent = `overview`) | Audience tabs | Also remembered in localStorage `askdiq.audience_scope`. |
| `range` | `24h`, `30d`, `90d` (absent = `7d`) | Date range picker | Preset, rolling window ending now. |
| `from`, `to` | `YYYY-MM-DD` | Date range picker (custom) | Whole local days, `to` inclusive; wins over `range` when both are valid. |
| `<table>_page`, `<table>_limit`, `<table>_sort`, `<table>_q` | page number; `10/25/50/100`; sort key; search text | Tables using `useTableState` | Per-table prefix, see Tables. |

Command palette: press **Ctrl+K** (Windows/Linux) or **Cmd+K** (macOS) anywhere, or click the palette button in the header.

## Layout at a glance

```text
+------------+-------------------------------------------------------------+
| [LOGO]   < | [i] Page title  (badge)   [Overview|Users|Testing] [Last 7  |
|------------|     subtitle / breadcrumb                       days v]     |
| ADMIN    v |                              [actions] [Cmd] [Bell] (AB)    |
|  Dashboard |-------------------------------------------------------------|
|  Chat      |                                                             |
|  Documents |                     page content                            |
|  Visitors  |                                                             |
|  Usage & C.|                                                             |
|  Conv. Logs|                                                             |
|  Feedback  |                                                             |
|  Eval(Live)|                                                             |
|  Governance|                                                             |
|  Analytics |                                                             |
|  User M. 3 |                                         +-----------------+ |
|  Settings  |                                         | toast           | |
|------------|                                         +-----------------+ |
| (AB) Name  |                                                             |
| email      |                                                             |
| [ROLE] [*][->]                                                           |
+------------+-------------------------------------------------------------+
Below md: sidebar hidden; a top bar shows [=] (mark) ... [Cmd] [Bell] (AB).
```

> Screenshot placeholder: `img/shell-desktop.png` — expanded sidebar for a superadmin (12 items, pending-access badge), Dashboard header with audience tabs and date range.

> Screenshot placeholder: `img/shell-collapsed.png` — collapsed 64 px sidebar with icons only, theme toggle and avatar at the bottom.

> Screenshot placeholder: `img/shell-mobile.png` — mobile top bar and the open off-canvas navigation drawer.

## Layout tour

### Sidebar

`frontend/components/Sidebar.tsx`. Desktop (md and wider) only; below md it becomes an off-canvas drawer.

- **Top**: the brand logo; a **Collapse sidebar** button (chevron). When collapsed (64 px), the logo mark itself is the **Expand sidebar** button. The collapsed state is remembered in localStorage `deployiq.sidebar.collapsed` (`"1"`/`"0"`).
- **Section header**: **Admin** (shield icon, chevron) toggles the whole list open/closed; remembered in `deployiq.sidebar.adminExpanded`. It is shown only when the sidebar is expanded, and it heads every list, including the Chat-only list of users without a role.
- **Nav items** (in this order). An item is shown only if the signed-in role has its permission; items without a permission are shown to everyone signed in and not suspended:

| # | Label | Route | Permission | Extra palette keywords |
|---|---|---|---|---|
| 1 | **Dashboard** | `/admin` | `dashboard:read` | |
| 2 | **Chat** | `/chat` | none | |
| 3 | **Documents** | `/documents` | `dashboard:read` | |
| 4 | **Visitors** | `/admin/users` | `dashboard:read` | users, audience, ip, test ip |
| 5 | **Usage & Cost** | `/admin/usage` | `dashboard:read` | |
| 6 | **Conversation Logs** | `/admin/conversations` | `dashboard:read` | |
| 7 | **Feedback** | `/admin/feedback` | `dashboard:read` | |
| 8 | **Evaluation (Live)** | `/admin/evaluation-live` | `dashboard:read` | |
| 9 | **Governance** | `/admin/governance` | `dashboard:read` | |
| 10 | **Analytics** | `/admin/analytics` | `dashboard:read` | |
| 11 | **User Management** | `/admin/user-management` | `users:manage` | access requests, roles |
| 12 | **Settings** | `/admin/settings` | `branding:write` | branding, taxonomy, white-label |

- **Active item**: highlighted with a brand-coloured bar; matching is segment-aware (`/admin` is active only on `/admin` itself; other items also on their sub-paths, e.g. `/admin/conversations/<id>`).
- **Collapsed**: icons only, the label is the hover tooltip.
- **Badge**: **User Management** shows an amber count of open access requests (`99+` above 99). Only fetched for users with `users:manage` (see Notifications).
- **Footer (expanded)**: avatar with initials, full name, email, role badge, theme toggle and **Sign out** (log-out icon).
- **Footer (collapsed)**: theme toggle and the avatar only (tooltip = name). There is no sign-out button in the collapsed sidebar; use the profile menu in the page header.

Redirects (`frontend/next.config.js`): `/admin/evaluation` and `/evaluation` redirect (307) to `/admin/evaluation-live`. The standalone `/evaluation-live` route still exists but has no nav item.

#### Role -> nav items

Permissions come from `backend/app/auth/permissions.py` via `GET /auth/me`.

| Who | Sees in the sidebar and palette |
|---|---|
| No role (`pending`, `rejected`, or active without a role) | **Chat** only. Any dashboard URL shows **Access required** ([login.md](login.md)). |
| `operator` (`dashboard:read`) | Dashboard, Chat, Documents, Visitors, Usage & Cost, Conversation Logs, Feedback, Evaluation (Live), Governance, Analytics (10 items). Write controls inside pages are hidden. |
| `admin` (+ `documents:write`, `feedback:triage`, `feedback:export`, `eval:run`, `testip:write`, `usage:export`) | Same 10 items as operator; extra actions appear inside pages (uploads, triage, exports, eval runs, **Manage test IPs** on Visitors). |
| `superadmin` (+ `users:manage`, `audit:auth:read`, `branding:write`, `taxonomy:write`) | All 12 items, including **User Management** (with the pending badge) and **Settings**. |
| `suspended` | No shell at all: the suspended page. |

Hiding an item is UX only; every API route checks the permission itself (`require_permission` in `backend/app/auth/deps.py`).

### Mobile top bar and navigation drawer

`frontend/components/shell/MobileTopBar.tsx`. Below md only:

- **Open navigation** (hamburger), the logo mark, then the header utilities (palette, bell, profile menu).
- The drawer (**Navigation** dialog) slides in from the left with the same permission-filtered items (no **Admin** header, no footer), a **Close navigation** button, focus trap, Esc and backdrop click to close. It closes itself after navigating.
- The page headers hide their own utilities below md, so this bar is where the palette, bell and profile menu live on phones.

### Page header (DashboardHeader)

`frontend/components/admin/DashboardHeader.tsx`, used by Dashboard, Documents, Visitors, Usage & Cost, Conversation Logs, Feedback, Evaluation (Live), Governance, Analytics, User Management, Settings and the Access Required view. The Chat page has its own header without these utilities (on desktop, Chat users reach theme and sign-out through the sidebar footer).

- **Left**: icon tile, page **title**, optional count badge, then a subtitle or a breadcrumb trail (detail pages, e.g. a conversation).
- **Centre** (md and wider): the audience tabs (unless a page turns them off or replaces them), plus the date range picker on pages that use it.
- **Right**: page actions, then (md and wider) the header utilities: **Open command palette** button (tooltip **Command palette (Ctrl/⌘ K)**), the notification bell and the profile menu.

### Audience tabs (Overview / Users / Testing)

`frontend/components/admin/AudienceScopeTabs.tsx`, state in `frontend/lib/audience-context.tsx`.

| Tab | Label | Tooltip description | Backend filter on the IP column |
|---|---|---|---|
| `overview` | **Overview** | Complete platform aggregate including all end users and internal tests | none (all rows) |
| `users` | **Users** | Authentic production usage filtered to exclude configured test IPs | IP is not NULL and not a test IP |
| `testing` | **Testing** | Internal benchmarking, evaluation runs, and queries originating from test IPs | IP is NULL or a test IP |

- **Which IPs are "test"** (`backend/app/governance/test_ips.py`): always `127.0.0.1`, `::1`, `localhost`, `0.0.0.0`, `unknown` and anything starting `127.`; plus the backend env `DEV_TEST_IPS` (comma or semicolon list, default `127.0.0.1,::1,localhost,0.0.0.0`); plus database overrides in `test_ip_configs` managed from **Visitors -> Manage test IPs** (admin, `testip:write`; see [visitors.md](visitors.md)). A database row marked inactive removes an env entry. CIDR ranges (e.g. `10.0.0.0/8`) are supported; a `client-` prefix on an id is ignored.
- **Classification** (`backend/app/governance/audience.py::get_classified_ips`): every distinct IP seen in chat messages, chat sessions and the audit log is classified as test or user; cached in-process for `AUDIENCE_CACHE_TTL_SECONDS` (default 30 s) and cleared when test IPs change.
- **Rows without an IP count as Testing**, not Users.
- **Badges and tooltips** (`GET /admin/usage/audience-counts`, `backend/app/api/routes/admin_usage.py::get_audience_counts`): the pill on each tab is the number of distinct IPs. Hover text: `<Tab>: <n> Users|Testers · <q> Queries · <s> Sessions` plus the description.
  - Queries = assistant messages paired with a user turn; Sessions = chat sessions; IPs = distinct IPs from messages and sessions.
  - Testing = rows whose IP (message IP, else session IP) is NULL or a test IP. Users = Overview minus Testing, so the three always reconcile.
  - All time: these counts ignore the date range.
  - Cached on the server for `USAGE_SUMMARY_CACHE_TTL_SECONDS` (default 15 s); the browser loads them once when the shell mounts (no polling; reload to refresh).
- **Persistence**: choosing a tab writes localStorage `askdiq.audience_scope` and the URL (`?audience=users|testing`, removed for Overview) with `history.replaceState` (no new history entry). On load the URL wins; with no `audience` in the URL the stored value is used. Back/forward re-reads the URL.
- **Keyboard**: Left/Right arrows move between tabs and select.
- Changing the audience resets table pagination to page 1.

### Date range picker

`frontend/components/ui/DateRangePicker.tsx`, `frontend/lib/range-context.tsx`. Shown on pages that opt in: Dashboard, Visitors (list and detail), Conversation Logs, Documents and the date-based Governance tabs.

- Button shows the current label (e.g. **Last 7 days**, or `1 Oct 2026 – 14 Oct 2026` for a custom range).
- Presets: **Last 24 hours**, **Last 7 days** (default), **Last 30 days**, **Last 90 days**. A preset is a rolling window ending at the moment the page computed it (browser clock).
- **Custom range**: **From** and **To** date inputs, **Apply range**. The range is clamped: ends swapped if reversed, `to` capped at today, at most 365 days.
- Stored only in the URL (`range`, or `from`/`to`), never in localStorage, so a shared link carries it.
- **Timezone**: custom days are the viewer's local days. The Dashboard sends them to the API as UTC instants of local midnight to the next local midnight (`frontend/lib/dashboard-api.ts::rangeParams`), so a day means the viewer's calendar day. The entity pages (Visitors, Conversation Logs, Documents) send plain `YYYY-MM-DD` days (`frontend/components/admin/entity/shared.tsx::useRangeParams`), including for presets (so "Last 24 hours" becomes yesterday-to-today there). Not verified: how each entity endpoint interprets those day strings (UTC vs local).
- **Carried across pages**: entity links and in-app admin links add the current `range`/`from`/`to` to the target URL (`frontend/lib/entity-routes.ts::carryParams`), so a click-through keeps the same window. Audience needs no carrying (it is global).

### Command palette

`frontend/components/shell/CommandCenter.tsx`, `frontend/components/ui/CommandPalette.tsx`.

- Open/close: **Ctrl+K** / **Cmd+K** (works even while typing in a field), or the header button.
- Searches **pages only**: the enabled sidebar items the user is allowed to see. A query matches when every word appears in the label, the route (shown as the hint, e.g. `/admin/usage`) or the item's keywords. Example: `test ip` finds **Visitors**; `branding` finds **Settings** (superadmins only).
- Placeholder: **Jump to a page…**. No match: **No matching pages or records**.
- Record search (ids, emails, documents) is wired as a hook point for a future `/admin/search` endpoint but is not connected: the layout passes no search provider.
- Footer hint: **↑ ↓ to move · Enter to open · Esc to close** and **? for shortcuts**.

### Keyboard shortcuts

`frontend/lib/shortcuts.ts`, shown in the **Keyboard shortcuts** sheet (press `?`):

| Keys | Action | Group |
|---|---|---|
| Ctrl / ⌘ + K | Open the command palette | Global |
| ? | Show keyboard shortcuts | Global |
| ↑ ↓ | Move through palette results | Overlays |
| Enter | Open the selected result | Overlays |
| Esc | Close the open drawer, dialog or menu | Overlays |

Also implemented but not listed: Home/End in the palette; Arrow keys, Home, End and Tab in the profile menu; Left/Right on the audience tabs. The sheet notes **Single-key shortcuts are paused while you type in a field.** (`?` is ignored in text inputs, text areas, selects and editable regions.)

### Notification bell

`frontend/components/shell/NotificationBell.tsx`, feed from `frontend/lib/use-shell-notifications.ts`.

- Bell button (**Notifications**, or **Notifications (n new)**) with a red count (`9+` above 9).
- Panel title **Notifications**. Empty: **You're all caught up** / **Access requests and incidents will appear here.**
- Today the only item is for superadmins (`users:manage`): **`n` access request(s) waiting** / **Review them in User Management.**, linking to `/admin/user-management?tab=requests`.
- Data: `GET /admin/access-requests?status=pending&limit=1` (`total`), shared with the sidebar badge through one cached query: fresh for 30 s, re-polled every 60 s while the tab is visible, and revalidated on window focus.
- Incidents and feedback spikes are planned (TODO in code) but not wired.

### Profile menu

`frontend/components/shell/ProfileMenu.tsx`. The round avatar (initials) at the right of the header; button label **Account menu for `<name>`**.

- Name, email and role badge.
- **Theme** group: **Light**, **Dark**, **System** (radio items, a check marks the current one).
- **Sign out**.
- Keyboard: focus moves into the menu on open; Up/Down, Home/End move; Tab or Esc closes.
- There is no Help or documentation link in the menu today.

### Role badge

`frontend/components/shell/RoleBadge.tsx`: a small upper-case chip, **Superadmin** (brand colours), **Admin** (info blue), **Operator** (neutral). Shown in the sidebar footer, the profile menu and user lists. Users without a role get no badge.

### Theme

`frontend/components/ThemeToggle.tsx`, `frontend/lib/theme-context.tsx`, `frontend/lib/theme-script.ts`.

- Three modes: **Light**, **Dark**, **System** (follows the OS live). Default when nothing is stored: **System**.
- The sidebar toggle cycles Light -> Dark -> System -> Light; its label reads e.g. **Theme: Light. Switch to dark mode**. The icon shows the current mode (moon = light, sun = dark, monitor = system).
- The profile menu sets a mode directly.
- Stored per browser in localStorage `askdiq-theme`; other open tabs follow immediately. An inline script in `<head>` applies the stored theme before first paint (no flash); it carries the per-request CSP nonce.

### Sign out

See [login.md](login.md#sign-out). Entry points in the shell: the sidebar footer (expanded only) and the profile menu.

### Brand (white-label)

`frontend/lib/brand.ts`, `frontend/lib/brand-server.ts`, `frontend/lib/brand-context.tsx`, `frontend/components/brand/BrandLogo.tsx`.

- The root layout fetches public `GET /branding` on the server (2.5 s timeout, cached for 5 minutes). Any failure falls back to the defaults: product **DeployIQ**, assistant **AskDIQ**, company **Exigo Tech**, Exigo Tech logos.
- Shown in the shell: the full logo in the sidebar header and mobile drawer, the logo mark in the collapsed sidebar and the mobile top bar (light/dark artwork switch with the theme), the palette colours (CSS variables injected in `<head>`), favicon and page title metadata. Product, company and assistant names appear in page copy (login, Access Required, Chat).
- Saving in Settings -> Branding updates the open page immediately; other users see it within the 5-minute cache window. See [settings.md](settings.md).

### Toasts

`frontend/components/ui/ToastProvider.tsx`.

- Bottom-right stack, at most 4 visible (oldest dropped), tones success / error / info.
- Auto-dismiss after 6 s; the timer pauses while the pointer or keyboard focus is on the toast. Each toast has a dismiss button (**Dismiss notification**) and optionally **Undo**.
- Announced politely to screen readers.
- Shared conflict toast for edits that lost a race (HTTP 409): **Changed by someone else** / **This record was updated elsewhere. The latest version has been loaded; review it and try again.**

### Drawers and dialogs

`frontend/components/ui/DrawerShell.tsx`, `Modal.tsx`, `ConfirmDialog.tsx`.

- Every record inspector uses one drawer: slides in from the right over a dimmed backdrop, traps focus and returns it to the trigger, closes on Esc or backdrop click.
- Optional **Previous record** / **Next record** buttons (mouse only; no keyboard shortcut) and an **Open full view** link to the record's own page.
- Confirm dialogs default to **Confirm** / **Cancel** unless the page names the action.

### Query Inspector drawer (evaluation records)

`frontend/components/admin/QueryInspectorDrawer.tsx`, used by Evaluation (Live). Loads `GET /metrics/evaluation/record/{id}` (`dashboard:read`; id is the record UUID or its `request_id`).

- Title: **Evaluation Passed** or **Issue Detected**, `ID: <request id>` with **Copy request id**.
- Tabs: **Interaction & Audit**, **LLM Judge Breakdown**, **Sources & Citations (n)**, **Latency & Ops**. Footer **Continuous Evaluator v2.0** and **Close Inspector**.
- Values from the record: query, answer, faithfulness, relevance, completeness (1 decimal), citation precision (%), total latency (seconds), judge reason, unsupported claims, retrieved chunks. Missing numbers show `—`.
- **Caution**: several parts are static text, not data (see Known limitations): the **Security & Governance Guardrails** block always says Clean / Compliant / Verified, the **End-to-End Latency Waterfall** stage times are fixed numbers, a missing `passed` is shown as passed, and a missing judge reason shows a canned sentence.

Usage & Cost, Visitors detail and Conversation detail use a different drawer, **Single-Query Economic Inspector** (`frontend/components/admin/usage/QueryInspectorDrawer.tsx`, `GET /admin/usage/queries/{request_id}`), documented in [usage-cost.md](usage-cost.md).

### Tables

`frontend/components/admin/DataTable.tsx`, `TableToolbar.tsx`, `Pagination.tsx`, `CursorPagination.tsx`, `frontend/lib/use-table-state.ts`, `frontend/lib/use-table-preferences.ts`.

- **Sorting**: click a sortable header. First click sorts descending, the next toggles ascending/descending (`<column>_desc` / `<column>_asc`). Sorting is done by the API, not in the browser. Screen readers get `aria-sort`.
- **Search**: toolbar box (default placeholder **Search across columns...**), debounced 300 ms; **Clear search** button.
- **Filters**: one **Filters** button with a count, a **Filter Options** popover, removable filter pills (**Remove filter ...**), and **Reset** (**Reset all filters and search**) when anything is active.
- **Columns**: **Columns** menu (**Visible columns**) to hide/show columns.
- **Density**: **Compact** toggle (comfortable / compact rows).
- **Column visibility and density** are remembered per table in localStorage `askdiq.table.<tableId>`.
- **Page, page size, sort and search** live in the URL (`<prefix>_page`, `<prefix>_limit`, `<prefix>_sort`, `<prefix>_q`), so they survive reload, back/forward and sharing. Defaults (page 1, 10 rows, default sort, empty search) are left out of the URL. Changing search, filters or audience goes back to page 1.
- **Offset pagination**: **Showing a–b of n entries**, **Rows per page:** 10 / 25 / 50 / 100, **First page**, **Previous page**, **Next page**, **Last page**.
- **Cursor pagination** (logs, traces, events with no total): **Showing n entries** (or the page's item label) with **Newer** / **Older**.
- **States**: skeleton rows on first load; existing rows dim while a refresh runs; error **Failed to load table records** + message + **Try Again**; empty default **No matching records found** / **Try adjusting your search query, filters, or audience scope.** (pages may override).

### Deep links

`frontend/lib/use-deep-link.ts`, `frontend/lib/entity-links.ts`, `frontend/lib/entity-routes.ts`.

- Page state that matters (tabs, filters, selected record) lives in URL params, written with `router.replace` (no extra history entry, no scroll jump) unless a page asks for `push`. Several updates in one tick compose (e.g. **Reset** clearing several filters).
- Every entity id rendered in a table, chart or drawer links through one map (`entityHref`), with a copy button for the id:

| Entity | Link |
|---|---|
| Visitor | `/admin/usage?tab=sessions&ip=<ip>` |
| Session | `/admin/usage?tab=queries&session=<id>` |
| Conversation | `/admin/conversations/<id>` |
| Turn | `/admin/conversations/<id>?turn=<turn id>` |
| Trace | `/admin/governance?tab=Traces&request_id=<id>` |
| Feedback | `/admin/feedback?message_id=<id>` |
| Document | `/documents/<id>` |
| Chunk | `/documents/<id>?chunk=<chunk id>` |
| Analytics slice | `/admin/analytics?intent=<intent>&quality=<quality>` |

- Entity-page helpers: visitor detail `/admin/users/<client-ip or user id>`; visitors by heatmap cell `/admin/users?weekday=<0-6>&hour=<0-23>`; conversation list filters `/admin/conversations?ip=&min_turns=&max_turns=&outcome=&day=&preview=`.

### Shared caching and refresh

`frontend/lib/query-cache.ts`, `frontend/lib/use-visibility-polling.ts`.

- One in-memory cache per browser tab, shared by all pages. Identical requests in flight are merged into one.
- **Stale-while-revalidate**: cached data shows at once; it is refetched in the background once older than its stale time (default 30 s; pages set their own, e.g. Dashboard summary 30 s, activity feed 15 s).
- Leaving a page aborts its requests that nothing else is waiting for.
- **Window focus / tab becomes visible**: every observed, stale query is refetched.
- **Polling** (`useVisibilityPolling`): runs only while the tab is visible, pauses when hidden, and fires once immediately when the tab becomes visible again.
- **Prefetch**: hovering or focusing some cards/tabs for 120 ms warms the target page's first request; the next table page is prefetched when the browser is idle.
- **Mutations**: optimistic update with rollback on failure, optional **Undo**, and on 409 a refetch plus the **Changed by someone else** toast.
- Sign-out does a full page load, which empties the cache.
- There is no global "refresh" button; pages that have one document it. A browser reload always starts fresh.

### The API proxy

Every browser call goes to `/api/proxy/<path>` (`frontend/app/api/proxy/[...path]/route.ts`), which adds the service key, forwards the session cookie as `X-Session` (plus `X-Requested-With: askdiq` and the browser `Origin` on writes), caps request bodies at 15 MB, and passes back only safe headers (never `Set-Cookie`). A 401 deletes the session cookie. If the backend is unreachable it answers 502 **Backend service is unreachable at `<url>` (`<code>`). Please ensure the FastAPI backend server is running.**

## Common tasks

1. **Jump to a page without the mouse**
   1. Press Ctrl+K (Cmd+K on a Mac).
   2. Type part of a name or keyword, e.g. `conv` or `test ip`.
   3. Use ↑/↓ and press Enter.

2. **Compare real users with internal testing**
   1. On any dashboard page, click **Users** in the audience tabs and note the numbers.
   2. Click **Testing**. The URL now has `?audience=testing`, and the choice sticks across pages and reloads.
   3. If a colleague's office IP shows up under Users, an admin adds it in **Visitors -> Manage test IPs**; numbers move within about 30 seconds.

3. **Share exactly what you are looking at**
   1. Set the audience, date range, filters, sort and page.
   2. Copy the browser URL; it contains all of them. (Tip: if you rely on the audience, make sure `audience=` is in the URL; without it the recipient sees their own stored audience.)

4. **Look at a specific week**
   1. Open the date range button, enter **From** and **To** under **Custom range**, click **Apply range**.
   2. Click through to Visitors or Conversation Logs; the same `from`/`to` travel with the link.

5. **Reset a cluttered table**
   1. Click **Reset** in the table toolbar (clears search and filters, back to page 1).
   2. Column visibility and density are kept; change them from **Columns** and **Compact**.

6. **Act on pending access requests** (superadmin)
   1. Watch for the amber badge on **User Management** or the bell count.
   2. Click the bell item **n access requests waiting**; it opens User Management on the requests tab.

## Interactions and states

- **Hover**: collapsed sidebar items show their label as a tooltip; audience tabs show counts and the definition.
- **Reduced motion**: all shell animations (sidebar indicator, drawers, toasts, palette) are disabled when the OS asks for reduced motion.
- **Storage blocked** (private mode, strict settings): sidebar, theme, audience and table preferences still work for the session but are not remembered.
- **Backend down**: the shell renders (branding falls back to defaults); data areas show their error states with retry buttons; the audience badges simply do not appear.
- **Empty states**: palette **No matching pages or records**; bell **You're all caught up**; tables **No matching records found**.
- **Exports**: none in the shell; see each page.

## Light and dark theme

- Colours are CSS tokens; branded palette shades from Settings override the defaults in both themes.
- Logos have separate light and dark artwork and switch with the theme without a reload.
- Charts read their colours from the current theme (`frontend/lib/use-chart-theme.ts`); see each page for chart-specific notes.

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/auth/me` | any signed-in user | Name, email, role, permissions (sidebar, palette, gate) |
| POST | `/auth/logout` | any signed-in user | Sign out |
| GET | `/admin/usage/audience-counts` | `dashboard:read` | Audience tab badges and tooltips |
| GET | `/admin/access-requests?status=pending&limit=1` | `users:manage` | Sidebar badge and bell item |
| GET | `/branding` | public | Brand names, logos, palette (server-side) |
| GET | `/metrics/evaluation/record/{record_id}` | `dashboard:read` | Query Inspector drawer |

## Related pages

- [README.md](README.md), [login.md](login.md), [dashboard.md](dashboard.md), [chat.md](chat.md), [documents.md](documents.md), [visitors.md](visitors.md), [usage-cost.md](usage-cost.md), [conversation-logs.md](conversation-logs.md), [feedback.md](feedback.md), [evaluation.md](evaluation.md), [governance.md](governance.md), [analytics.md](analytics.md), [user-management.md](user-management.md), [settings.md](settings.md)

## Known limitations and in-flight changes

- **Query Inspector drawer shows static content**: the guardrails block is hard-coded (**PII Leakage: Clean**, **Secret Scanning: Clean**, **Prompt Injection: Compliant**, **Groundedness: Verified**) although the API returns `safety_passed` and `guardrail_flags`; the latency waterfall stages (110 / 180 / 140 / 1,380 / 35 ms, **LLM Generation (Azure OpenAI)**) are fixed values, only the total is real; `passed` defaults to true when missing; an empty judge reason shows a canned sentence about "ACSC and Zero Trust compliance context", and an empty claims list shows **Zero unsupported claims detected. Full 100% semantic grounding.**
- The command palette searches page names only; record search is not connected.
- Audience counts are all-time, ignore the date range, and load once per page load.
- The audience provider is mounted twice (app layout and admin layout); both fetch the counts, and users without dashboard access also request them (and get 403). Harmless but wasteful.
- With no `audience` in the URL, the stored audience applies, so a shared link without the param can show a different audience to the recipient.
- Entity pages send local `YYYY-MM-DD` days for the date range, while the Dashboard sends exact UTC instants; the two can disagree for non-UTC viewers near midnight and for the **Last 24 hours** preset.
- The collapsed sidebar has no sign-out button; the Chat page has no header utilities on desktop.
- The **Admin** header in the sidebar also heads the Chat-only list of users without a role.
- Drawer Previous/Next have no keyboard shortcuts.
- The bell only carries access requests; incidents and feedback spikes are TODO.
- Coming in branch `fix/shell-nav-logout-theme-dates`:
  - Flat sidebar: the **Admin** header and its expand/collapse (and `deployiq.sidebar.adminExpanded`) are removed; the collapsed sidebar shows the same list with icons.
  - Sign-out: a sign-out button in the collapsed sidebar too; spinner and **Signing out…** while it runs; bounded (6 s client, 4 s backend) through a new `/auth/logout` route that always clears the cookie, then `location.replace("/login")` (see [login.md](login.md)).
  - Two themes only: **Light** and **Dark**. **System** is removed from the toggle and the profile menu; a stored `system` (or no value) is converted once to the OS preference at that moment and saved as `light`/`dark`. The toggle simply flips light/dark.
  - Timezone-aware dates: a shared `RelativeTime` element shows "just now", "5m ago", "3h ago", "2d ago" (calendar date after 30 days) with the full local date-time and timezone (e.g. `16 Oct 2026, 02:30 pm AEDT`) in the tooltip, used on record lists across pages; server timestamps without a zone are read as UTC.
  - One active-visitors definition (DAU/WAU/MAU, active now) on the Dashboard and Visitors pages, and a shared `InfoTip` help icon; see [dashboard.md](dashboard.md) and [visitors.md](visitors.md).
- Coming in branch `perf/dashboard-load-speed`: the root layout loads branding and `/auth/me` in parallel on the server and passes the user to the app, so the first HTML already shows the sidebar, header and page skeletons (no full-screen spinner, no client `/auth/me` round trip before page data). `motion` joins the optimised package imports.
- Coming in branch `fix/chat-page-fetch-timeouts` (affects every page):
  - Proxy timeouts: if the backend sends no response headers within `BACKEND_TIMEOUT_MS` (reads, default 60 s) or `BACKEND_WRITE_TIMEOUT_MS` (writes, default 180 s), the proxy answers 504 **The backend did not respond within `<n>` seconds. Please try again.** Streams are not cut once headers arrive.
  - Retries: reads and sign-out are retried up to `BACKEND_RETRIES` times (default 2, jittered 250-1000 ms backoff) after a connection error or 502/503/504.
  - Query cache back-off: after a failed request, mounts, focus and stale checks wait 1 s, 2 s, 4 s ... up to 60 s before re-requesting that key; explicit **Try again** and polling still go through.
  - Branding is cached in the server process (5 minutes, stale served while one bounded refresh runs) instead of Next's data cache, so a hung backend no longer holds page renders open.
  - A last-resort `global-error.tsx` page (**Something went wrong** / **This page could not be loaded** / **Try again in a moment.**, buttons **Try again** and a link home).
- Coming in branch `feat/askdiq-chat-widget`: a floating AskDIQ launcher on every shell page except `/chat`, opening a right-side chat drawer, and a new shortcut **Ctrl / ⌘ + /** "Open or close the assistant chat drawer" (works in fields); see [chat.md](chat.md).
