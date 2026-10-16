# Sign-in, Access Required and Suspended

> How people get into the console: Microsoft single sign-on, the session cookie, the Access Required flow for users without a role, the suspended page and sign-out.  Route: `/login` (also `/suspended`, `/`) · Nav: none (public pages) · Permission: none (public); Access Required needs a signed-in, non-suspended user

## Purpose and who uses it

- **Everyone** signs in here. There are no passwords: the only sign-in method is **Sign in with Microsoft** (Entra ID, OpenID Connect authorization code flow with PKCE).
- **New users** land in Chat and see **Access required** on dashboard pages until a superadmin approves them.
- **Superadmins** approve or reject the requests that this page creates (in [User Management](user-management.md)).
- **Support** uses the error codes below to work out why someone could not sign in.

User statuses (backend `backend/app/auth/permissions.py`): `pending`, `active`, `rejected`, `suspended`. Roles: `operator` < `admin` < `superadmin`.

| Status / role | Gets a session? | Chat | Dashboard pages |
|---|---|---|---|
| `active` with a role | yes | yes | yes, filtered by role permissions |
| `active` without a role | yes | yes | **Access required** |
| `pending` (new user, or request re-opened) | yes | yes | **Access required** |
| `rejected` | yes | yes | **Access required** (can ask again) |
| `suspended` | no (sign-in refused) | no | no: **Your account is suspended** |

Source: `frontend/lib/auth-gate.ts`, `backend/app/auth/service.py`, `backend/app/auth/deps.py`.

## How to get there

| URL | What happens |
|---|---|
| `/` | Spinner, then: signed out -> `/login`; active with a role -> `/admin`; anyone else signed in -> `/chat` (`frontend/app/page.tsx`). |
| `/login` | The sign-in page. Query params: `error=<code>` (shows an allow-listed message, see below) and `next=<path>` (where to go after sign-in, same-origin only). |
| `/login?next=/admin/feedback` | After sign-in, active users go to `/admin/feedback`. |
| Any app page without the session cookie | `frontend/middleware.ts` redirects to `/login?next=<path+query>` (no `next` for `/`). |
| `/suspended` | Static "account suspended" page shown when sign-in is refused for a suspended account. |
| `/auth/login` | Server route behind the button; starts SSO (not a page). |
| `/auth/callback`, `/auth/callback/<provider>`, `/api/auth/callback/<provider>` | Server routes Microsoft redirects back to (not pages). |
| `/mock-idp/...` | Development-only fake Microsoft sign-in (see below). |

Rules for `next` (`frontend/lib/safe-redirect.ts::safeNextPath`): must start with `/` (not `//`), at most 2048 characters, no control characters or backslashes, must stay on this origin, and must not point at `/login`, `/auth/...`, `/api/...`, `/mock-idp` or `/suspended`. Anything else is silently dropped.

The command palette is not available on these pages (they are outside the app shell).

## Layout at a glance

```text
Desktop (md and wider)
+--------------------------------------+-----------------------------------+
| [LOGO, light-on-teal]                |                                   |
|                                      |  Welcome to <Product>             |
|  EYEBROW (assistant name)            |  Sign in with your <Company>      |
|  Governed answers for security       |  Microsoft account to open ...    |
|  and compliance teams.               |                                   |
|  <Product> answers from your own     |  [!] error message (if ?error=)   |
|  knowledge base, ...                 |                                   |
|        (animated hexagons)           |  [ (MS logo) Sign in with Microsoft ]
|                                      |  ------- SINGLE SIGN-ON -------   |
|                                      |  Access is provisioned by your    |
|                                      |  <Company> administrator. ...     |
| (c) <year> <Company>                 |  Privacy Policy  Terms  Help      |
+--------------------------------------+-----------------------------------+
Mobile: a teal header strip with the logo replaces the left panel.
```

> Screenshot placeholder: `img/login-default.png` — the split-screen sign-in page with the default DeployIQ brand, no error.

