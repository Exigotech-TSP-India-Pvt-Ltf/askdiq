# AskDIQ production deploy runbook (backend + admin dashboard)

Target: host `Internal-Dev-Ubuntu`, user `WebAdmin`, compose dir `/opt/askdiq`,
server compose file `/opt/askdiq/docker-compose.prod.yml` (authoritative; it is
**never** replaced by anything from the kit).

| Container | Image | Built from | Env file | Networks |
|---|---|---|---|---|
| `askdiq-api` | `askdiq-api:latest` | `backend/Dockerfile.prod` | `/opt/askdiq/backend/.env` | `askdiq_internal`, `et-platform_internal` |
| `askdiq-web` (new) | `askdiq-web:latest` | `frontend/Dockerfile.prod` | `/opt/askdiq/frontend/.env` | `askdiq_internal` |

Database: `et-platform-postgres-1:5432`, database `askdiq` (never `localhost`).
The kit never contains a compose file, an nginx conf or a `.env` outside
`deploy-kit/`, and `deploy-kit/` is never extracted into `/opt/askdiq`.

---

> **STOP: deploy blocker for `preview/view` and anything built from it.**
>
> In this code, `POST /query` and every `/sessions` route require a signed-in
> SSO session (`require_role_or_chat`, `X-Session` header), not just the API
> key. The public website calls `https://askdiq.ethub.co/query` and
> `/sessions` with the API key only, so deploying this backend as it is would
> make every public chat request fail with **401**.
>
> Before deploying `preview/view` to production, the backend must keep an
> API-key-only service path for the public website (for example a
> service-key guard on `/query` and `/sessions` for that client, or a
> dedicated public route), verified on a staging copy. Until then, do not
> deploy this backend to production.
>
> Check before and after every deploy, from the server:
>
> ```bash
> curl -s -o /dev/null -w '%{http_code}\n' -X POST https://askdiq.ethub.co/query \
>   -H 'Content-Type: application/json' -H "X-API-Key: <public site key>" -d '{"query":"ping"}'
> ```
>
> This must print `200` before the deploy and still print `200` after it. A
> `401` after the deploy means the public website is down: roll the backend
> back immediately (section 8).

## 0. Decisions before the first dashboard deploy

1. **Dashboard domain.** Recommended: a subdomain such as
   `askdiq-admin.ethub.co` (DNS record + TLS certificate on et-platform nginx).
   Every `<dashboard-domain>` below means this value.
2. **nginx change** (et-platform nginx owner): a new server block from
   `deploy-kit/deploy/nginx/askdiq-admin.conf.example`. The existing
   `askdiq.ethub.co` conf is not touched.
