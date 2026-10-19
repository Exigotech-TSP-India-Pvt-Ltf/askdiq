# Production environment and SSO (askdiqui.ethub.co)

Step-by-step setup of every `.env` value and of Microsoft Entra ID sign-in for
the production admin console at **https://askdiqui.ethub.co**. It complements
the [deployment guide](deployment-guide.md) (kit, backups, build, start,
rollback) and uses the same layout:

| Item | Value |
|---|---|
| Server / user | `Internal-Dev-Ubuntu` / `WebAdmin`, compose dir `/opt/askdiq` |
| API (existing) | `https://askdiq.ethub.co` -> nginx -> `askdiq-api:8000` (public website, API key) |
| Admin console (new) | `https://askdiqui.ethub.co` -> nginx -> `askdiq-web:3000` (staff, SSO) |
| Backend env file | `/opt/askdiq/backend/.env` (read by `askdiq-api`) |
| Frontend env file | `/opt/askdiq/frontend/.env` (read by `askdiq-web`) |
| Database | `et-platform-postgres-1:5432`, database `askdiq` |

Rules: edit the server `.env` files in place with `nano`, one line at a time,
after a backup (`cp -a backend/.env ~/askdiq.env.bak.$(date +%Y%m%d-%H%M%S)`).
Never upload a `.env` from a laptop, never paste a secret into a ticket or
chat, never commit a real value. The templates are
[`infra/backend.env.prod.example`](../../infra/backend.env.prod.example) and
[`infra/frontend.env.prod.example`](../../infra/frontend.env.prod.example);
[`backend/.env.example`](../../backend/.env.example) lists every backend
setting.

How sign-in works (why the values below must match): the browser only talks
to `askdiqui.ethub.co`. "Sign in with Microsoft" calls `/auth/login` on the
console, which asks the API for the Entra authorize URL. Entra redirects the
browser back to the console's callback route, and the console's server sends
the `code` to the API, which redeems it with the client secret (authorization
code flow with PKCE), validates the ID token (tenant, nonce, signature),
creates a server-side session and returns it. The console stores the session in
an `HttpOnly`, `Secure`, `SameSite=Lax`, host-only cookie (`askdiq_session`).
All Entra settings live in the **backend** `.env`; the frontend only relays.

---

## 1. Checklist

- [ ] DNS: `A` record `askdiqui.ethub.co` -> the same public IP as `askdiq.ethub.co`
- [ ] TLS certificate and nginx server blocks for `askdiqui.ethub.co`
- [ ] Entra ID app registration: redirect URI, secret, permissions
- [ ] Secrets generated: `SESSION_SECRET` (and `API_KEY_NAME` only for a new install)
- [ ] Backend `.env` values (section 5)
- [ ] Frontend `.env` values (section 6)
- [ ] Deploy (deployment guide, sections 2-3)
- [ ] Verification (section 9)

---

## 2. DNS

At the DNS provider for `ethub.co`, add:

| Type | Name | Value | TTL |
|---|---|---|---|
| `A` | `askdiqui` | the public IPv4 of `Internal-Dev-Ubuntu` (same as `askdiq.ethub.co`) | 300 |

Check (from any machine):

```bash
dig +short askdiq.ethub.co
dig +short askdiqui.ethub.co      # must print the same IP
```

---

## 3. TLS and nginx