> Screenshot placeholder: `img/login-error.png` — the sign-in card showing the red "Your sign-in session didn't match. Please start again." alert.

## Layout tour

### Brand panel (left, desktop only)

- Logo (white artwork for the teal panel), then three lines of brand copy, then `© <year> <companyName>`.
- All copy comes from branding (Settings -> Branding, see [settings.md](settings.md)). Defaults when a field is not set (`frontend/components/auth/LoginView.tsx::loginCopy`):
  - Eyebrow: the assistant name in capitals (default **ASKDIQ**).
  - Headline: **Governed answers for security and compliance teams.**
  - Subtext: **`<productName>` answers from your own knowledge base, with every response traceable, reviewed and governed.**
- The hexagons follow the pointer; with "reduce motion" turned on in the OS nothing animates.

### Sign-in card (right)

- Heading: **Welcome to `<productName>`** (default product name **DeployIQ**).
- Description (default): **Sign in with your `<companyName>` Microsoft account to open the `<productName>` console.**
- Error alert (only with `?error=`): red box with an icon, one fixed message per code (table below). The raw query value is never displayed.
- Button: **Sign in with Microsoft**. After a click it shows a spinner and **Redirecting to Microsoft…** and is disabled (no double submits). Coming back with the browser Back button re-enables it.
- Divider: **Single sign-on**.
- Help text: **Access is provisioned by your `<companyName>` administrator. If you can't sign in, contact `<email>`.** The email is `ACCESS_CONTACT_EMAIL` from the frontend server environment (validated; a mailto link). When it is not set the text ends with **your administrator**.
- Footer links: **Privacy Policy**, **Terms of Service**, **Help**. Each appears only when the matching branding URL is set (`privacy_url`, `terms_url`, `help_url`); https links open in a new tab.

### Sign-in error messages (every code)

Source: `frontend/lib/login-errors.ts`, emitted by `frontend/app/auth/login/route.ts` and `frontend/app/auth/callback/route.ts`.

| `?error=` | Message shown | When it happens |
|---|---|---|
| `state_mismatch` | Your sign-in session didn't match. Please start again. | Microsoft sent back a `state` that does not equal the `askdiq_oidc_state` cookie, or the cookie is missing (it lives 10 minutes; sign-in started in another browser or tab; cookies blocked). |
| `expired` | Your sign-in link expired or was already used. Please start again. | The callback had no `code`/`state` (or a code over 4096 chars), or the backend answered 400: unknown, expired (10 min TTL) or replayed `state`. |
| `signin_failed` | We couldn't verify your Microsoft sign-in. Please try again. | Backend answered 401 (ID token failed validation: signature, issuer, audience, tenant, nonce, expiry, `ver` 2.0), or its success response could not be parsed. Note: a wrong frontend `API_KEY` also gives 401 here, so this message can mean a configuration problem. |
| `identity_conflict` | This email is linked to a different Microsoft account. Contact your administrator. | Backend answered 403 with anything other than "Account suspended.": the email already belongs to a user row linked to a different Entra object id (`oid`). |
| `rate_limited` | Too many sign-in attempts. Please wait a minute and try again. | Backend answered 429: more than `AUTH_LOGIN_RATE_LIMIT_PER_MINUTE` (default 10) sign-in calls from one client IP in a 60 s window. Both steps (start and callback) count, so this is about 5 full sign-ins per minute per IP. |
| `idp_unavailable` | Microsoft sign-in is unavailable right now. Please try again shortly. | Backend answered 502 (discovery / token exchange with Microsoft failed) or any other unexpected status, or returned an authorize URL without a valid `state`. |
| `idp_denied` | Sign-in was cancelled or denied at Microsoft. | Microsoft redirected back with an `error` parameter (user cancelled, consent refused, account not allowed in the tenant). |
| `backend_unavailable` | The service is unavailable right now. Please try again shortly. | The frontend server could not reach the backend at all (network error). |
| `session_expired` | Your session has ended. Please sign in again. | Defined but **not emitted anywhere** in the current code. |
| any other value | Sign-in didn't complete. Please try again. | Unknown or tampered code. |

