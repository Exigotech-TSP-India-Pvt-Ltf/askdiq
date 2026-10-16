# User Management

> Superadmin console for accounts, access requests, roles and the sign-in audit trail.  Route: `/admin/user-management` · Nav: **User Management** · Permission: `users:manage` (superadmin only)

## Purpose and who uses it

User Management is where a superadmin decides who can open the dashboard and with which role. It covers:

- **Accounts**: everyone who has signed in with Microsoft SSO or been invited.
- **Access requests**: people who signed in without a role and asked for dashboard access.
- **Roles & permissions**: a read-only view of what each role can do.
- **Audit trail**: sign-ins, denied sign-ins, approvals, role changes, suspensions and session revocations.

Typical users: superadmins (approve and reject requests, change roles, suspend leavers) and, for read-only questions such as "who signed in yesterday", anyone with superadmin rights who is investigating an access issue. Admins and operators cannot open this page.

Roles (from `backend/app/auth/permissions.py`, mirrored in `frontend/lib/user-management-api.ts`):

| Role | Permissions |
|---|---|
| Operator | `dashboard:read` |
| Admin | Operator + `documents:write`, `feedback:triage`, `feedback:export`, `eval:run`, `testip:write`, `usage:export` |
| Superadmin | Admin + `users:manage`, `audit:auth:read`, `branding:write`, `taxonomy:write` |

User statuses: `pending` (signed in, no role yet; can use Chat only), `active`, `rejected` (request turned down; Chat only, can ask again), `suspended` (sees the `/suspended` page; every API call answers 403 `Account suspended.`).

## How to get there