3. **Entra redirect URI** (owner's identity team, same client id): add
   `https://<dashboard-domain>/api/auth/callback/microsoft-entra-id` (or
   `https://<dashboard-domain>/auth/callback`; the dashboard serves both) to the
   app registration's Web redirect URIs. The backend's `ENTRA_REDIRECT_URI`
   (alias `OIDC_REDIRECT_URI`) must equal the registered value exactly.

### Port plan (why no host port)

- `askdiq-web` publishes **no host port**. It joins the external network
  `askdiq_internal`, the network et-platform-nginx-1 already uses to reach
  `askdiq-api`, so nginx reaches it as `askdiq-web:3000` by container name.
  Nothing listens on the host, so nothing can clash with the other apps on the
  box (host ports 3001-3600, 5000-5600 and 5501 are taken) and nothing bypasses
  nginx/TLS.
- `askdiq-api` keeps exactly the exposure the server file already gives it.
- If a host port is ever needed for debugging, bind loopback only:
  `127.0.0.1:5500:<container port>` is the one known-free choice, for one
  service at a time, removed afterwards. Never `0.0.0.0`, never another port
  without checking `ss -ltnp` first.

### Routing: subdomain (recommended) vs path-based

The dashboard and the API both use top-level paths, so they cannot share
`askdiq.ethub.co` without one of them moving:

| Path | Dashboard (Next.js) | API (FastAPI) |
|---|---|---|
| `/auth/*` | `/auth/login`, `/auth/callback[/<provider>]` | `/auth/me`, `/auth/logout`, `/auth/sso/login`, `/auth/sso/callback`, `/auth/access-request` |
| `/admin/*` | every admin page (`/admin`, `/admin/usage`, `/admin/governance/...`) | 64 admin API routes (`/admin/usage/...`, `/admin/governance/...`) |
| `/documents` | Documents page | `/documents`, `/documents/{id}/...` |
| `/branding/assets/{sha}` | logo route (proxies to the API) | the same path on the API |
| `/mock-idp/*` | dev-only forwarder | dev-only mock IdP |
| `/api/*` | `/api/proxy/*`, `/api/auth/callback/*` | none today (free, but any future `/api` route would clash) |
| `/`, `/chat`, `/login`, `/suspended`, `/evaluation*`, `/_next/*` | dashboard | none |

A path-based setup would need the dashboard rebuilt with a Next `basePath`
(e.g. `/dashboard`, a build-time setting that also moves `/api/proxy`,
`/auth/*` and the Entra redirect URI under it) plus nginx rules for that
prefix. Moving the API instead would break existing API clients. A subdomain
needs neither, keeps cookies scoped to the dashboard host, and is the
recommendation.

---

## 1. Build the kit (Windows, local)

```powershell
cd C:\Projects\askdiq
git checkout <release-branch>   # committed code only; the kit is built from git
powershell -ExecutionPolicy Bypass -File deploy\build_kit.ps1
# -> askdiq-deploy-<sha>.tgz and a checklist; it must print "KIT OK"
```

## 2. Copy to the server (Windows)

```powershell
$kit = (Get-ChildItem C:\Projects\askdiq\askdiq-deploy-*.tgz | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
scp $kit WebAdmin@Internal-Dev-Ubuntu:~/
```

## 3. On the server: read the current state, back up

```bash
ssh WebAdmin@Internal-Dev-Ubuntu
set -u
KIT=$(ls -t ~/askdiq-deploy-*.tgz | head -1); echo "$KIT"
TS=$(date +%Y%m%d-%H%M%S)
BK=~/askdiq-backups/$TS; mkdir -p "$BK"

cd /opt/askdiq
docker ps -a --filter name=askdiq --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker inspect askdiq-api --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
#   expect: askdiq_internal et-platform_internal

# Current migration revision (needed for a rollback)
docker exec askdiq-api python -m alembic current 2>/dev/null | tee "$BK/alembic-before.txt"

# Rollback images
docker tag askdiq-api:latest askdiq-api:rollback-$TS
docker image inspect askdiq-web:latest >/dev/null 2>&1 && docker tag askdiq-web:latest askdiq-web:rollback-$TS

# Server files: compose, both .env files, current source
cp -a docker-compose.prod.yml "$BK/docker-compose.prod.yml"
cp -a backend/.env "$BK/backend.env"
[ -f frontend/.env ] && cp -a frontend/.env "$BK/frontend.env"
tar --exclude='.env' -czf "$BK/src-before.tgz" backend $( [ -d frontend ] && echo frontend )
chmod 600 "$BK"/*.env* 2>/dev/null; ls -la "$BK"
```

### Database dump (before any migration)

The DB user comes from `DATABASE_URL`; only the user name is extracted, the
password is never printed.

```bash
PGU=$(grep -E '^DATABASE_URL=' /opt/askdiq/backend/.env | sed -E 's#^DATABASE_URL=["'"'"']?[a-z+0-9]+://([^:@/]+).*#\1#')
echo "db user: $PGU"
docker exec et-platform-postgres-1 pg_dump -U "$PGU" -d askdiq -Fc > ~/askdiq-db-$TS.dump
ls -lh ~/askdiq-db-$TS.dump          # must be non-empty
docker exec -i et-platform-postgres-1 pg_restore --list < ~/askdiq-db-$TS.dump | head -5   # readable
```

If `pg_dump` asks for a password, run it as the container's superuser instead
(`-U postgres`) rather than typing the app password on the command line.

## 4. Extract only backend/ and frontend/

Extract to a staging directory first, then sync into `/opt/askdiq` without
touching any `.env`. `deploy-kit/` stays in the staging directory.

```bash
REL=~/askdiq-release/$TS; mkdir -p "$REL"
tar -xzf "$KIT" -C "$REL"
cat "$REL/deploy-kit/KIT_VERSION"
ls "$REL"                              # backend  deploy-kit  frontend

mkdir -p /opt/askdiq/frontend
# Dry run: review what would be deleted (stale files from the previous release).
for d in backend frontend; do
  rsync -an --delete --itemize-changes --exclude='/.env' --exclude='/.env.*' \
    "$REL/$d/" "/opt/askdiq/$d/" | grep '^\*deleting' || true
done
# Only server-written data should never be in that list; if it is, add an
# --exclude for it. Then apply:
for d in backend frontend; do
  rsync -a --delete --exclude='/.env' --exclude='/.env.*' "$REL/$d/" "/opt/askdiq/$d/"
done
ls -la /opt/askdiq/backend/.env       # still there, unchanged
```

(`--delete` matters: a stale migration file left from an older release would
give Alembic two heads.)

## 5. Configuration

### 5a. Frontend env (new file, first deploy only)

```bash
cp "$REL/deploy-kit/deploy/frontend.env.prod.example" /opt/askdiq/frontend/.env
chmod 600 /opt/askdiq/frontend/.env
nano /opt/askdiq/frontend/.env
```

Required values (all read at run time; nothing is baked into the image):

| Variable | Production value |
|---|---|
| `API_BASE_URL` | `http://askdiq-api:8000` |
| `API_KEY` | same value as `API_KEY_NAME` in `backend/.env` |
| `APP_BASE_URL` | `https://<dashboard-domain>` |
| `SESSION_COOKIE_NAME` | same as the backend (`askdiq_session` by default) |
| `SESSION_COOKIE_SECURE` | `true` |
| `ACCESS_CONTACT_EMAIL` | same as the backend |
| `AUTH_DEV_MOCK` | `false` |

The OIDC/Entra settings (`ENTRA_*` / `OIDC_*`: tenant, client id, client
secret, redirect URI) and the session secret (`SESSION_SECRET`) live in the
**backend** `.env`, not here: the backend runs the whole sign-in exchange and
the dashboard only relays it.

### 5b. Backend env: lines to check or add (edit one line at a time)

```bash
cd /opt/askdiq
grep -E '^(ENV|APP_BASE_URL|ENTRA_REDIRECT_URI|OIDC_REDIRECT_URI|SESSION_COOKIE_SECURE|SESSION_COOKIE_NAME|TRUST_PROXY_HEADERS|AUTH_DEV_MOCK|CORS_ALLOWED_ORIGINS)=' backend/.env
grep -cE '^DATABASE_URL=.*@et-platform-postgres-1:5432/askdiq' backend/.env   # must print 1
```

| Variable | Value | Why |
|---|---|---|
| `APP_BASE_URL` | `https://<dashboard-domain>` | CSRF: every dashboard POST/PATCH/DELETE must carry this exact Origin, otherwise **403**. Also the default redirect URI base. |
| `ENTRA_REDIRECT_URI` (or `OIDC_REDIRECT_URI`) | `https://<dashboard-domain>/api/auth/callback/microsoft-entra-id` | Sent to Entra; must match the app registration exactly. |
| `SESSION_COOKIE_SECURE` | `true` | Session cookie only over HTTPS. |
| `SESSION_COOKIE_NAME` | same as the frontend | |
| `TRUST_PROXY_HEADERS` | `true` | Client IP from `X-Forwarded-For` (audit, rate limits, audience). |
| `ENV` / `AUTH_DEV_MOCK` | `production` / `false` | |

**Cookies over HTTPS.** The dashboard sets the session cookie itself:
`HttpOnly`, `SameSite=Lax`, `Path=/`, host-only (no `Domain`, so it is scoped to
`<dashboard-domain>` and never sent to `askdiq.ethub.co`), and `Secure` when
`SESSION_COOKIE_SECURE=true`. The short-lived sign-in cookies
(`askdiq_oidc_state`, next-path) use the same flags. `SameSite=Lax` is required
(not `Strict`): the Entra redirect back to the callback is a top-level
cross-site navigation that must carry the state cookie.

**CORS.** Not needed for the dashboard. The browser only calls its own origin
(`/api/proxy/*`, `/auth/*`); the Next.js server then calls
`http://askdiq-api:8000` server-to-server, where CORS does not apply. The
dashboard's CSP (`connect-src 'self'`) would block a direct browser call to the
API anyway. Add the dashboard origin to `CORS_ALLOWED_ORIGINS` only if browser
code is ever changed to call `https://askdiq.ethub.co` directly. Leave the
existing value as it is for the API's current clients.

### 5c. Compose: add the `askdiq-web` service (do not replace the file)

```bash
cd /opt/askdiq
diff -u docker-compose.prod.yml "$REL/deploy-kit/deploy/docker-compose.prod.example.yml" || true
nano docker-compose.prod.yml
```

Add **only** the `askdiq-web:` service block from the example under
`services:`. Keep the server's existing `askdiq-api` service and its two
external networks as they are. Optional for `askdiq-api`: add the
`RUN_MIGRATIONS` / `RUN_INGESTION` / `RUN_USAGE_BACKFILL` environment lines and
`start_period: 180s` from the example (the image defaults are already
`true` / `true` / `false`).

```bash
docker compose -f docker-compose.prod.yml config --services     # lists askdiq-api and askdiq-web
docker compose -f docker-compose.prod.yml config | grep -A3 '^networks:'   # askdiq_internal + et-platform_internal, external
docker compose -f docker-compose.prod.yml config | grep -n 'published' || echo 'no new host ports'
```

## 6. Build and start

```bash
cd /opt/askdiq
docker compose -f docker-compose.prod.yml build askdiq-api askdiq-web
docker compose -f docker-compose.prod.yml up -d --force-recreate askdiq-api askdiq-web
```

### Watch the entrypoint (migrations, ingestion)

```bash
docker logs -f askdiq-api 2>&1 | grep -E --line-buffered 'prestart|alembic|unchanged|embedded|Total|ERROR|Uvicorn running'
```

Expected sequence:

```
prestart begin migrations=True ingestion=True usage_backfill=False
prestart database reachable attempt=1
prestart alembic upgrade head started / finished exit=0
prestart alembic current ... (head)
prestart ingestion started
<doc>.md: unchanged (embedded 0, kept N, removed 0)      # changed docs are re-embedded
prestart ingestion finished exit=0
prestart done
Uvicorn running on http://0.0.0.0:8000
```

- `prestart FAILED, app not started: ...` means migrations or the DB failed;
  the container exits and restarts (restart policy) and keeps failing loudly
  until fixed. Nothing serves traffic on a half-migrated schema. Go to
  Rollback.
- `prestart ingestion failed ...; API starts anyway` is non-fatal: the API
  runs with the previous knowledge base. Re-run by hand later:
  `docker exec askdiq-api python scripts/ingest_real_docs.py`.
- Usage backfill (only once, when asked): set `RUN_USAGE_BACKFILL: "true"`
  on `askdiq-api`, `up -d --force-recreate askdiq-api`, check the log for
  `prestart usage backfill finished exit=0`, then set it back to `"false"`.
- Toggles: `RUN_MIGRATIONS=false` / `RUN_INGESTION=false` skip a step. A
  separate job worker (`python -m app.jobs.worker`) never runs either step.

## 7. Verify

```bash
docker ps --filter name=askdiq --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
#   askdiq-api  Up (healthy)   askdiq-web  Up (healthy), no host ports for askdiq-web

# Alembic: current must equal head
docker exec askdiq-api python -m alembic current
docker exec askdiq-api python -m alembic heads

# API health (through nginx)
curl -fsS https://askdiq.ethub.co/health

# Public website path (API key only, no session): must still be 200, see the STOP box at the top
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://askdiq.ethub.co/query \
  -H 'Content-Type: application/json' -H "X-API-Key: <public site key>" -d '{"query":"ping"}'

# Dashboard reachable from the nginx container by name
docker exec et-platform-nginx-1 wget -qO- -S http://askdiq-web:3000/login 2>&1 | head -1
#   HTTP/1.1 200 OK   (no wget in that image? use: curl -sI http://askdiq-web:3000/login | head -1)
docker exec askdiq-web node -e "fetch('http://askdiq-api:8000/health').then(r=>console.log(r.status))"
#   200
```

After the nginx owner has applied the server block and reloaded
(`docker exec et-platform-nginx-1 nginx -t && docker exec et-platform-nginx-1 nginx -s reload`):

1. `https://<dashboard-domain>/login` shows "Sign in with Microsoft", and
   `curl -sI https://<dashboard-domain>/admin | grep -i '^location'` shows
   `https://<dashboard-domain>/login?next=%2Fadmin`. If it shows
   `localhost:3000`, the `proxy_redirect` line of the nginx block is missing:
   the Next.js middleware builds that redirect from its own listen address.
2. Sign in with a `SUPERADMIN_EMAILS` account: lands on the dashboard. In the
   browser dev tools the `askdiq_session` cookie shows `HttpOnly`, `Secure`,
   `SameSite=Lax`.
3. Open Admin > Governance > Monitoring: the live feed connects
   (`/api/proxy/admin/governance/stream` stays open, events arrive).
4. `/query` with the forwarded IP: ask one question in Chat
   (`/chat` -> `POST /api/proxy/query`). Then open the conversation in Admin >
   Conversations (or Governance > Logs): its `ip_address` must be your public
   client IP, not a Docker address (172.x). A 172.x address means
   `TRUST_PROXY_HEADERS` is not `true` or nginx is not setting
   `X-Forwarded-For`.
5. Sign out works and returns to `/login`.

## 8. Rollback

### Dashboard only

```bash
cd /opt/askdiq
docker compose -f docker-compose.prod.yml stop askdiq-web
# previous image, if there was one:
docker tag askdiq-web:rollback-$TS askdiq-web:latest && \
  docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-web
```

The nginx block can stay; it returns 502 for the dashboard host only.

### Backend, no new migrations (alembic-before.txt == current)

```bash
cd /opt/askdiq
docker tag askdiq-api:rollback-$TS askdiq-api:latest
docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-api
curl -fsS https://askdiq.ethub.co/health
```

### Backend, new migrations were applied

The old image does not know the new revisions, so downgrade with the **new**
image first, then swap images. The entrypoint runs a non-uvicorn command as
is (no prestart):

```bash
cd /opt/askdiq
PREV=$(grep -oE '^[0-9a-f]{12}' "$BK/alembic-before.txt" | head -1); echo "$PREV"
docker compose -f docker-compose.prod.yml stop askdiq-api
docker compose -f docker-compose.prod.yml run --rm --no-deps askdiq-api python -m alembic downgrade "$PREV"
docker tag askdiq-api:rollback-$TS askdiq-api:latest
docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-api
docker exec askdiq-api python -m alembic current      # == $PREV
```

Read the downgrade functions of the new revisions first
(`backend/alembic/versions/`): a downgrade that drops a column or table loses
the data written since the deploy. If that is not acceptable, or the
downgrade fails, restore the dump instead.

### Last resort: restore the dump

```bash
cd /opt/askdiq
docker compose -f docker-compose.prod.yml stop askdiq-api askdiq-web
docker exec -i et-platform-postgres-1 pg_restore -U "$PGU" -d askdiq --clean --if-exists --no-owner --single-transaction < ~/askdiq-db-$TS.dump
docker tag askdiq-api:rollback-$TS askdiq-api:latest
docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-api
```

Everything written after the dump (conversations, audit events) is lost.

### Server files

Restore from `$BK` (`docker-compose.prod.yml`, `backend.env`, `frontend.env`,
`src-before.tgz`) only if they were changed; `cp -a "$BK/backend.env"
/opt/askdiq/backend/.env` restores the env file byte for byte.

## 9. After the deploy

- Keep `$BK`, the dump and the rollback images for at least a week, then
  `docker image rm askdiq-api:rollback-<ts>` and delete old dumps.
- `rm -rf ~/askdiq-release/<ts>` once the deploy is confirmed.


## Keep the API self-healing (once, after the first deploy)

Docker restarts a container that crashes (`restart: unless-stopped`) but not one
that is alive and hung: the healthcheck only marks it `unhealthy`. Install the
watchdog so an unhealthy `askdiq-*` container is restarted within a minute:

```bash
chmod +x /opt/askdiq/deploy-kit/askdiq-autoheal.sh
( crontab -l 2>/dev/null; echo '* * * * * /opt/askdiq/deploy-kit/askdiq-autoheal.sh >> /var/log/askdiq-autoheal.log 2>&1' ) | crontab -
docker inspect --format '{{.State.Health.Status}}' askdiq-api   # healthy
```

It only touches containers whose name starts with `askdiq-`; other apps on the host are never restarted.