Every backend sign-in outcome is written to the auth audit log as `login_success` or `login_denied` with a reason (`invalid_state`, `invalid_token: ...`, `idp_error: ...`, `identity_conflict`, `suspended`). Superadmins can read it (permission `audit:auth:read`, see [user-management.md](user-management.md)).

### The SSO flow, step by step

1. **Button click** -> browser goes to `/auth/login?next=<validated path>`.
2. **`/auth/login`** (Next server) calls backend `GET /auth/sso/login` with the service key. The backend creates single-use `state`, `nonce` and a PKCE verifier (stored server-side for 10 minutes) and returns Microsoft's authorize URL (scopes `openid profile email`). The route sets two httpOnly cookies for 10 minutes, `askdiq_oidc_state` (login-CSRF binding) and `askdiq_login_next` (the validated `next`, or empty), then redirects (303) to Microsoft.
3. **Microsoft** signs the user in and redirects to `/auth/callback?code=...&state=...`.
4. **`/auth/callback`** checks `state` against the cookie (constant-time compare), then POSTs `{code, state}` to backend `POST /auth/sso/callback`, sending any previous session cookie as `X-Session` so it is rotated (revoked).
5. **Backend** exchanges the code, validates the ID token (RS256, issuer, audience, tenant = `ENTRA_TENANT_ID`, nonce, expiry), then upserts the user on the Entra `oid`:
   - Known `oid`: name, tenant and (if free) email are refreshed.
   - Unknown `oid` but an invited row with the same email and no `oid`: the invite is linked.
   - Same email already linked to another `oid`: refused (`identity_conflict`).
   - Otherwise a new user is created as `pending` with no role.
   - An email listed in `SUPERADMIN_EMAILS` becomes an active superadmin on its first sign-in (only while still `pending`; it never overrides a later decision such as suspension).
   - `suspended`: refused with 403 and no session.