- **Sidebar**: **User Management** (icon: user with cog). Only shown to users whose role grants `users:manage`. The item carries a count badge of open access requests (see [Sidebar pending badge](#sidebar-pending-badge-and-notification-bell)).
- **URL**: `/admin/user-management`.
- **Command palette** (Ctrl/Cmd+K): type "User Management", or the keywords `access requests` or `roles`.
- **Notification bell**: when requests are waiting, the bell shows **"N access request(s) waiting"** / **"Review them in User Management."** and links to `/admin/user-management?tab=requests`.

Deep-link query parameters (parsed in `frontend/components/admin/user-management/use-um-url.ts`; anything not allowed falls back to the default):

| Param | Allowed values | Default | Effect |
|---|---|---|---|
| `tab` | `users`, `requests`, `roles`, `audit` | `users` | Selected tab |
| `q` | free text, trimmed to 100 chars | empty | Users search (name or email) |
| `role` | `superadmin`, `admin`, `operator` | all | Users role filter |
| `status` | `pending`, `active`, `rejected`, `suspended` | all | Users status filter |
| `sort` | `created_at`, `last_login_at`, `email`, `name` | `created_at` | Users sort column |
| `order` | `asc`, `desc` | `desc` | Users sort direction |
| `page` | integer >= 1 | 1 | Users page |
| `user` | a user id (UUID) | none | Opens the user drawer |
| `view` | `pending`, `approved`, `rejected`, `all` | `pending` | Access Requests view |
| `action` | one auth event code (see [Audit event types](#audit-event-types)) | all | Audit Trail event filter |

Examples: `/admin/user-management?tab=users&status=suspended`, `/admin/user-management?tab=audit&action=login_success`, `/admin/user-management?tab=requests&view=rejected`.

Page size (Users, Access Requests and Audit Trail tables) is not in the URL; it resets to 25 on reload.

## Layout at a glance

```text
+----------------------------------------------------------------------------+
| [cog] User Management                                     [ N pending ]   |
| Accounts, access requests, roles and the sign-in audit trail               |
+----------------------------------------------------------------------------+
| Total users | Active | Pending | Suspended | Logins (24h)   <- KPI cards   |
|  (spark)    | (spark)| (spark) |  (spark)  |   (spark)                      |
+-------------------------------------+--------------------------------------+
| Logins per day       Last 14 days   | Access-request funnel                |
| [daily bars]                        | Signed in > Requested > Approved >   |
|                                     | Active                               |
+-------------------------------------+--------------------------------------+
| Role distribution (donut, Active)   | Time to approve       Last 30 days   |
|                                     | <1h | 1-4h | 4-24h | 1-3d | >3d      |
+-------------------------------------+--------------------------------------+
| [Users (N)] [Access Requests (N)] [Roles & Permissions] [Audit Trail]      |
+----------------------------------------------------------------------------+
| Users  (N)                                              [Invite user]      |
| [Search name or email...]  [Filters]                                       |
| Name | Email | Role | Status | Last login | Approved by | Created          |
| ...                                                       (row -> drawer)  |
| < 1 2 3 >                                         page size 10/25/50/100   |
+----------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/user-management-overview.png` — KPI strip, the four charts and the Users tab with one row's drawer open.

> Screenshot placeholder: `img/user-management-requests.png` — Access Requests queue with two rows selected and the bulk bar visible.

## Layout tour

### Header

- Title **User Management**, subtitle **Accounts, access requests, roles and the sign-in audit trail**.
- When there are open requests, a header badge reads **`N pending`** (N = `kpis.open_requests` from the stats endpoint).
- No audience tabs (`showAudienceTabs={false}`) and no date-range picker: the global Audience filter and date range do not apply anywhere on this page.

Source: `frontend/components/admin/user-management/UserManagementPage.tsx`.

### Access guard

The page checks `users:manage` on the client (`useCurrentUser().can`, which requires status `active` and the permission). Without it you see the empty state **Superadmins only** / **User Management needs the users:manage permission. Ask a superadmin if you need access.** If `/auth/me` itself fails you see **Could not check your access**. Every backend route repeats the check (`require_permission(Permission.USERS_MANAGE)` on the router in `backend/app/api/routes/user_management.py`).

### KPI cards

Five cards; each one is a link that drills into a filtered view. Values show `—` while loading.

| Card | Value | Sparkline (last 14 UTC days) | Click goes to |
|---|---|---|---|
| **Total users** | `kpis.total` | `access_requested` + `user_invited` events per day (hint **New requests and invites per day**) | Users tab, no filter |
| **Active** | `kpis.active` | `access_approved` + `user_reactivated` + `user_invited` per day (**Activations per day**) | Users, `status=active` |
| **Pending** | `kpis.pending` | `access_requested` per day (hint **`N open requests · new per day`**) | Users, `status=pending` |
| **Suspended** | `kpis.suspended` | `user_suspended` per day (**Suspensions per day**) | Users, `status=suspended` |
| **Logins (24h)** | `kpis.logins_24h` | sign-ins per day from the stats series (**Sign-ins per day**) | Audit Trail, `action=login_success` |

The **Pending** card is highlighted in the warning colour whenever `kpis.pending > 0`.

How it's computed:

- Endpoint: `GET /admin/users-accounts/stats?days=30` (`users:manage`).
- `total` = sum of user rows grouped by status (`UserRepository.count_by_status`); `active`, `pending`, `rejected`, `suspended` = the per-status counts. These are all-time counts of the `users` table, not windowed.
- `open_requests` = count of `access_requests` with status `pending` (`AccessRequestRepository.count_pending`). This can differ from **Pending** users: a pending user who never submitted the request form has no request row.
- `logins_24h` = count of `audit_log` rows with action `login_success` in the last rolling 24 hours (`AuthStatsRepository.count_events_since`). It counts sign-in events, not distinct people.
- The four account sparklines are computed **in the browser** from the newest 500 auth audit events (`dailyEventSeries` in `UserOverview.tsx`), bucketed by the UTC date of `created_at`. On a busy tenant the 500 newest events may not reach back 14 days (every sign-in is an event), so older days can show 0 even when something happened.
- Refresh: fetched on page load via the shared query cache (`lib/query-cache.ts`, stale after 30 s, revalidated on window focus). No polling. Any change made on this page (approve, reject, role change, suspend, invite, revoke) invalidates the stats, users, requests and audit caches so the cards update right away.

Source: `backend/app/auth/admin_service.py::UserAdminService.stats`, `backend/app/auth/user_repository.py`.

### Logins per day

- Title **Logins per day**, subtitle **Successful sign-ins; select a day to sort Users by last login**, window **Last 14 days**.
- Bars: last 14 entries of `logins_per_day`; tooltip row **Sign-ins**.
- How it's computed: `GET /admin/users-accounts/stats?days=30` returns one entry per day for the last 30 days (zero-filled). Each value = count of `audit_log` rows with action `login_success`, grouped by `CAST(created_at AS DATE)`. `audit_log.created_at` is naive UTC, so days are **UTC days**; the day labels are also formatted in UTC (`timeZone: "UTC"`).
- Click any bar: switches to the Users tab sorted by **Last login** (newest first) with role and status filters cleared. It does not filter to the selected day.
- **View as table** toggle shows columns **Day** / **Sign-ins**.

### Access-request funnel

- Title **Access-request funnel**, subtitle **Select a stage to filter Users**. Stages **Signed in**, **Requested**, **Approved**, **Active**.
- How it's computed (all-time, not limited to the 30-day window):
  - **Signed in** = users with a non-null `last_login_at` (`count_signed_in`).
  - **Requested** = distinct users with at least one access request of any status.
  - **Approved** = distinct users with at least one `approved` request.
  - **Active** = users with status `active` (same as the Active KPI). Invited users and bootstrap superadmins are active without a request, so **Active** can exceed **Approved**.
- Click a stage: **Signed in** clears the status filter; **Requested** filters Users to `pending`; **Approved** and **Active** both filter to `active`.

### Role distribution

- Title **Role distribution**, subtitle **Active users; select a role to filter Users**. Donut with centre label **Active**; empty copy **No active users yet**.
- How it's computed: users with status `active` and a non-null role, grouped by role (`active_role_mix`). Every role is always returned (0 when none).
- Click a slice: Users tab filtered to that role and `status=active`.
- This count also feeds the "last superadmin" guard in the user drawer.

### Time to approve

- Title **Time to approve**, subtitle **Approved requests by wait; select a bin to see decisions**, window **Last 30 days**. Bins **<1h**, **1-4h**, **4-24h**, **1-3d**, **>3d**; empty copy **No approvals in this period**.
- How it's computed: access requests with status `approved` and `decided_at` within the last 30 days; wait = `decided_at - created_at` in seconds, bucketed `< 3600`, `< 4 h`, `< 24 h`, `< 3 d`, else `>3d` (`AccessRequestRepository.approval_histogram`). Requests approved implicitly via a role/status change in the drawer also count, because the service marks the pending request approved.
- Click any bin: opens Access Requests with the **Approved** view. It does not filter to the bin.

Chart loading, error (with retry) and "View as table" behaviour come from the shared `ChartCard`.

### Tabs

**Users** (badge = total users), **Access Requests** (badge = open requests), **Roles & Permissions**, **Audit Trail**. Switching tab clears `user` and `page` from the URL.

### Users tab

- Card title **Users**, description **Everyone who has signed in or been invited**, total count badge, button **Invite user**.
- Toolbar: search box **Search name or email…** (300 ms debounce; case-insensitive literal substring match on email or full name, server side), and one **Filters** popover with **Role** (**All roles**, **Superadmin**, **Admin**, **Operator**) and **Status** (**All statuses**, **Pending**, **Active**, **Rejected**, **Suspended**).
- Columns: **Name** (with **(you)** on your own row), **Email**, **Role** (badge; **No role** when null), **Status** (badge), **Last login**, **Approved by**, **Created**. Sortable: Name, Email, Last login, Created. Nulls sort last.
- Pagination: 10 / 25 / 50 / 100 per page (default 25); the next page is prefetched.
- Empty state: **No users match these filters** / **Clear the search or filters, or invite someone.**
- Click a row or the name to open the user drawer.
- Endpoint: `GET /admin/users-accounts?search=&role=&status=&sort=&order=&limit=&offset=` (`limit` 1-200, `offset` 0-10000).
- Dates are formatted in the **browser's local time zone** (`Intl.DateTimeFormat`, medium date + short time). See the timezone note under [Known limitations](#known-limitations-and-in-flight-changes).

### User drawer

Opens on the right (`?user=<id>`), with Previous / Next arrows that step through the current page. Title is the user's name (or email), with the email underneath and a status badge.

Sections:

- **Profile**: Role, Status, Last login, Created, Approved by, Approved.
- **Actions**:
  - **Role** select + **Change role** button (enabled only when the selected role differs). Confirm dialog **Change role**: "`<email>` becomes `<Role>`. It takes effect on their next request."
  - **Suspend** (danger) for any non-suspended user, or **Reactivate** for a suspended one. Suspend asks you to type the user's email to confirm: "`<email>` loses access immediately on their next request." Reactivate: "`<email>` regains access with their current role."
  - **Revoke sessions**: type the email to confirm (**Revoke all sessions** / "`<email>` is signed out on every device.").
- **Live sessions**: up to 50 live sessions (IP, user agent, last seen, started, expires); empty copy **No active sessions**.
- **Recent activity**: up to 8 auth events where this user is the actor; empty copy **No recent activity**.
- **Audit entries**: up to 8 auth events whose `target_user_id` is this user; empty copy **No audit entries about this user**.

Recent activity and Audit entries are filtered in the browser from the same 500 newest auth events the Audit Trail tab uses, so older events will not appear here.

Endpoints: `GET /admin/users-accounts/{id}` (detail + sessions), `PATCH /admin/users-accounts/{id}` with `{version, role?, status?}`, `POST /admin/users-accounts/{id}/revoke-sessions`.

#### Guard rails

Blocked buttons stay focusable and show the reason as a tooltip. The API enforces the same rules and answers **409**, which the page shows as a toast **Change not applied** with the server's reason.

| Rule | UI reason text | Server detail (`admin_service.py`) |
|---|---|---|
| You cannot change your own role or status (role select disabled on your own row) | **You cannot change your own role or status.** | `You cannot change your own role or status.` |
| The last active superadmin cannot be demoted or suspended | **The last active superadmin cannot be demoted or suspended.** | same text |
| Reactivating requires a role | **Assign a role before reactivating this user.** | `Assign a role before activating this user.` |
| Someone else changed the user since you loaded it (`version` mismatch) | toast **Changed by someone else** / "The latest version has been loaded; review it and try again." | `This user was changed by someone else. Reload and try again.` |

The UI's "last superadmin" check uses the Role distribution count; the server re-checks under a row lock on all active superadmins, so two superadmins demoting each other at the same moment cannot both succeed.

Role/status changes are applied optimistically to the table and rolled back on failure. Success toasts: **Role set to `<Role>`**, **User suspended**, **User reactivated**, **Revoked N session(s)**.

What a change actually does:

- **Change role** updates the role only. On a `pending` or `rejected` user it does **not** grant access (status stays as it is); approve their request in Access Requests instead.
- **Suspend** sets status `suspended`. Existing sessions are not deleted, but every request re-reads the user row, so the next request is refused (403) and the browser lands on `/suspended`. A sign-in attempt while suspended is recorded as `login_denied` with reason `suspended`. Use **Revoke sessions** as well if you want the sessions gone from the list.
- **Reactivate** sets status `active` again with the existing role (event `user_reactivated`).
- If a user with a pending request is moved to `active` by any route other than suspension, the pending request is marked approved by you.

### Invite user dialog

- Title **Invite user**, help text **Creates an active account with this role. It links to their Microsoft identity on first sign-in.**
- Fields: **Email** (required; errors **Enter an email address.** / **Enter a valid email address.**), **Name (optional)** (max 256), **Role** (default Operator). Buttons **Cancel**, **Send invite**.
- `POST /admin/users-accounts/invite` creates an `active` user with that role, approved by you; the email is lower-cased; the name defaults to the part before `@`. No email is sent: tell the person to sign in with Microsoft SSO. A duplicate email gives 409 **A user with this email already exists.**
- Success toast **Invitation created** / "`<email>` can sign in with SSO as `<Role>`."
- Invited users never get an access request row, so they are not in the funnel's Requested / Approved stages.

### Access Requests tab

A view switch in the card header: **Queue** (pending), **Approved**, **Rejected**, **All decisions**.

**Queue** (card title **Access request queue**, description **Oldest first. Choose a role, then approve; reject with a note.**):

- Hint: **Focus a row, then press A to approve or X to reject.**
- Bulk bar: **`N selected`**, a role select (**Role for bulk approval**), **Approve selected** (disabled with 0 or more than 100 selected).
- Columns: select checkbox, **User** (name + email), **Requested** (role the user asked for; **No role** if none), **Justification** (two lines), **Waiting** (e.g. `45s`, `12m`, `3h 5m`, `2d 4h`, from server `waiting_seconds`), **Approve as** (role select; defaults to the requested role, else Operator), **Decision** (**Approve**, **Reject**).
- **Reject** opens **Reject access request**: "`<email>` sees this note on the Access Required page." Field **Note** is required (1-1000 characters, no control characters); buttons **Cancel**, **Reject**.
- Rows leave the queue immediately (optimistic) and come back if the call fails. Toasts: **Access approved** ("`<email>` is now `<Role>`."), **Access rejected**, **Approved N request(s)** ("All as `<Role>`.").
- Empty state: **No pending requests** / **New access requests appear here, oldest first.**

**Approved / Rejected / All decisions** (card title **Decision history**, description **Who decided what, and when.**): columns **User**, **Requested**, **Decision**, **Decided by**, **Decided**, **Note**; newest first. Empty state **No decisions yet**.

Server behaviour (`UserAdminService.approve / reject / bulk_approve`):

- A request can be decided once. The row is locked and must still be pending; otherwise 409 **This access request has already been decided.**
- Approving sets the user's role and status `active` and records who approved and when. Approving a suspended user is refused: 409 **This user is suspended. Reactivate them first.**
- Rejecting sets the user's status to `rejected` (only if they were `pending`). The user sees the note as **Reviewer note:** on the Access Required page and can submit a new request, which puts them back to `pending`.
- Bulk approve is all-or-nothing in one transaction (max 100 ids): if any one request is already decided or missing, nothing is approved.
- Endpoints: `GET /admin/access-requests?status=pending|approved|rejected|all&limit=&offset=` (pending oldest first, others newest first), `POST /admin/access-requests/{id}/approve` `{role}`, `POST /admin/access-requests/{id}/reject` `{note}`, `POST /admin/access-requests/bulk-approve` `{ids, role}`.

### Roles & Permissions tab

- Card **Roles & permissions**, description **Read-only. The API enforces exactly this table; the UI hides what a role cannot do.**
- Matrix: one row per permission (friendly label + code, e.g. **Manage users and access** / `users:manage`), columns **Superadmin**, **Admin**, **Operator**; your own role column is highlighted with **(you)**.
- The matrix is a frontend copy of `ROLE_PERMISSIONS` (there is no backend endpoint listing roles). The tab compares your column with your live permissions from `GET /auth/me` and, if they differ, shows a warning: "Your live permissions from `/auth/me` differ from this table for: … The API is the authority."
- Roles cannot be edited here; changing the permission table is a code change in `backend/app/auth/permissions.py` (and its frontend mirror).

### Audit Trail tab

- Card **Auth audit trail**, description **Who approved whom, role changes, suspensions and denied sign-ins. Newest first.**
- Toolbar: search **Search actor or target…** and a **Filters** popover with **Event** (**All events** plus each event label). The event filter is in the URL (`action=`); the search is not.
- Columns: **When**, **Event** (coloured badge), **Actor**, **Detail**, **IP**, **Request** (first 12 characters of the request id).
- Detail summary (`auditSummary` in `UserDrawer.tsx`): masked target email, then `from_role → to_role` or `from_status → to_status` or the role, `N revoked`, and the quoted note when present.
- Empty state: **No auth events match** / **Sign-ins, approvals and role changes appear here.**
- How it's fetched: `GET /governance/audit-log?category=auth&limit=500`. The route needs only `dashboard:read`, but auth events are filtered out unless the caller also has `audit:auth:read` (superadmin). The page loads the **500 newest auth events once**; search, event filter and pagination (10/25/50/100, default 25) all run in the browser over those 500 rows. Older events are not reachable from this page.
- Actor is the full email of whoever acted (the signing-in person for sign-in events, the superadmin for decisions; `anonymous` when unknown). Target emails in `detail` are masked as `a***@example.com`, so searching a full target email will not match; search by the actor email or by the domain.

#### Audit event types

From `backend/app/auth/audit.py::AuthEvent` (label in the UI from `AUTH_EVENT_LABEL`):

| Code | UI label | Written when | Badge tone |
|---|---|---|---|
| `login_success` | Signed in | SSO sign-in succeeded (detail: status, role, created, superadmin_bootstrap, session_id) | green |
| `login_denied` | Sign-in denied | Suspended user, identity conflict (email owned by another Entra identity), bad/replayed state, invalid token, IdP down (detail: `reason`) | red |
| `access_requested` | Access requested | User submitted the access request form (justification PII-redacted) | blue |
| `access_approved` | Access approved | Request approved, or a non-suspended user moved to active | green |
| `access_rejected` | Access rejected | Request rejected (note PII-redacted, max 500 chars) | red |
| `role_changed` | Role changed | Role changed in the drawer (`from_role`, `to_role`) | blue |
| `user_suspended` | Suspended | Status set to suspended | red |
| `user_reactivated` | Reactivated | Suspended user set back to active | green |
| `session_revoked` | Sessions revoked | **Revoke sessions** (`revoked` count) | red |
| `logout` | Signed out | User signed out | blue |
| `user_invited` | Invited | **Invite user** (role) | green |

Each event is written in the same transaction as the change it describes, so a failed change leaves no audit row.

### Sidebar pending badge and notification bell

- Source: `frontend/lib/use-shell-notifications.ts::usePendingAccessCount`, shown on the **User Management** nav item and in the header bell.
- Value = `total` from `GET /admin/access-requests?status=pending&limit=1`, i.e. the number of pending access requests (same as the page's `open_requests`).
- Fetched only for users with `users:manage` (everyone else gets 0 and no request). Refreshed every 60 s while the browser tab is visible (paused when hidden), cached 30 s, and revalidated on window focus. Badge text caps at **99+**; its accessible label is "`N` pending".
- The page's own decisions do not invalidate this key directly, so the badge can lag by up to a minute after you clear the queue.

## Common tasks

### Approve an access request

1. Open **User Management** (the sidebar badge or bell tells you requests are waiting) and select **Access Requests**, or go to `/admin/user-management?tab=requests`.
2. In **Queue**, read the **Justification** and **Waiting** time. Oldest requests are at the top.
3. In **Approve as**, pick the role. It defaults to what the person asked for; give the least privilege that works (Operator for read-only dashboards).
4. Click **Approve** (or focus the row and press **A**). The row disappears and the toast confirms "`<email>` is now `<Role>`."
5. The person gets access on their next request; they may need to refresh the Access Required page. The decision appears under **Approved** and as **Access approved** in the Audit Trail.
6. Several at once: tick the rows, choose the role in the bulk bar, click **Approve selected** (max 100, all-or-nothing).

### Reject an access request

1. In **Queue**, click **Reject** (or focus the row and press **X**).
2. Write a short, polite **Note**; the requester sees it on their Access Required page.
3. Click **Reject**. The user becomes **Rejected** and can still use Chat; they may request again later.

### Suspend a user (for example a leaver)

1. **Users** tab, search by name or email, click the row.
2. In the drawer, click **Suspend**, type the user's email exactly, and confirm.
3. Optional: click **Revoke sessions** and confirm with the email to clear their live sessions.
4. Check **Audit Trail** with **Event = Suspended** for the `user_suspended` row. To restore access later, open the same user and click **Reactivate**.

You cannot suspend yourself or the last active superadmin; promote another superadmin first.

### Change someone's role

1. **Users** tab, open the user, pick a new **Role**, click **Change role**, confirm.
2. The change applies on their next request (no sign-out needed). If you see **Changed by someone else**, the drawer has reloaded the latest values; review and retry.

### Audit who signed in

1. Quick view: the **Logins (24h)** card, or **Logins per day** for the last 14 UTC days.
2. Detail: click **Logins (24h)** (opens Audit Trail with `action=login_success`) or open **Audit Trail** and set **Event** to **Signed in**.
3. Type the person's email (or the domain) in **Search actor or target…**; for sign-in events the actor is the person who signed in.
4. Read **When**, **IP** and **Request**. For failures, switch the event to **Sign-in denied** and read the reason in **Detail** where present.
5. For a per-person view, open the user in **Users**; **Recent activity** lists their own sign-ins and actions, **Live sessions** shows where they are signed in now.

Only the newest 500 auth events are searchable here; for older history query `audit_log` directly or use the Governance audit log.

### Pre-provision a new colleague

1. **Users** > **Invite user**, enter **Email**, optional **Name**, pick **Role**, **Send invite**.
2. Tell them to sign in with Microsoft. Their first SSO sign-in links the invite to their identity; no request or approval step is needed.

## Interactions and states

- **Drill-downs**: every KPI card and chart mark writes filters to the URL and switches tab (see each section). Back/forward restores the view.
- **Keyboard**: Access Requests queue rows are focusable; **A** approves with the row's selected role, **X** opens the reject dialog (ignored while typing in a field). Ctrl/Cmd+K opens the command palette. Chart marks support roving focus and Enter to select.
- **Exports**: none on this page. Charts offer **View as table** only.
- **Loading**: skeleton cards while `/auth/me` resolves; skeleton rows in tables; `—` in KPI cards.
- **Errors**: tables show an error with retry; the drawer shows an error with retry; mutation failures show toasts (**Change failed**, **Approve failed**, **Reject failed**, **Bulk approve failed**, **Could not revoke sessions**, **Invite failed**). All 409s show **Change not applied** with the server's reason, or **Changed by someone else** for stale versions.
- **Empty states** (exact copy): **No users match these filters**, **No pending requests**, **No decisions yet**, **No auth events match**, **No active sessions**, **No recent activity**, **No audit entries about this user**, **No active users yet**, **No approvals in this period**.
- **Missing data**: if the funnel or charts look empty on a fresh install, nobody has signed in yet. The first superadmin is created by listing their email in the backend `SUPERADMIN_EMAILS` setting; on first sign-in a pending user with that email is promoted to active superadmin (`login_success` detail `superadmin_bootstrap: true`).

## Light and dark theme

Badges and charts use semantic tokens (`status-ok`, `status-warn`, `status-danger`, `status-info`, brand ramp) and the chart theme from `lib/use-chart-theme.ts`, so both themes are covered without page-specific colours. The highlighted **Pending** card uses the warning background in both themes.

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/auth/me` | signed in, any status (`current_user`) | Your role, permissions, id (guard, "(you)", roles drift check) |
| GET | `/admin/users-accounts/stats?days=30` | `users:manage` | KPI cards and the four charts |
| GET | `/admin/users-accounts` | `users:manage` | Users table (search, filters, sort, paging) |
| GET | `/admin/users-accounts/{id}` | `users:manage` | Drawer detail and live sessions |
| PATCH | `/admin/users-accounts/{id}` | `users:manage` | Change role, suspend, reactivate (`version` required) |
| POST | `/admin/users-accounts/{id}/revoke-sessions` | `users:manage` | Revoke sessions |
| POST | `/admin/users-accounts/invite` | `users:manage` | Invite user |
| GET | `/admin/access-requests` | `users:manage` | Queue and decision history; sidebar badge (`status=pending&limit=1`) |
| POST | `/admin/access-requests/{id}/approve` | `users:manage` | Approve with a role |
| POST | `/admin/access-requests/{id}/reject` | `users:manage` | Reject with a note |
| POST | `/admin/access-requests/bulk-approve` | `users:manage` | Approve selected (max 100) |
| GET | `/governance/audit-log?category=auth&limit=500` | `dashboard:read`; auth rows only with `audit:auth:read` | Audit Trail, sparklines, drawer activity lists |

All calls go through the Next proxy `/api/proxy/...`; POST/PATCH carry `X-Requested-With: askdiq` (CSRF marker).

## Related pages

- [README.md](README.md) — walkthrough index
- [shell.md](shell.md) — sidebar, command palette, notification bell
- [login.md](login.md) — SSO sign-in, Access Required and Suspended pages
- [governance.md](governance.md) — full audit log (non-auth events, branding and taxonomy changes)
- [settings.md](settings.md) — the other superadmin page
- [dashboard.md](dashboard.md), [chat.md](chat.md), [documents.md](documents.md), [visitors.md](visitors.md), [usage-cost.md](usage-cost.md), [conversation-logs.md](conversation-logs.md), [feedback.md](feedback.md), [evaluation.md](evaluation.md), [analytics.md](analytics.md)

## Known limitations and in-flight changes

- **500-event cap**: the Audit Trail tab, the KPI sparklines and the drawer's Recent activity / Audit entries all come from the 500 newest auth events, loaded once. On an active tenant this can cover only a few days.
- **Timezone**: `audit_log.created_at` and `users.created_at` are stored as naive UTC and serialised without an offset; `formatDateTime` passes them to `new Date()`, which treats an offset-less ISO string as local time. Those timestamps are therefore likely shown shifted by the viewer's UTC offset (Last login and Approved are timezone-aware columns and are not affected). Not verified in a running browser.
- Funnel stages are all-time while the histogram and login series are windowed (30 days); the page does not let you change `days` (API allows 1-90).
- Logins per day and Time to approve drill-downs do not filter to the selected day or bin.
- **Change role** on a pending or rejected user does not activate them.
- Suspension does not delete sessions; access is cut on the next request instead.
- The sidebar badge can lag up to 60 s after you decide requests.
- Roles & Permissions is a frontend mirror; drift is only detected for your own role.
- No CSV export of users or audit events.
- Coming in branch `fix/shell-nav-logout-theme-dates`: all dates on this page (Users **Last login** / **Created**, drawer Profile and sessions, Audit Trail **When**, request history) switch to a shared `RelativeTime` component ("3h ago" with the full local date-time in the tooltip; absolute in the drawer profile) that reads offset-less server timestamps as UTC, fixing the timezone issue above. The queue's **Waiting** cell also shows the absolute submission time, and **Decision history** gains a **Submitted** column. The sidebar becomes one flat list (no collapsible ADMIN header) and gets a sign-out button; the **User Management** item and its badge are unchanged.
- Coming in branch `perf/dashboard-load-speed`: the root layout preloads `/auth/me` on the server, so the permission gate can resolve on first paint instead of after a client round trip.
