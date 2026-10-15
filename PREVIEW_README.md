# AskDIQ combined preview (local only)

Branch `preview/all` in `C:\Projects\askdiq-wt\preview` combines every admin-console
branch (backend PR-01 to PR-13 chain + auth/user-management API, load balancing,
and UI PR-17 to PR-26: nav cleanup, auth gate/login, user management, governance
revamp, feedback workspace, settings/branding, analytics, interactive dashboard).
It is for local testing only. It is never pushed.

All commands below are for Windows PowerShell. `PREVIEW` means
`C:\Projects\askdiq-wt\preview`.

---

## 1. Copy your env files (you do this yourself)

```powershell
Copy-Item C:\Projects\askdiq\backend\.env        C:\Projects\askdiq-wt\preview\backend\.env
Copy-Item C:\Projects\askdiq\frontend\.env.local C:\Projects\askdiq-wt\preview\frontend\.env.local
```

Then add or check these variables (they may be new to your files; see
`backend\.env.example` and `frontend\.env.local.example` in the preview).

**backend\.env**

| Variable | Preview value |
| --- | --- |
| `ENV` | `development` (the mock IdP refuses to start otherwise) |
| `AUTH_DEV_MOCK` | `true` |
| `SESSION_SECRET` | any long random string (32+ characters) |
| `SUPERADMIN_EMAILS` | the email you will type in the mock login, e.g. `you@exigotech.com.au` |
| `ACCESS_CONTACT_EMAIL` | an admin email shown on Access Required pages (may be empty) |
| `APP_BASE_URL` | `http://localhost:3000` |
| `SESSION_COOKIE_NAME` | `askdiq_session` |
| `SESSION_COOKIE_SECURE` | `false` |
| `API_KEY_NAME` | your existing service key (the frontend `API_KEY` must equal it) |

`ENTRA_TENANT_ID` / `ENTRA_CLIENT_ID` / `ENTRA_CLIENT_SECRET` may stay unset with
`AUTH_DEV_MOCK=true` (placeholder ids are used). `CORS_ALLOWED_ORIGINS` should
include `http://localhost:3000`.

**frontend\.env.local**

| Variable | Preview value |
| --- | --- |
| `API_BASE_URL` | `http://localhost:8000` |
| `API_KEY` | same value as backend `API_KEY_NAME` |
| `APP_BASE_URL` | `http://localhost:3000` |
| `SESSION_COOKIE_NAME` | `askdiq_session` (same as backend) |
| `SESSION_COOKIE_SECURE` | `false` |
| `ACCESS_CONTACT_EMAIL` | same as backend (may be empty) |
| `AUTH_DEV_MOCK` | `true` |

## 2. Database migrations (WARNING)

The preview adds new tables and columns (auth users/sessions, access requests,
branding, insights taxonomy, feedback triage, jobs, governance indexes). Running
the migrations **alters whatever database `DATABASE_URL` points at**, and your
normal branch will not know about those changes.

Recommended: point `DATABASE_URL` in the preview's `backend\.env` at a copy or a
new database, for example create `ragdb_preview` (with the `vector` extension)
or restore a dump of your dev DB into it, then:

```powershell
cd C:\Projects\askdiq-wt\preview\backend
C:\Projects\askdiq\backend\venv\Scripts\python.exe -m pip install -r requirements.txt
C:\Projects\askdiq\backend\venv\Scripts\python.exe -m alembic upgrade head
```

The migration chain has a single head (`72125f44984d`).

## 3. Start the apps

Terminal 1 (backend):

```powershell
cd C:\Projects\askdiq-wt\preview\backend
C:\Projects\askdiq\backend\venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

Terminal 2 (frontend):

```powershell
cd C:\Projects\askdiq-wt\preview\frontend
npm ci --legacy-peer-deps   # first time only
npm run dev                 # http://localhost:3000
```

Stop anything else on ports 8000 / 3000 first (e.g. your main checkout's servers).

## 4. Sign in

1. Open http://localhost:3000; you land on `/login`.
2. Click **Sign in with Microsoft**. With `AUTH_DEV_MOCK=true` this opens the
   local mock IdP instead of Microsoft.
3. Enter (or pick) the email listed in `SUPERADMIN_EMAILS` and continue. You
   return signed in as **Superadmin**, so every nav item is visible.
   Any other email signs in as a new, pending user (you see Access Required),
   which you can then approve from User Management.

## 5. What to check

| Area | Where | What should be true |
| --- | --- | --- |
| Dashboard | `/admin` | Interactive dashboard: KPI trend cards, charts that drill through to the matching page, activity feed, health pill |
| Usage & Cost | `/admin/usage` | The **Overview** tab is the former Live Telemetry view (with cost insights above it); the old Overview is gone; old `?tab=live_telemetry` links land on Overview |
| Sidebar | every page | Dashboard, Chat, Documents, Visitors, Usage & Cost, Conversation Logs, Feedback, Evaluation (Live), Governance, Analytics, User Management, Settings; **no** old "Evaluation" item; `/admin/evaluation` and `/evaluation` redirect to `/admin/evaluation-live`; items are filtered by role |
| User Management | `/admin/user-management` | Users, Access Requests (pending badge in the sidebar), Roles & Permissions matrix, Audit Trail; invite, approve/reject, change role, suspend, revoke sessions |
| Governance | `/admin/governance` | Overall / Monitoring / Traces / Logs tabs (tab kept in `?tab=`). Logs: summary column, expandable JSON detail, server paging (Next/Previous), level/service/search/date filters, CSV export. Traces: filters, server paging, trace drawer with stage waterfall. Live updates via the stream (health banner, incidents) |
| Feedback | `/admin/feedback` | Overview charts (click to filter), Review Queue triage (keyboard j/k/r/d/p, bulk actions, undo), Golden Cases, Preference Data, Knowledge Gaps |
| Analytics | `/admin/analytics` | Guide, Overview, Trend, Flow, Topics, Drop-off, Conversations tabs |
| Settings | `/admin/settings` | Branding editor (colours, names, logos, live light/dark preview) and Insights Taxonomy editor; superadmin only |
| Login | `/login` (sign out first) | Split-screen branded design, transparent logo with tagline, Microsoft button |
| Theme | every page | Light / Dark / **System** toggle (sidebar footer or profile menu); every page, chart and drawer follows it without a reload |

Sign out from the sidebar footer. To test lower roles, sign in with another
email and approve it as `admin` or `operator` from User Management.

## Notes

- Nothing from this folder is pushed; `preview/all` is a local branch only.
- The temporary permission shims (`lib/use-can.tsx`, User Management's
  `use-current-user.ts`) delegate to the real `usePermission` from
  `lib/auth-context.tsx` here; that consolidation exists only in the preview.