6. **Session issued**: the route stores the session id in the httpOnly cookie (`SESSION_COOKIE_NAME`, default `askdiq_session`; `SameSite=Lax`; `Secure` when `SESSION_COOKIE_SECURE=true`; expires at the session's absolute expiry), clears both sign-in cookies and redirects:
   - `active` users: to `next`, or `/` (which forwards to `/admin` with a role, `/chat` without).
   - `pending` / `rejected` users: to `next` only if it is a Chat path, otherwise `/chat`.
   - `suspended` (403 "Account suspended."): to `/suspended`, and any old session cookie is deleted.

The session id never appears in a URL, a response body seen by the browser, or client JavaScript. The proxy refuses to forward `auth/sso/callback` (404), so only the callback route can call it.

Alternate callback paths: `/auth/callback/<provider>` (e.g. `/auth/callback/microsoft-entra-id`) and `/api/auth/callback/<provider>` (NextAuth-style, so an existing Entra app registration can be reused) run exactly the same code. The backend sends `ENTRA_REDIRECT_URI` to Microsoft (default `APP_BASE_URL/auth/callback`), so that exact URL must be registered in Azure.

Source: `frontend/app/auth/login/route.ts`, `frontend/app/auth/callback/route.ts`, `frontend/lib/server/auth-cookies.ts`, `backend/app/api/routes/auth.py`, `backend/app/auth/oidc.py`, `backend/app/auth/service.py`.

### Session lifetime

Backend settings (`backend/app/core/config.py`, `backend/app/auth/sessions.py`):

- Absolute lifetime `SESSION_TTL_MINUTES` (default 480 = 8 hours). The cookie expires at the same moment.
- Idle timeout `SESSION_IDLE_MINUTES` (default 60). Each request extends it, but `last_seen_at` is written at most every `SESSION_TOUCH_INTERVAL_SECONDS` (default 60).
- Only an HMAC of the session id is stored in the database.
- The user row is re-read on every API request, so a role change or suspension applies to the very next call.
- A 401 from any proxied call deletes the session cookie (`frontend/app/api/proxy/[...path]/route.ts`).

### Route gate inside the app

Every page under the app shell runs `useRouteGate` (`frontend/components/auth/RouteGate.tsx`) on top of `GET /auth/me`:

- Loading: full-screen spinner.
- No session (401): spinner, then `/login?next=<current path+query>`.
- `/auth/me` failed for another reason (backend down): full-screen error **We couldn't check your session** / **The service may be unavailable. Try again in a moment.** with a **Try again** button.
- Suspended: the suspended view (with **Sign out**) replaces the whole shell.
- No dashboard access on a dashboard route: the shell stays, the page area shows **Access required**.
- Otherwise the page renders.

The gate is UX only; the backend authorises every call itself.

### Access Required page

Shown in place of any dashboard route for signed-in users without dashboard access (`frontend/components/auth/AccessRequiredView.tsx`). Chat (`/chat`) always works for them, and their sidebar contains only **Chat**.

- Header: **Access required** / **Dashboard access needs approval** (the standard page header with palette, bell and profile menu, no audience tabs).
- Card title: **You don't have dashboard access yet** (switches to **Access approved** at the moment of approval).
- Text: **Signed in as `<email>`. `<productName>` dashboards open once a `<companyName>` administrator approves your access. Chat is available now.**
- Stepper (three steps):

| Situation | Step 1 | Step 2 | Step 3 |
|---|---|---|---|
| No request yet | **Requested** (current) | **Under review** | **Approved** |
| Request pending | **Requested** (done) | **Under review** (current) | **Approved** |
| Request rejected | **Requested** (done) | **Not approved** (failed, red X) | **Approved** |
| Approved | all three done | | |

- When rejected with a note: **Reviewer note: `<note>`**.
- Request form (shown when there is no request yet, or the last one was rejected):
  - **Role** select: **Let the administrator decide** (default), **Operator**, **Admin**. (Superadmin cannot be requested from the UI.)
  - **Why do you need access? (optional)** text area with a `n/500` counter (500 characters max; the backend also rejects some unsafe characters, which shows the generic failure message).
  - Button **Request access**, or **Request access again** after a rejection.
  - Success toast: **Access requested** / **An administrator will review it.**
  - A request already open (409): toast **A request is already open**, and the page shows the pending state.
  - Any other failure: inline **Your request couldn't be sent. Please try again.**
- Pending state text: **Your request for the `<Role>` role is waiting for review. This page updates on its own when it is approved.** (without "for the ... role" if no role was requested).
- Footer: **Questions? Contact `<email>`** (from `/auth/me`, else `/auth/access-request`, else **your administrator**) and an **Open Chat** button.
- Live update: while this view is open and the tab is visible, `/auth/me` is re-read every 15 s (`ACCESS_POLL_INTERVAL_MS`); the request status is re-read too while still not approved. On approval the dashboard replaces the view in place (no re-login) and a toast says **Access approved** / **Welcome to the dashboard.**

Backend rules (`backend/app/auth/service.py::request_access`): one open request per user (409 **An access request is already pending.**); an active user with a role gets 409 **You already have dashboard access.**; asking again after a rejection sets the user back to `pending`. Each request is audited as `access_requested` (the justification is masked in the audit log).

### Suspended page

`frontend/components/auth/SuspendedView.tsx`, used in two places:

- **`/suspended`** (sign-in was refused; no session): logo, red shield icon, **Your account is suspended**, **You can't use `<productName>` while your account is suspended. To restore access, contact `<email>`.** (fallback **your `<companyName>` administrator**), and a **Back to sign in** button.
- **Inside the app** (the account was suspended while a session exists and `/auth/me` reports `suspended`): same text with a **Sign out** button instead.

Suspended accounts get 403 **Account suspended.** from every API route except `/auth/me` and `/auth/logout`.

### Sign-out

Available from the sidebar footer (log-out icon, **Sign out**; expanded sidebar only), the profile menu (**Sign out**) and the suspended view. Current behaviour (`frontend/lib/auth-context.tsx::signOut`):

1. `POST /api/proxy/auth/logout` -> backend `POST /auth/logout` revokes the server session (audited as `logout`) and the proxy deletes the cookie.
2. Whatever the result, the browser does a full navigation to `/login` (so the in-memory cache is dropped).

There is no client timeout in the current code: if the backend hangs, the click waits until the proxy request fails.

### Not-found and error pages

- **404** (`frontend/app/not-found.tsx`): branded card, **Error 404**, **Page not found**, **The page you are looking for does not exist or has moved.**, buttons **Go to the dashboard** (`/admin`) and **Open chat** (`/chat`).
- **Route error** (`frontend/app/error.tsx`): **Something went wrong**, **This page could not be loaded**, **An unexpected error occurred. Try again, or go back to the dashboard.**, and when Next provides one, **Reference `<digest>`** with a copy button (**Copy error reference**). Buttons **Try again** (re-renders the route) and **Go to the dashboard**. No stack trace is shown; the full error goes to the browser console.

### Development mock identity provider

- `/mock-idp/...` (`frontend/app/mock-idp/[...path]/route.ts`) forwards to the backend's mock Entra provider. It answers 404 unless the frontend server has `AUTH_DEV_MOCK=true`, and the backend refuses to start with `AUTH_DEV_MOCK=true` unless `ENV=development`.
- With the mock on, **Sign in with Microsoft** opens a plain page **Mock Entra ID (development only)** with fields **Email**, **Name**, **Object id (oid)**, a checkbox **Include the email claim** and a **Sign in** button. The rest of the flow (state, PKCE, token validation, cookies) is identical to production.

## Common tasks

1. **Sign in for the first time**
   1. Open the console URL; you are sent to `/login`.
   2. Click **Sign in with Microsoft** and complete the Microsoft prompt.
   3. You land in Chat. Open any dashboard link (or go to `/admin`) to see **Access required**.
   4. Pick a role (or leave **Let the administrator decide**), add a reason, click **Request access**.
   5. Leave the tab open: when a superadmin approves, the dashboard appears by itself within about 15 seconds.

2. **Ask again after a rejection**
   1. Open any dashboard URL. The stepper shows **Not approved** and any **Reviewer note**.
   2. Adjust the role or reason and click **Request access again**. Your status returns to `pending`.

3. **Help a user who sees an error on the sign-in page** (support)
   1. Ask for the full URL; read the `error=` value and look it up in the table above.
   2. `state_mismatch` / `expired`: ask them to start again from `/login` in one tab, within 10 minutes, with cookies allowed.
   3. `identity_conflict`: their email is linked to a different Microsoft identity; a superadmin must fix the user record.
   4. `rate_limited`: wait a minute; if many people share one office IP, check `TRUST_PROXY_HEADERS` and the rate limit setting.
   5. `signin_failed` for everyone at once: check the frontend `API_KEY` matches the backend `API_KEY_NAME`, and the Entra settings.
   6. Confirm in the auth audit log (`login_denied` with a reason).

4. **Deep-link a colleague to a page they must sign in for**
   1. Send the normal page URL, e.g. `https://<host>/admin/feedback?tab=...`.
   2. Signed out, they are sent to `/login?next=/admin/feedback?tab=...` and return there after sign-in (if they have dashboard access; otherwise they land in Chat).

5. **Bootstrap the first superadmin** (deployment)
   1. Put the email in backend `SUPERADMIN_EMAILS` (comma-separated).
   2. That person signs in once; they become an active superadmin and can approve everyone else.

## Interactions and states

- **Keyboard**: the sign-in button and links are focusable with visible focus rings; no shortcuts on these pages.
- **Loading**: `/` and the app shell show a centred spinner while `/auth/me` loads.
- **Backend down**: on `/login` nothing fails until the button is clicked (`backend_unavailable`); inside the app, the **We couldn't check your session** screen with **Try again**.
- **Approval while on Chat**: the Access Required poll only runs while that view is shown. A pending user who stays on `/chat` keeps the Chat-only sidebar until they open a dashboard route or reload.
- **Suspension while signed in**: API calls start failing with 403 at once, but the suspended view appears only when `/auth/me` is re-read (page reload, or the Access Required poll).
- **Session expiry mid-use**: API calls return 401 and the proxy deletes the cookie; the next page navigation goes to `/login?next=...`. Not verified: whether an open page redirects by itself without a navigation (the generic API client does not redirect on 401).
- **Exports**: none on these pages.

## Light and dark theme

The login brand panel always uses the light-on-teal logo artwork (`tone="onDark"`); the card follows the stored theme. The theme is applied before first paint by an inline script, so there is no flash.

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/auth/sso/login` | service key only (no session); rate-limited | Start SSO, get the authorize URL (called by `/auth/login`) |
| POST | `/auth/sso/callback` | service key only; rate-limited; never reachable through `/api/proxy` | Exchange the code, issue a session (called by `/auth/callback`) |
| GET | `/auth/me` | any signed-in user, any status (`current_user`) | Identity, role, status, permissions, contact email |
| POST | `/auth/logout` | any signed-in user (`current_user`) | Revoke the session |
| GET | `/auth/access-request` | signed in, not suspended (`require_role_or_chat`) | Own status and latest request |
| POST | `/auth/access-request` | signed in, not suspended (`require_role_or_chat`) | Create an access request |
| GET | `/branding` | public | Brand copy, logos and footer links (server-side, cached) |

All browser calls go through `/api/proxy/...`, which adds the service key and forwards the session cookie as `X-Session`; state-changing calls also carry `X-Requested-With: askdiq` and the browser `Origin`, checked by the backend.

## Related pages

- [README.md](README.md) - overview of all walkthroughs
- [shell.md](shell.md) - sidebar, profile menu, sign-out button, theme
- [chat.md](chat.md) - what pending and rejected users can use
- [user-management.md](user-management.md) - approving requests, suspending users, auth audit log
- [settings.md](settings.md) - branding copy and footer links shown on the login page
- [dashboard.md](dashboard.md) - where approved users land

## Known limitations and in-flight changes

- `session_expired` is an allow-listed code that nothing emits.
- A misconfigured service key surfaces as `signin_failed` ("We couldn't verify your Microsoft sign-in"), which points users at Microsoft rather than at configuration.
- A 403 CSRF rejection on the callback (only possible when an old session cookie is sent with a mismatched `Origin`) is shown as `identity_conflict`.
- Suspending a user does not by itself revoke their sessions; their requests are refused (403) instead. A superadmin can revoke sessions explicitly in User Management.
- The UI never offers Superadmin as a requestable role, although the backend accepts it.
- The rate limit is per client IP; users behind one NAT share it.
- Coming in branch `fix/shell-nav-logout-theme-dates`: sign-out goes through a new same-origin route `POST /auth/logout` (Next server) that revokes the backend session with a 4 s bound, **always** clears the cookie (matching the cookie's `Secure` flag), sends `Clear-Site-Data: "cache"`, and the client gives up after 6 s and uses `location.replace("/login")` so Back cannot return to the signed-in page. Sign-out is single-flight, the sidebar button shows a spinner (**Signing out…**), the collapsed sidebar gains a sign-out button, and pages restored from the back/forward cache re-check the session.
- Coming in branch `perf/dashboard-load-speed`: the root layout preloads `/auth/me` on the server (1.5 s bound) so the first HTML already contains the shell instead of the full-screen spinner; on timeout or error the client falls back to its own `/auth/me` call.
- Coming in branch `fix/chat-page-fetch-timeouts`: `/auth/login` and `/auth/callback` bound their backend calls (`BACKEND_TIMEOUT_MS`, default 60 s; `BACKEND_WRITE_TIMEOUT_MS`, default 180 s, for the callback), so a hung backend ends in `backend_unavailable` instead of a hanging page; a new `global-error.tsx` adds a last-resort error page (**Something went wrong** / **This page could not be loaded** / **Try again in a moment.**).