Follow [deployment guide section 1a-1c](deployment-guide.md#1-one-time-prerequisites-checklist)
exactly; it appends the ready-made blocks from
[`infra/nginx/askdiq-admin.append.conf`](../../infra/nginx/askdiq-admin.append.conf)
to the existing askdiq conf of `et-platform-nginx-1`. Short version:

```bash
D=askdiqui.ethub.co
sed "s#<DASHBOARD_DOMAIN>#$D#g" ~/askdiq-admin.append.conf > /tmp/askdiq-web.conf
# 1. append PART 1 (port 80 + ACME webroot), nginx -t, reload
# 2. issue the certificate (no email):
sudo certbot certonly --webroot -w /var/www/certbot -d "$D" \
  --register-unsafely-without-email --agree-tos --non-interactive
# 3. append PART 2 (port 443), nginx -t, reload
docker exec et-platform-nginx-1 nginx -t && docker exec et-platform-nginx-1 nginx -s reload
```

The blocks already contain what the console needs behind nginx:

- `proxy_set_header X-Forwarded-For $remote_addr` (set, not appended, so a
  browser cannot spoof its IP) and `X-Forwarded-Proto $scheme`;
- `proxy_redirect` that rewrites `http://localhost:3000/...` redirects to
  `https://askdiqui.ethub.co/...` (required);
- an unbuffered SSE location for the live governance feed;
- `client_max_body_size 15m` (document uploads).

Do not add a Content-Security-Policy or other security headers for this host:
Next.js sends its own (nonce CSP, HSTS, frame and nosniff headers).

---

## 4. Microsoft Entra ID app registration

Use the existing AskDIQ app registration if there is one (same client id), or
create one. Entra admin center (https://entra.microsoft.com) > **Identity >
Applications > App registrations**.

### 4.1 Create or open the registration

| Setting | Value |
|---|---|
| Name | `AskDIQ` (any) |
| Supported account types | **Accounts in this organizational directory only (single tenant)**. The API rejects tokens from any other tenant. |
| Platform | **Web** (not SPA, not mobile): the code is redeemed server-side with a client secret |

Note the **Application (client) ID** and **Directory (tenant) ID** from the
Overview page: they become `ENTRA_CLIENT_ID` and `ENTRA_TENANT_ID`.

### 4.2 Redirect URIs

**Authentication > Platform configurations > Web > Redirect URIs**, add:

```
https://askdiqui.ethub.co/api/auth/callback/microsoft-entra-id
```

The API sends the value of `ENTRA_REDIRECT_URI` to Entra verbatim, so the
registered URI and `ENTRA_REDIRECT_URI` must be byte-for-byte identical
(scheme `https`, host, path, no trailing slash). The console serves three
equivalent callback routes; any of them works as long as both sides use the
same one:

| Callback URL | Use |
|---|---|
| `https://askdiqui.ethub.co/api/auth/callback/microsoft-entra-id` | **Recommended** (matches the deployment guide; reuses an Entra registration shared with NextAuth-style apps) |
| `https://askdiqui.ethub.co/auth/callback` | The code's default when `ENTRA_REDIRECT_URI` is empty (`APP_BASE_URL` + `/auth/callback`) |
| `https://askdiqui.ethub.co/auth/callback/microsoft-entra-id` | Provider-suffixed variant |

Registering the first two is safe and avoids a lock-out if
`ENTRA_REDIRECT_URI` is ever left empty. For local development you may also
register `http://localhost:3000/auth/callback`.

On the same page:

| Setting | Value |
|---|---|
| Front-channel logout URL | **Leave empty.** AskDIQ does not implement Entra front-channel logout. Signing out of AskDIQ revokes the AskDIQ session only; it does not sign the user out of Microsoft. |
| Implicit grant and hybrid flows: Access tokens / ID tokens | **Both unchecked.** AskDIQ uses the authorization code flow with PKCE; the ID token comes from the token endpoint, not the implicit flow. |
| Allow public client flows | **No** |

### 4.3 Client secret

**Certificates & secrets > Client secrets > New client secret**:

- Description `askdiq-prod-<yyyy-mm>`, expiry 12 or 24 months (your policy).
- Copy the secret **Value** immediately (it is shown once). This is
  `ENTRA_CLIENT_SECRET`. The "Secret ID" is not the secret.
- Put the expiry date in the team calendar about 30 days ahead. To rotate:
  create a new secret, replace `ENTRA_CLIENT_SECRET` in the backend `.env`,
  recreate `askdiq-api`, test a sign-in, then delete the old secret. An
  expired secret makes every sign-in fail with `AADSTS7000222`.

### 4.4 API permissions

**API permissions**: Microsoft Graph, **Delegated**: `openid`, `profile`,
`email` (`User.Read` may stay; it is not used). Then **Grant admin consent
for <tenant>** so users are not asked to consent.

### 4.5 Optional

- **Token configuration > Add optional claim > ID > `email`**. Without it
  AskDIQ uses `preferred_username` (normally the UPN, which is the email).
- **Enterprise applications > AskDIQ > Properties > Assignment required =
  Yes**, then assign users or groups, to stop people outside those groups from
  reaching AskDIQ's sign-in at all. AskDIQ itself also gates access: a new
  user is **pending** (chat only) until an admin approves them in User
  Management.

---

## 5. Backend `.env` (`/opt/askdiq/backend/.env`)

### 5.1 Generate secrets (on the server)

```bash
openssl rand -hex 32          # SESSION_SECRET (64 hex chars; at least 32 bytes are required in production)
openssl rand -hex 32          # API_KEY_NAME, ONLY for a brand-new install (see below)
```

Without `openssl`: `python3 -c "import secrets; print(secrets.token_hex(32))"`.

- `SESSION_SECRET`: changing it later signs everyone out. Never reuse the
  development value.
- `API_KEY_NAME` is the service key the **public website already uses** to
  call `https://askdiq.ethub.co/query`. On the existing server keep the
  current value; a new value would break the website until it is updated
  too. The console's `API_KEY` must equal it.

### 5.2 Required values

| Variable | Production value | Secret | What it does |
|---|---|---|---|
| `ENV` | `production` | No | Turns on HSTS, hides `/docs`, enforces the 32-byte `SESSION_SECRET`, refuses `AUTH_DEV_MOCK=true` |
| `DATABASE_URL` | `postgresql+asyncpg://<user>:<password>@et-platform-postgres-1:5432/askdiq` | **Yes** | The one Postgres (data + pgvector). Never `localhost`; `deploy.sh` refuses any other host |
| `AZURE_OPENAI_API_KEY` | key of the Azure OpenAI resource | **Yes** | LLM and embeddings |
| `AZURE_OPENAI_ENDPOINT` | `https://<resource>.openai.azure.com/` | No | Azure OpenAI endpoint |
| `AZURE_OPENAI_API_VERSION` | `2024-12-01-preview` | No | API version |
| `AZURE_DEPLOYMENT_NAME` | `gpt-5-mini` (your chat deployment name) | No | Default chat deployment (Settings > Models can override it) |
| `EMBEDDING_MODEL` | `text-embedding-3-small` (your embedding deployment name) | No | Default embedding deployment (overridable in Settings > Models) |
| `EMBEDDING_DIM` | `1536` | No | Must match the pgvector column; do not change |
| `API_KEY_NAME` | existing website key (5.1) | **Yes** | Service key: the website and the console's server send it as `X-API-Key` |
| `SESSION_SECRET` | from 5.1 | **Yes** | Signs server-side sessions |
| `ENTRA_TENANT_ID` | Directory (tenant) ID (4.1) | No | Pins the tenant; tokens from other tenants are rejected |
| `ENTRA_CLIENT_ID` | Application (client) ID (4.1) | No | App registration |
| `ENTRA_CLIENT_SECRET` | secret **Value** (4.3) | **Yes** | Redeems the authorization code |
| `ENTRA_REDIRECT_URI` | `https://askdiqui.ethub.co/api/auth/callback/microsoft-entra-id` | No | Sent to Entra; must equal the registered URI exactly |
| `APP_BASE_URL` | `https://askdiqui.ethub.co` | No | Console origin, no trailing slash. Used for the CSRF `Origin` check on every POST/PATCH/DELETE; must equal the frontend's |
| `SESSION_COOKIE_NAME` | `askdiq_session` | No | Must equal the frontend's |
| `SESSION_COOKIE_SECURE` | `true` | No | Cookie only over HTTPS |
| `TRUST_PROXY_HEADERS` | `true` | No | Read the client IP from `X-Forwarded-For` (nginx sets it) for audit, rate limits and audience; `false` shows container IPs (`172.x`) |
| `AUTH_DEV_MOCK` | `false` | No | Development-only mock identity provider; the API refuses to start with `true` when `ENV` is not `development` |
| `SUPERADMIN_EMAILS` | e.g. `darshan.patel@exigotech.com.au` (comma-separated, lower or mixed case) | No | These emails become **active superadmins** when they sign in (new or still-pending accounts; an already active or suspended account is not changed). Set at least one before go-live, or nobody can approve users |
| `ACCESS_CONTACT_EMAIL` | the admin mailbox to contact for access | No | Shown on the Access Required and suspended pages |

`OIDC_TENANT_ID`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`
and `OIDC_DISCOVERY_URL` are accepted aliases (the tenant can come from the
discovery URL). If both an `ENTRA_*` and an `OIDC_*` name are set, `ENTRA_*`
wins. Use one family only.

### 5.3 Recommended values

| Variable | Value | What it does |
|---|---|---|
| `ALLOW_CLIENT_IP_OVERRIDE` | `true` while the public website sends the visitor IP in the `/query` body (`ip_address`); otherwise `false` | Lets the API-key caller supply the end user's IP. Code default is `true`. `deploy.sh` warns when the forwarded IP is not echoed |
| `CORS_ALLOWED_ORIGINS` | keep the server's current value (JSON list) | Only for browsers that call `https://askdiq.ethub.co` directly. The console needs **no** CORS entry: its browser code only calls its own origin |
| `LOG_LEVEL` | `info` | Log verbosity |
| `WEB_CONCURRENCY`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | `1`, `20`, `10` (defaults) | Keep `workers x (DB_POOL_SIZE + DB_MAX_OVERFLOW) <= max_connections - 20`; the DB is shared |
| `MONTHLY_COST_BUDGET_USD` | e.g. `100` | Budget line on the dashboard cost chart; unset = none |
| `ONLINE_EVAL_ENABLED`, `ONLINE_EVAL_SAMPLE_RATE` | `true`, `0.10` | Sampled live answer-quality scoring (Evaluation page cards stay empty while off) |
| `ENABLE_API_DOCS` | `false` | Keep `/docs` off in production |

Everything else (session lifetimes, OIDC timeouts, rate limits, governance
thresholds, job queue, SSE, branding defaults, insights) has safe defaults;
the full list with defaults is in [`backend/.env.example`](../../backend/.env.example)
and [technical/06-configuration.md](../technical/06-configuration.md).
Useful ones: `SESSION_TTL_MINUTES` (480), `SESSION_IDLE_MINUTES` (60),
`AUTH_LOGIN_RATE_LIMIT_PER_MINUTE` (10), `SSE_MAX_STREAM_SECONDS` (120),
`ENTRA_AUTHORITY_HOST` (`https://login.microsoftonline.com`, leave it).

### 5.4 Settings > Models & Pricing (runtime overrides)

From the release that contains `feat/settings-models-renames`, a superadmin
can change the chat deployment, the embedding deployment and the four token
prices in **Settings > Models & Pricing** without a redeploy. Each new
deployment name is tested with one live Azure call before it is saved, and the
embedding deployment must keep `EMBEDDING_DIM` (1536). The `.env` values
become the defaults:

| Variable | Default | What it does |
|---|---|---|
| `AZURE_DEPLOYMENT_NAME`, `EMBEDDING_MODEL` | (required) | Default deployments until overridden |
| `PRICE_EMBED_PER_MILLION` | `0.02` | USD per 1M embedding tokens |
| `PRICE_PROMPT_PER_MILLION` | `0.25` | USD per 1M prompt tokens |
| `PRICE_OUTPUT_PER_MILLION` | `2.00` | USD per 1M completion (incl. reasoning) tokens |
| `PRICE_CACHED_PER_MILLION` | `0.125` | USD per 1M cached prompt tokens |
| `MODEL_SETTINGS_REFRESH_SECONDS` | `60` | How often each worker re-reads the overrides |
| `MODEL_SETTINGS_LOAD_TIMEOUT_SECONDS` | `5` | Bound on one read; the last good values are kept on failure |

The release applies one new migration (the model settings table); the
entrypoint runs it automatically under the advisory lock.

### 5.5 Email alerts (SMTP)

Available from the release that contains the alerts work (branch
`feat/email-alerts-error-hardening`, in progress). Until that release is
deployed these variables are ignored. Names:

| Variable | Example | Secret |
|---|---|---|
| `ALERTS_ENABLED` | `true` | No |
| `SMTP_HOST` | `smtp.office365.com` | No |
| `SMTP_PORT` | `587` | No |
| `SMTP_USERNAME` | the sending mailbox | No |
| `SMTP_PASSWORD` | its password or app password | **Yes** |
| `SMTP_STARTTLS` | `true` | No |
| `ALERT_EMAIL_FROM` | `askdiq-alerts@exigotech.com.au` | No |
| `ALERT_EMAIL_TO` | recipient address(es) | No |

Check the release notes of that release for exact semantics and defaults
before setting them.

### 5.6 Apply

```bash
cd /opt/askdiq
cp -a backend/.env ~/askdiq.env.bak.$(date +%Y%m%d-%H%M%S)
nano backend/.env
# check names without printing secrets:
grep -E '^(ENV|APP_BASE_URL|ENTRA_REDIRECT_URI|SESSION_COOKIE_SECURE|SESSION_COOKIE_NAME|TRUST_PROXY_HEADERS|AUTH_DEV_MOCK|SUPERADMIN_EMAILS)=' backend/.env
grep -cE '^(SESSION_SECRET|ENTRA_CLIENT_SECRET|API_KEY_NAME)=.+' backend/.env      # must print 3
grep -cE '^DATABASE_URL=.*@et-platform-postgres-1:5432/askdiq' backend/.env        # must print 1
```

An `.env`-only change needs a recreate, not a rebuild:
`docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-api`.

Optional preflight inside the container (prints check names and PASS/FAIL,
never values):

```bash
docker exec askdiq-api python scripts/verify_sso_config.py
```

---

## 6. Frontend `.env` (`/opt/askdiq/frontend/.env`)

All values are server-only and read at run time (there are no
`NEXT_PUBLIC_*` variables), so a change needs only a recreate of
`askdiq-web`.

```bash
cp deploy-kit/infra/frontend.env.prod.example /opt/askdiq/frontend/.env   # first deploy only (path inside the extracted kit)
chmod 600 /opt/askdiq/frontend/.env
nano /opt/askdiq/frontend/.env
```

| Variable | Production value | Required | Secret | What it does |
|---|---|---|---|---|
| `API_BASE_URL` | `http://askdiq-api:8000` | Yes | No | API by container name on `askdiq_internal`. Never `localhost`, never the public URL |
| `API_KEY` | same value as backend `API_KEY_NAME` | Yes | **Yes** | Sent as `X-API-Key` by the server-side proxy |
| `APP_BASE_URL` | `https://askdiqui.ethub.co` | Yes | No | Public origin for redirects and the sign-in flow; must equal the backend's |
| `SESSION_COOKIE_NAME` | `askdiq_session` | Yes | No | Must equal the backend's |
| `SESSION_COOKIE_SECURE` | `true` | Yes | No | Session cookie only over HTTPS |
| `ACCESS_CONTACT_EMAIL` | same as the backend | No | No | Shown on the login and suspended pages before a session exists |
| `AUTH_DEV_MOCK` | `false` | Yes | No | Never `true` in production |
| `BACKEND_TIMEOUT_MS` | `60000` (default) | No | No | Proxy wait for the API's response headers on reads before a 504 |
| `BACKEND_WRITE_TIMEOUT_MS` | `180000` (default) | No | No | Same for writes (chat answers, uploads) |
| `BACKEND_RETRIES` | `2` (default) | No | No | Retries of reads and sign-out after connection errors or 502/503/504 (0 disables, max 5) |

The session cookie has no `Domain` attribute (host-only on
`askdiqui.ethub.co`), is always `HttpOnly`, `SameSite=Lax` (required for the
Entra redirect back), `Path=/`. There is no cookie-domain setting.

`deploy.sh` checks before deploying that `API_BASE_URL` is
`http://askdiq-api:8000`, `API_KEY` equals the backend `API_KEY_NAME`, and
`APP_BASE_URL` is the same in both files (values are never printed).

---

## 7. Minimal value sets

Backend (`/opt/askdiq/backend/.env`), names only:

```
ENV=production
DATABASE_URL=postgresql+asyncpg://<user>:<password>@et-platform-postgres-1:5432/askdiq
AZURE_OPENAI_API_KEY=<secret>
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_DEPLOYMENT_NAME=gpt-5-mini
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
API_KEY_NAME=<existing website key>
SESSION_SECRET=<openssl rand -hex 32>
ENTRA_TENANT_ID=<tenant id>
ENTRA_CLIENT_ID=<client id>
ENTRA_CLIENT_SECRET=<secret value>
ENTRA_REDIRECT_URI=https://askdiqui.ethub.co/api/auth/callback/microsoft-entra-id
APP_BASE_URL=https://askdiqui.ethub.co
SESSION_COOKIE_NAME=askdiq_session
SESSION_COOKIE_SECURE=true
TRUST_PROXY_HEADERS=true
AUTH_DEV_MOCK=false
SUPERADMIN_EMAILS=<your email>
ACCESS_CONTACT_EMAIL=<admin mailbox>
```

Frontend (`/opt/askdiq/frontend/.env`):

```
API_BASE_URL=http://askdiq-api:8000
API_KEY=<same as backend API_KEY_NAME>
APP_BASE_URL=https://askdiqui.ethub.co
SESSION_COOKIE_NAME=askdiq_session
SESSION_COOKIE_SECURE=true
ACCESS_CONTACT_EMAIL=<admin mailbox>
AUTH_DEV_MOCK=false
```

---

## 8. Deploy

Build the kit and deploy with the [deployment guide](deployment-guide.md)
(sections 2 and 3; `infra/build_kit.ps1`, then `deploy.sh` on the server).
Kits built from this release keep the scripts under `deploy-kit/infra/` and
the guides under `deploy-kit/docs/deployment/` (older kits used
`deploy-kit/deploy/`).

---

## 9. Verification

On the server:

```bash
# API health (database + pool)
curl -fsS https://askdiq.ethub.co/health; echo
#   {"status":"ok","database":"healthy",...}

# Console up behind nginx, HTTPS redirect, no localhost leak
curl -sI https://askdiqui.ethub.co/login | head -1                 # HTTP/2 200
curl -sI http://askdiqui.ethub.co/login | grep -i '^location'      # https://askdiqui.ethub.co/login
curl -sI https://askdiqui.ethub.co/admin | grep -i '^location'     # https://askdiqui.ethub.co/login?next=%2Fadmin

# /auth/me without a session must be 401 (the proxy and the API are wired)
curl -s -o /dev/null -w '%{http_code}\n' https://askdiqui.ethub.co/api/proxy/auth/me   # 401

# Console server reaches the API
docker exec askdiq-web node -e "fetch('http://askdiq-api:8000/health').then(r=>console.log('api',r.status))"   # api 200

# Public website path still works (API key only)
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://askdiq.ethub.co/query \
  -H 'Content-Type: application/json' -H "X-API-Key: <public site key>" -d '{"query":"ping"}'   # 200
```

In a private browser window:

1. Open `https://askdiqui.ethub.co`, you land on `/login`.
2. **Sign in with Microsoft** -> Microsoft sign-in -> back on the console.
   The browser address bar on the way back shows
   `https://askdiqui.ethub.co/api/auth/callback/microsoft-entra-id?code=...`.
3. With an email in `SUPERADMIN_EMAILS` you reach the Dashboard; any other
   account sees Access Required (pending) until approved in User Management.
4. DevTools > Network: `GET /api/proxy/auth/me` returns **200** with your
   `user`, `role`, `status` and `permissions`.
5. DevTools > Application > Cookies: `askdiq_session` is `HttpOnly`, `Secure`,
   `SameSite=Lax`, domain `askdiqui.ethub.co`.
6. Save any harmless setting (no 403), ask a question in AskDIQ, and check that
   Governance > Logs shows your public IP, not `172.x`.
7. Sign out: back on `/login`; `/admin` redirects to `/login`.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Microsoft shows **AADSTS50011** (redirect URI mismatch) | `ENTRA_REDIRECT_URI` is not registered, or differs by scheme, host, path or trailing slash; or `ENTRA_REDIRECT_URI` is empty and the default `.../auth/callback` is not registered | Register exactly the `ENTRA_REDIRECT_URI` value under **Web** redirect URIs (4.2); recreate `askdiq-api` after changing `.env` |
| **AADSTS700016** (application not found in the directory) | Wrong `ENTRA_CLIENT_ID`, or the tenant id belongs to another directory | Copy both ids again from the registration's Overview page |
| **AADSTS7000215** (invalid client secret) | The secret ID was pasted instead of the secret Value, or a typo | Create a new secret, paste its Value into `ENTRA_CLIENT_SECRET`, recreate `askdiq-api` |
| **AADSTS7000222** (client secret expired) | Secret past its expiry | Rotate (4.3) |
| **AADSTS50105** / "not assigned to a role for the application" | Assignment required = Yes and the user or group is not assigned | Assign them in Enterprise applications, or turn assignment off |
| **AADSTS65001** / consent prompt for every user | Admin consent not granted | Grant admin consent for `openid profile email` (4.4) |
| Platform error mentioning a cross-origin or SPA redemption | Redirect URI registered under **Single-page application** | Move it to the **Web** platform |
| Login page: "Your sign-in session didn't match" (`state_mismatch`) or "expired" | The `askdiq_oidc_state` cookie was lost: the site was opened over `http`, the host changed between start and callback, or the attempt took over 10 minutes | Always use `https://askdiqui.ethub.co`; check the nginx HTTP->HTTPS redirect; start again |
| "We couldn't verify your Microsoft sign-in" (`signin_failed`, API 401) | ID token rejected: wrong tenant, clock skew, or the account has no usable email | Check `ENTRA_TENANT_ID`; server clock (NTP); add the `email` optional claim (4.5). `docker logs askdiq-api` shows the reason |
| "This email is linked to a different Microsoft account" (`identity_conflict`) | The email exists in AskDIQ for another Entra identity | An admin fixes the user in User Management |
| "Microsoft sign-in is unavailable" (`idp_unavailable`) | The API cannot reach `login.microsoftonline.com` (outbound HTTPS blocked) or the discovery URL is wrong | Allow outbound 443 from `askdiq-api`; leave `OIDC_DISCOVERY_URL` empty unless needed |
| "The service is unavailable" (`backend_unavailable`) | `askdiq-web` cannot reach `askdiq-api:8000` | `API_BASE_URL=http://askdiq-api:8000`; both containers on `askdiq_internal`; API healthy |
| Signed in at Microsoft but sent back to `/login` (loop) | Cookie not stored: `SESSION_COOKIE_SECURE`/`SESSION_COOKIE_NAME` differ between the two `.env` files, or the site is reached over `http` | Same name and `true` in both; HTTPS only; nginx sends `X-Forwarded-Proto $scheme` |
| 403 "Cross-site request rejected." on saves | `APP_BASE_URL` is not exactly `https://askdiqui.ethub.co` in both files | Fix both (no trailing slash), recreate both containers |
| 401 on every console call although signed in | Frontend `API_KEY` differs from backend `API_KEY_NAME` | Make them equal; recreate `askdiq-web` |
| Redirect to `http://localhost:3000/...` | `proxy_redirect` missing in the nginx block | Add it (section 3), `nginx -t`, reload |
| API does not start: "Missing SSO settings" | `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID` or `ENTRA_CLIENT_SECRET` empty | Fill them (5.2) |
| API does not start: "AUTH_DEV_MOCK=true is only allowed when ENV=development" | Mock left on | `AUTH_DEV_MOCK=false` |
| API does not start: "SESSION_SECRET must be at least 32 bytes in production" | Short secret | `openssl rand -hex 32` |
| Nobody can approve users | `SUPERADMIN_EMAILS` empty when the first admin signed in | Add the email, recreate `askdiq-api`, and sign in again with it (a pending account is promoted to active superadmin on sign-in) |
| Client IP `172.x` everywhere | `TRUST_PROXY_HEADERS` not `true` | Set it, recreate `askdiq-api` |

More deploy-side symptoms (502, networks, migrations, ingestion, SSE
buffering): [deployment guide section 6](deployment-guide.md#6-troubleshooting).
