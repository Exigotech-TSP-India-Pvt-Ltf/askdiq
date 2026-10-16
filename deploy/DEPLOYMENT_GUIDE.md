# AskDIQ production deployment guide (API + admin dashboard)

Copy-paste guide for deploying `askdiq-api` (FastAPI) and `askdiq-web`
(Next.js 14 dashboard) to `Internal-Dev-Ubuntu`. Every command is meant to be
pasted as is, except the placeholders below.

| Placeholder | Meaning |
|---|---|
| `<DASHBOARD_DOMAIN>` | Dashboard host name, not decided yet (e.g. `askdiq-admin.ethub.co`). Used everywhere below. |
| `<public site key>` | The API key the public website sends (`API_KEY_NAME` in the backend `.env`). Never paste it into a ticket or chat. |
| `<release-branch>` | The git branch/commit being released. |

Golden rules (the 2026-10-16 outage came from breaking them):

- The server owns `/opt/askdiq/docker-compose.prod.yml`, the nginx conf and
  both `.env` files. Never overwrite them. Add lines; never replace files.
- The kit contains only `backend/`, `frontend/` and `deploy-kit/`. Only
  `backend/` and `frontend/` are extracted into `/opt/askdiq`.
- `DATABASE_URL` points at `et-platform-postgres-1:5432/askdiq`, never
  `localhost`.
- Never touch containers, networks or ports of other apps on the host.

---

## 0. Summary

### What runs where

| Item | Value |
|---|---|
| Host / user | `Internal-Dev-Ubuntu` / `WebAdmin` |
| Compose dir / file | `/opt/askdiq` / `docker-compose.prod.yml` (server copy is authoritative) |
| Reverse proxy | `et-platform-nginx-1` (shared by other apps), TLS terminates here |
| Database | `et-platform-postgres-1:5432`, database `askdiq` (Postgres + pgvector) |

| Container | Image | Built from | Env file | Networks (both external) | Host ports | Reached as |
|---|---|---|---|---|---|---|
| `askdiq-api` | `askdiq-api:latest` | `backend/Dockerfile.prod` | `/opt/askdiq/backend/.env` | `askdiq_internal`, `et-platform_internal` | unchanged from the server file | `askdiq-api:8000` (nginx, dashboard) |
| `askdiq-web` (new) | `askdiq-web:latest` | `frontend/Dockerfile.prod` | `/opt/askdiq/frontend/.env` | `askdiq_internal` | **none** | `askdiq-web:3000` (nginx only) |

| Domain | Served by | Who calls it |
|---|---|---|
| `https://askdiq.ethub.co` | nginx -> `askdiq-api:8000` (existing, unchanged) | public website (API key), integrations |
| `https://<DASHBOARD_DOMAIN>` | nginx -> `askdiq-web:3000` (new) | browsers (SSO users) |

Request path for the dashboard: browser -> nginx (TLS) -> `askdiq-web:3000`
-> (server-to-server, `X-API-Key` + session) -> `askdiq-api:8000` -> Postgres.
The browser never calls the API directly.

### Why `askdiq-web` publishes no host port

- nginx and `askdiq-web` share the Docker network `askdiq_internal`, so nginx
  reaches it by container name. A host port adds nothing for production.
- Host ports 3001-3600, 5000-5600 and 5501 belong to other apps; with no
  published port there is nothing to clash with.
- A published port on `0.0.0.0` would let anyone on the network reach the
  dashboard over plain HTTP, bypassing nginx, TLS and HSTS, and the session
  cookie (`Secure`) would not work there anyway.
- Debugging only: publish `127.0.0.1:5500:3000` (loopback, the one known-free
  port), use it through an SSH tunnel, and remove it afterwards. **Never
  `0.0.0.0:5500` or a bare `5500:3000`** (Docker binds bare ports to all
  interfaces and bypasses ufw).

### Timeline (about 30 minutes)

| Min | Step |
|---|---|
| (before) | Section 1 prerequisites: DNS, cert, nginx, Entra, `.env` values |
| 0-3 | Build the kit on Windows, `scp` it |
| 3-8 | Backups: images, `.env` files, compose, `pg_dump` |
| 8-11 | Extract, frontend `.env`, compose block |
| 11-20 | Build both images (first frontend build is the slow part) |
| 20-25 | Start, watch migrations and ingestion |
| 25-30 | Verification, autoheal cron |

---

## 1. One-time prerequisites (checklist)

Do these before the first dashboard deploy. Later deploys skip this section.

- [ ] **Release blocker check.** The public website calls `/query` with the
      API key only. This must return `200` before you start (section 4.3).
- [ ] **DNS:** an A record `<DASHBOARD_DOMAIN>` -> the same public IP as
      `askdiq.ethub.co`. Check: `dig +short <DASHBOARD_DOMAIN>`.
- [ ] **nginx + certificate:** append the server blocks to the existing
      askdiq conf and issue the certificate (steps 1a-1c below).
- [ ] **Entra redirect URI** (same app registration, same client id): add
      `https://<DASHBOARD_DOMAIN>/api/auth/callback/microsoft-entra-id` to
      **Authentication > Web > Redirect URIs**.
- [ ] **Backend `.env` values** (step 1d).
- [ ] **Frontend `.env` values** ready (section 3.3).

### 1a. Find the existing askdiq nginx conf (on the server)

```bash
ssh WebAdmin@Internal-Dev-Ubuntu
D=<DASHBOARD_DOMAIN>            # e.g. askdiq-admin.ethub.co
TS=$(date +%Y%m%d-%H%M%S)

# Host directories mounted into the nginx container
docker inspect et-platform-nginx-1 --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
# Find the conf that serves askdiq.ethub.co (use the Source dir shown above)
sudo grep -rl 'askdiq.ethub.co' <nginx conf Source dir>
CONF=<path printed above>        # the existing askdiq .conf
sudo cp -a "$CONF" "$CONF.bak.$TS"
# How the existing block does TLS and ACME (copy the same style):
sudo grep -nE 'acme-challenge|root |ssl_certificate|include' "$CONF"
```

### 1b. Append PART 1 (HTTP), issue the certificate (no email)

The ready-to-append file is `deploy/nginx/askdiq-admin.append.conf` (its only
placeholder is `<DASHBOARD_DOMAIN>`). PART 1 goes in first: the PART 2 (443)
block references the certificate, and appending it before the certificate
exists makes `nginx -t` fail, which would block reloads for every app on the
shared proxy. PART 1 uses ACME webroot `/var/www/certbot`; if the existing
askdiq block (grep above) uses another webroot, change that one line.

From Windows (once): `scp C:\Projects\askdiq\deploy\nginx\askdiq-admin.append.conf WebAdmin@Internal-Dev-Ubuntu:~/`
(it is also in every kit under `deploy-kit/deploy/nginx/`).

```bash
sed "s#<DASHBOARD_DOMAIN>#$D#g" ~/askdiq-admin.append.conf > /tmp/askdiq-web.conf
grep -n '<DASHBOARD_DOMAIN>' /tmp/askdiq-web.conf || echo "placeholder replaced"
sed -n '/^# === PART 1/,/^# === PART 2/{/^# === PART 2/d;p}' /tmp/askdiq-web.conf > /tmp/askdiq-web-part1.conf
sed -n '/^# === PART 2/,$p' /tmp/askdiq-web.conf > /tmp/askdiq-web-part2.conf
cat /tmp/askdiq-web-part1.conf                     # review

{ echo; echo "# ==== AskDIQ admin dashboard (askdiq-web), added $TS ===="; cat /tmp/askdiq-web-part1.conf; } | sudo tee -a "$CONF" >/dev/null
docker exec et-platform-nginx-1 nginx -t && docker exec et-platform-nginx-1 nginx -s reload

sudo certbot certonly --webroot -w /var/www/certbot -d "$D" \
  --register-unsafely-without-email --agree-tos --non-interactive
sudo ls /etc/letsencrypt/live/"$D"/                # fullchain.pem privkey.pem
```

If the existing askdiq certificate was issued another way (certbot container,
a different webroot), issue this one the same way; keep
`--register-unsafely-without-email`.

### 1c. Append PART 2 (HTTPS)

Check that the `ssl_certificate` paths match what the nginx container sees
(the `/etc/letsencrypt` mount from 1a) and copy any shared SSL `include` from
the existing askdiq block. The `proxy_redirect` line and the SSE location are
required (section 6). Do **not** add a CSP or the API conf's
`proxy_hide_header` lines: Next.js sets its own security headers.

```bash
cat /tmp/askdiq-web-part2.conf                     # review
sudo tee -a "$CONF" < /tmp/askdiq-web-part2.conf >/dev/null
docker exec et-platform-nginx-1 nginx -t && docker exec et-platform-nginx-1 nginx -s reload
```

If `nginx -t` fails: `sudo cp -a "$CONF.bak.$TS" "$CONF"` and reload, so the
other apps are never left on a broken config. Until `askdiq-web` runs, the
dashboard host answers 502; nothing else is affected.

### 1d. Backend `.env` values (`/opt/askdiq/backend/.env`)

Edit one line at a time with `nano`; never upload a `.env` from a laptop.
Names come from `backend/.env.example`.

```bash
cd /opt/askdiq
cp -a backend/.env ~/askdiq.env.bak.$TS
grep -E '^(ENV|APP_BASE_URL|ENTRA_REDIRECT_URI|OIDC_REDIRECT_URI|SESSION_COOKIE_SECURE|SESSION_COOKIE_NAME|TRUST_PROXY_HEADERS|AUTH_DEV_MOCK|CORS_ALLOWED_ORIGINS|SUPERADMIN_EMAILS|ACCESS_CONTACT_EMAIL)=' backend/.env
grep -cE '^DATABASE_URL=.*@et-platform-postgres-1:5432/askdiq' backend/.env   # must print 1
grep -cE '^(SESSION_SECRET|ENTRA_CLIENT_SECRET|OIDC_CLIENT_SECRET)=.+' backend/.env   # >= 2 (prints counts, not values)
nano backend/.env
```

| Variable | Value |
|---|---|
| `ENV` | `production` |
| `APP_BASE_URL` | `https://<DASHBOARD_DOMAIN>` (exact origin, no trailing slash; CSRF origin check) |
| `SESSION_COOKIE_SECURE` | `true` |
| `SESSION_COOKIE_NAME` | `askdiq_session` (must equal the frontend's) |
| `TRUST_PROXY_HEADERS` | `true` (client IP from `X-Forwarded-For`) |
| `SESSION_SECRET` | long random value, e.g. `openssl rand -hex 32`; changing it signs everyone out |
| `ENTRA_TENANT_ID` | tenant id |
| `ENTRA_CLIENT_ID` | the existing app registration's client id |
| `ENTRA_CLIENT_SECRET` | its client secret |
| `ENTRA_REDIRECT_URI` | `https://<DASHBOARD_DOMAIN>/api/auth/callback/microsoft-entra-id` (byte-identical to Entra) |
| `ENTRA_AUTHORITY_HOST` | leave the default |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`, `OIDC_DISCOVERY_URL` | aliases of the above; if both are set, `ENTRA_*` wins. Use one family. |
| `SUPERADMIN_EMAILS` | comma-separated admin emails (first sign-in gets admin) |
| `ACCESS_CONTACT_EMAIL` | shown on login/suspended pages |
| `AUTH_DEV_MOCK` | `false` (startup refuses `true` outside development) |
| `API_KEY_NAME` | the service key; the frontend `API_KEY` must equal it |
| `CORS_ALLOWED_ORIGINS` | leave as is (dashboard needs no CORS, see section 6) |

Optional tuning, already defaulted in code: `SESSION_TTL_MINUTES`,
`SESSION_IDLE_MINUTES`, `WEB_CONCURRENCY`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`,
`SSE_HEARTBEAT_SECONDS` (15), `SSE_MAX_STREAM_SECONDS` (120),
`TASK_DRAIN_TIMEOUT_SECONDS` (10), `ENABLE_API_DOCS`.

---

## 2. Build the kit and copy it (Windows)

The kit is built from **committed** git content only (no `node_modules`,
`.next`, `.env`, compose or nginx files); the script rejects the kit if any
slip in.

```powershell
cd C:\Projects\askdiq
git status --short                     # commit what you want to ship first
git checkout <release-branch>
powershell -ExecutionPolicy Bypass -File deploy\build_kit.ps1
# must end with: KIT OK   -> C:\Projects\askdiq\askdiq-deploy-<sha>.tgz
```

```powershell
$kit = (Get-ChildItem C:\Projects\askdiq\askdiq-deploy-*.tgz | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
scp $kit WebAdmin@Internal-Dev-Ubuntu:~/
```

---

## 3. On the server

### 3.0 Fast path: `deploy/server/deploy.sh` (recommended)

Do the one-time steps first (first deploy only): 3.3 (frontend `.env`) and
3.4 (paste `deploy/compose/askdiq-web.service.yml` into the server compose).
Then one script runs everything below (3.1, 3.2, 3.5-3.7 and the scripted
checks of section 4) and stops with the exact rollback command on any failure:

```bash
ssh WebAdmin@Internal-Dev-Ubuntu
KIT=$(ls -t ~/askdiq-deploy-*.tgz | head -1); echo "$KIT"
mkdir -p ~/askdiq-tools
tar -xzf "$KIT" -C ~/askdiq-tools --strip-components=3 deploy-kit/deploy/server/deploy.sh deploy-kit/deploy/server/rollback.sh
bash ~/askdiq-tools/deploy.sh --kit "$KIT"
#   flags: --skip-backup (no pg_dump/file copies; image tags + alembic revision are always recorded)
#          --only-backend | --only-frontend
```

What it does, in order (only `askdiq-api` / `askdiq-web` are ever named):

1. Preflight: compose defines the services; backend `DATABASE_URL` host is
   `et-platform-postgres-1:5432/askdiq`; frontend `API_BASE_URL`, `API_KEY`
   and `APP_BASE_URL` are consistent with the backend (values never printed).
2. Backups in `~/askdiq-backups/<ts>`: `alembic current`, image tags
   `askdiq-{api,web}:rollback-<ts>`, compose + both `.env` copies, source
   tarball, `pg_dump` (DB user parsed from `DATABASE_URL`; override with
   `PG_DUMP_USER=postgres`).
3. Extract the kit to `~/askdiq-release/<ts>`; `rsync --delete` only
   `backend/` and `frontend/` (server `.env` kept).
4. `docker compose build`, then `up -d --no-build --force-recreate` API first,
   wait healthy (migrations + ingestion), then web, wait healthy.
5. Checks: `/health` (database + pool), `alembic current` = single head,
   `/query` with the API key + forwarded `ip_address` = 200, web -> API
   reachability, `https://<DASHBOARD_DOMAIN>/login` = 200 and no
   `localhost` redirect.
6. Summary with the rollback command:
   `bash ~/askdiq-tools/rollback.sh --ts <ts>` (section 5).

Then do the browser checks (4.4) and, once, the autoheal cron (3.8).
Sections 3.1-3.7 below are the same steps by hand.

### 3.0b Manual path

Run everything in **one** SSH session: later steps use `$TS`, `$BK`, `$REL`,
`$KIT`, `$PGU`. If the session drops, re-set them (the values are printed).

```bash
ssh WebAdmin@Internal-Dev-Ubuntu
cd /opt/askdiq
KIT=$(ls -t ~/askdiq-deploy-*.tgz | head -1); echo "KIT=$KIT"
TS=$(date +%Y%m%d-%H%M%S); echo "TS=$TS"
BK=~/askdiq-backups/$TS; mkdir -p "$BK"; echo "BK=$BK"
REL=~/askdiq-release/$TS; mkdir -p "$REL"; echo "REL=$REL"

# Current state
docker ps -a --filter name=askdiq --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker inspect askdiq-api --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
#   expect: askdiq_internal et-platform_internal
```

### 3.1 Backups

```bash
cd /opt/askdiq

# Migration revision before the deploy (prod today: d6e7f8a9b0c1)
docker exec askdiq-api python -m alembic current 2>/dev/null | tee "$BK/alembic-before.txt"

# Images tagged for rollback
docker tag askdiq-api:latest askdiq-api:rollback-$TS
docker image inspect askdiq-web:latest >/dev/null 2>&1 && docker tag askdiq-web:latest askdiq-web:rollback-$TS
docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^askdiq-(api|web):rollback-'

# Server-owned files: compose, both .env files, current source
cp -a docker-compose.prod.yml "$BK/docker-compose.prod.yml"
cp -a backend/.env "$BK/backend.env"
[ -f frontend/.env ] && cp -a frontend/.env "$BK/frontend.env"
tar --exclude='.env' -czf "$BK/src-before.tgz" backend $( [ -d frontend ] && echo frontend )
chmod 600 "$BK"/*.env 2>/dev/null; ls -la "$BK"
```

Database dump. Only the **user name** is parsed out of `DATABASE_URL`; the
URL and password are never printed.

```bash
PGU=$(grep -E '^DATABASE_URL=' /opt/askdiq/backend/.env | sed -E 's#^DATABASE_URL=["'"'"']?[a-z+0-9]+://([^:@/]+).*#\1#')
echo "db user: $PGU"
docker exec et-platform-postgres-1 pg_dump -U "$PGU" -d askdiq -Fc > "$BK/askdiq-db.dump"
ls -lh "$BK/askdiq-db.dump"                                                   # non-empty
docker exec -i et-platform-postgres-1 pg_restore --list < "$BK/askdiq-db.dump" | head -5   # readable
```

If `pg_dump` prompts for a password, use the container superuser
(`-U postgres`) instead of typing the app password on the command line.

### 3.2 Extract (only `backend/` and `frontend/`)

```bash
tar -xzf "$KIT" -C "$REL"
cat "$REL/deploy-kit/KIT_VERSION"      # commit + build time
ls "$REL"                              # backend  deploy-kit  frontend

mkdir -p /opt/askdiq/frontend
# Dry run: what --delete would remove (must be only stale code, never data)
for d in backend frontend; do
  rsync -an --delete --itemize-changes --exclude='/.env' --exclude='/.env.*' \
    "$REL/$d/" "/opt/askdiq/$d/" | grep '^\*deleting' || true
done
# Apply
for d in backend frontend; do
  rsync -a --delete --exclude='/.env' --exclude='/.env.*' "$REL/$d/" "/opt/askdiq/$d/"
done
ls -la /opt/askdiq/backend/.env        # still there, unchanged
```

`--delete` matters: a stale migration file would give Alembic two heads.
`deploy-kit/` stays in `$REL`; nothing from it is copied over server files.

### 3.3 Create `/opt/askdiq/frontend/.env` (first deploy only)

```bash
[ -f /opt/askdiq/frontend/.env ] && echo "EXISTS - keep it, only check values" || {
  cp "$REL/deploy-kit/deploy/frontend.env.prod.example" /opt/askdiq/frontend/.env
  chmod 600 /opt/askdiq/frontend/.env
}
nano /opt/askdiq/frontend/.env
```

All values are server-only and read **at run time** (nothing is baked into the
image; there are no `NEXT_PUBLIC_*` variables), so changing one needs only a
container recreate, not a rebuild.

| Variable | Production value | Meaning |
|---|---|---|
| `API_BASE_URL` | `http://askdiq-api:8000` | Backend by container name on `askdiq_internal`. Never `localhost`, never the public URL. |
| `API_KEY` | same as backend `API_KEY_NAME` | Service key the proxy sends as `X-API-Key`. |
| `APP_BASE_URL` | `https://<DASHBOARD_DOMAIN>` | Public origin; must equal the backend's `APP_BASE_URL`. Used for redirects and the sign-in flow. |
| `SESSION_COOKIE_NAME` | `askdiq_session` | Must equal the backend's. |
| `SESSION_COOKIE_SECURE` | `true` | Session cookie sent only over HTTPS. Always `HttpOnly`, `SameSite=Lax`, `Path=/`, host-only. |
| `ACCESS_CONTACT_EMAIL` | same as backend | Shown on the login and suspended pages. |
| `AUTH_DEV_MOCK` | `false` | Dev-only mock IdP forwarding. Never `true` in production. |
| `BACKEND_TIMEOUT_MS` | optional, default `60000` | Proxy wait for backend response headers on reads before 504. |
| `BACKEND_WRITE_TIMEOUT_MS` | optional, default `180000` | Same for writes (chat answers, uploads). SSE is never cut once headers arrive. |
| `BACKEND_RETRIES` | optional, default `2` | Retries of reads/sign-out after connection errors or 502/503/504 (0 disables, max 5). |

The Entra/OIDC settings and `SESSION_SECRET` are **not** here: the backend
runs the whole sign-in exchange and the dashboard only relays it.

### 3.4 Add the `askdiq-web` block to the server compose

Do not replace the file. Check the service and network key names first:

```bash
cd /opt/askdiq
docker compose -f docker-compose.prod.yml config --services          # askdiq-api (and nothing named askdiq-web yet)
grep -n -A8 '^networks:' docker-compose.prod.yml                      # askdiq_internal + et-platform_internal, external: true
nano docker-compose.prod.yml
```

Paste the `askdiq-web:` block from `deploy/compose/askdiq-web.service.yml`
(shown here) under `services:` (2-space indent, same level as `askdiq-api:`).
The file uses the absolute `env_file: /opt/askdiq/frontend/.env` and adds
`stop_grace_period: 15s`; either form works.
If the server's top-level network key for `askdiq_internal` is spelled
differently, use the server's key; if the API service key is not
`askdiq-api`, fix `depends_on`.

```yaml
  askdiq-web:
    build:
      context: ./frontend
      dockerfile: Dockerfile.prod
    image: askdiq-web:latest
    container_name: askdiq-web
    env_file:
      - ./frontend/.env
    restart: unless-stopped
    depends_on:
      askdiq-api:
        condition: service_started
    # NO ports: nginx reaches askdiq-web:3000 on askdiq_internal.
    # Debug only, then remove:  ports: ["127.0.0.1:5500:3000"]
    healthcheck:
      test:
        [
          "CMD",
          "node",
          "-e",
          "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))",
        ]
      interval: 30s
      timeout: 5s
      start_period: 20s
      retries: 3
    networks:
      - askdiq_internal
```

Only if the server file lacks them (do not duplicate keys):

```yaml
networks:
  askdiq_internal:
    external: true
    name: askdiq_internal
  et-platform_internal:
    external: true
    name: et-platform_internal
```

Optional on the existing `askdiq-api` service (image defaults already match):
`environment: { RUN_MIGRATIONS: "true", RUN_INGESTION: "true", RUN_USAGE_BACKFILL: "false" }`,
healthcheck `start_period: 180s`, `stop_grace_period: 30s`.

```bash
docker compose -f docker-compose.prod.yml config -q && echo "compose OK"
docker compose -f docker-compose.prod.yml config --services          # askdiq-api askdiq-web
docker compose -f docker-compose.prod.yml config | grep -n 'published' || echo 'no host ports published'
diff <(docker compose -f "$BK/docker-compose.prod.yml" config 2>/dev/null) <(docker compose -f docker-compose.prod.yml config) | head -60   # only the askdiq-web block
```

### 3.5 Build both images (build first, then run)

```bash
cd /opt/askdiq
docker compose -f docker-compose.prod.yml build askdiq-api askdiq-web
docker images --format '{{.Repository}}:{{.Tag}}\t{{.CreatedSince}}' | grep -E '^askdiq-(api|web):latest'
```

The frontend image is a multi-stage **Next.js standalone** build:
`npm ci` and `next build` run at image build time; the final image holds only
`.next/standalone`, `.next/static` and `public/`, and the container runs
`node server.js`. No compilation happens at run time, so the container starts
in about 1 second. The backend build fails on purpose if the knowledge-base
markdown (`data/real_docs/*.md`) is missing.

The running containers keep serving during the build.

### 3.6 Start

Backend first (it migrates), then the dashboard.

```bash
cd /opt/askdiq
docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-api
```

### 3.7 Watch the entrypoint (migrations, ingestion)

```bash
docker logs -f askdiq-api 2>&1 | grep -E --line-buffered 'prestart|alembic|unchanged|embedded|Total|ERROR|Uvicorn running'
# Ctrl+C once "Uvicorn running" appears
```

Expected:

```
prestart begin migrations=True ingestion=True usage_backfill=False
prestart database reachable attempt=1
prestart alembic upgrade head started
... Running upgrade d6e7f8a9b0c1 -> ...            # each pending revision
prestart alembic upgrade head finished exit=0
prestart ingestion started
<doc>.md: unchanged (embedded 0, kept N, removed 0)  # changed docs are re-embedded
prestart ingestion finished exit=0
prestart done
Uvicorn running on http://0.0.0.0:8000
```

- `alembic upgrade head` runs under a Postgres advisory lock, so two starting
  containers never race. A failure is **fatal**: `prestart FAILED, app not
  started` / `entrypoint: prestart failed (exit 3)`, and nothing serves on a
  half-migrated schema. Go to section 5.
- `prestart ingestion failed ...; API starts anyway` is **non-fatal**: the API
  serves the previous knowledge base (section 6).

Wait for healthy, then start the dashboard:

```bash
for i in $(seq 1 60); do s=$(docker inspect -f '{{.State.Health.Status}}' askdiq-api); echo "askdiq-api: $s"; [ "$s" = healthy ] && break; sleep 5; done
docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-web
docker logs --tail 20 askdiq-web                   # "Ready in ..." within about 1 s
docker ps --filter name=askdiq --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
#   askdiq-api Up (healthy) | askdiq-web Up (healthy), PORTS column shows 3000/tcp only (not published)
```

### 3.8 Install the autoheal watchdog (once)

Docker restarts crashed containers (`restart: unless-stopped`) but not ones
that are alive and hung. `askdiq-autoheal.sh` restarts only containers named
`askdiq-*` that Docker reports `unhealthy`; other apps are never touched.

```bash
mkdir -p /opt/askdiq/ops
tar -xzf "$KIT" -O deploy-kit/deploy/askdiq-autoheal.sh > /opt/askdiq/ops/askdiq-autoheal.sh
chmod 755 /opt/askdiq/ops/askdiq-autoheal.sh
/opt/askdiq/ops/askdiq-autoheal.sh && echo "runs OK (no output = nothing unhealthy)"
crontab -l 2>/dev/null | grep -q askdiq-autoheal || \
  ( crontab -l 2>/dev/null; echo '* * * * * /opt/askdiq/ops/askdiq-autoheal.sh >> /opt/askdiq/ops/autoheal.log 2>&1' ) | crontab -
crontab -l | grep askdiq-autoheal
```

`WebAdmin` must be able to run `docker` without `sudo` (it already does for
the steps above). The log only gets a line when something is restarted.

---

## 4. Verification

### 4.1 Containers and networks

```bash
docker ps --filter name=askdiq --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker inspect askdiq-api --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'   # askdiq_internal et-platform_internal
docker inspect askdiq-web --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'   # askdiq_internal
```

### 4.2 Health (database and pool) and migrations

```bash
curl -fsS https://askdiq.ethub.co/health; echo
#   {"status":"ok","database":"healthy","reranker":...,"pool":{"size":..,"max_overflow":..,"checked_out":0,"idle":..},...}
#   checked_out > 0 on an idle server = connection leak; 503 + "unreachable" = DB problem

docker exec askdiq-api python -m alembic current     # must show (head)
docker exec askdiq-api python -m alembic heads       # exactly one head, same revision
```

### 4.3 Public website path: API key + forwarded IP

Must print `200` (and did before the deploy). A `401`/`403` here means the
public website is down: roll the backend back (section 5).

```bash
curl -s -w '\nHTTP %{http_code}\n' -X POST https://askdiq.ethub.co/query \
  -H 'Content-Type: application/json' -H "X-API-Key: <public site key>" \
  -d '{"query":"What is DeployIQ?","ip_address":"203.0.113.10"}' \
  | grep -oE '"ip_address":"[^"]*"|HTTP [0-9]+'
#   "ip_address":"203.0.113.10"
#   HTTP 200
```

### 4.4 Dashboard

```bash
# Inside the Docker network
docker exec askdiq-web node -e "fetch('http://askdiq-api:8000/health').then(r=>console.log('api',r.status))"   # api 200
docker exec askdiq-web node -e "fetch('http://127.0.0.1:3000/login').then(r=>console.log('web',r.status))"     # web 200

# Through nginx
curl -I https://<DASHBOARD_DOMAIN>/login                       # HTTP/2 200 (or HTTP/1.1 200)
curl -sI https://<DASHBOARD_DOMAIN>/admin | grep -i '^location' # https://<DASHBOARD_DOMAIN>/login?next=%2Fadmin  (NOT localhost:3000)
curl -sI http://<DASHBOARD_DOMAIN>/login | grep -i '^location'  # https://... (HTTP -> HTTPS)
```

In a browser (private window):

1. **SSO login:** `https://<DASHBOARD_DOMAIN>/login` -> "Sign in with
   Microsoft" -> Entra -> back on the dashboard. DevTools > Application >
   Cookies: `askdiq_session` is `HttpOnly`, `Secure`, `SameSite=Lax`.
2. **Chat / forwarded IP:** ask a question in Chat. Admin > Conversations (or
   Governance > Logs) shows your public IP, not `172.x`.
3. **Widget:** the AskDIQ launcher (floating button) opens the drawer and
   answers a question.
4. **Tours:** the guided tour starts (first visit, or replay it from its menu
   entry) and steps through without errors.
5. **SSE live feed:** Admin > Governance > Monitoring. DevTools > Network:
   `/api/proxy/admin/governance/stream` is `200`, type `eventsource`, stays
   open and events arrive. It closes after `SSE_MAX_STREAM_SECONDS` (120 s)
   and the browser reconnects automatically; that is normal. Events arriving
   in one burst at the end means nginx is buffering (section 6).
6. **A write:** change any harmless admin setting and save: no 403.
7. **Logout:** returns to `/login`; `/admin` then redirects to `/login`.

---

## 5. Rollback

Scripted (`deploy/server/rollback.sh`, uses the backup made by `deploy.sh`):

```bash
bash ~/askdiq-tools/rollback.sh --ts <ts>                       # images + source; refuses if migrations ran
bash ~/askdiq-tools/rollback.sh --ts <ts> --db downgrade         # alembic downgrade to the pre-deploy revision, then old image
bash ~/askdiq-tools/rollback.sh --ts <ts> --db restore --yes     # pg_restore the deploy's dump (loses data written since)
#   also: --only-backend | --only-frontend, --keep-source (do not restore /opt/askdiq source)
```

By hand:

Set the variables again if the session dropped:
`TS=<value>; BK=~/askdiq-backups/$TS`.

### Dashboard only

```bash
cd /opt/askdiq
docker compose -f docker-compose.prod.yml stop askdiq-web
# previous image, if there was one:
docker tag askdiq-web:rollback-$TS askdiq-web:latest && \
  docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-web
```

The API and the public website are unaffected; the dashboard host returns 502
while `askdiq-web` is stopped.

### Backend, no new migrations applied (`alembic current` equals `$BK/alembic-before.txt`)

```bash
cd /opt/askdiq
docker tag askdiq-api:rollback-$TS askdiq-api:latest
docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-api
curl -fsS https://askdiq.ethub.co/health
```

### Backend, new migrations were applied

The old image does not know the new revisions, so downgrade with the **new**
image first, then swap images. A non-uvicorn command skips the prestart.

```bash
cd /opt/askdiq
PREV=$(grep -oE '^[0-9a-f]{12}' "$BK/alembic-before.txt" | head -1); echo "PREV=$PREV"   # d6e7f8a9b0c1 for the first dashboard deploy
ls backend/alembic/versions/            # read the downgrade() of each revision newer than $PREV first
docker compose -f docker-compose.prod.yml stop askdiq-api
docker compose -f docker-compose.prod.yml run --rm --no-deps askdiq-api python -m alembic downgrade "$PREV"
docker tag askdiq-api:rollback-$TS askdiq-api:latest
docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-api
docker exec askdiq-api python -m alembic current      # == $PREV
```

A downgrade that drops tables or columns loses data written since the deploy
(sessions, users, new audit fields). If that is unacceptable or the downgrade
fails, restore the dump.

### Last resort: restore the dump

```bash
cd /opt/askdiq
PGU=$(grep -E '^DATABASE_URL=' backend/.env | sed -E 's#^DATABASE_URL=["'"'"']?[a-z+0-9]+://([^:@/]+).*#\1#')
docker compose -f docker-compose.prod.yml stop askdiq-api askdiq-web
docker exec -i et-platform-postgres-1 pg_restore -U "$PGU" -d askdiq --clean --if-exists --no-owner --single-transaction < "$BK/askdiq-db.dump"
docker tag askdiq-api:rollback-$TS askdiq-api:latest
docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-api
curl -fsS https://askdiq.ethub.co/health
```

Everything written after the dump (conversations, audit events) is lost.

### Server files

Only if they were changed:

```bash
cp -a "$BK/docker-compose.prod.yml" /opt/askdiq/docker-compose.prod.yml
cp -a "$BK/backend.env"  /opt/askdiq/backend/.env
[ -f "$BK/frontend.env" ] && cp -a "$BK/frontend.env" /opt/askdiq/frontend/.env
sudo cp -a "$CONF.bak.<TS of section 1>" "$CONF" && docker exec et-platform-nginx-1 nginx -t && docker exec et-platform-nginx-1 nginx -s reload
```

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| **502 from nginx** (either domain) | Container stopped, crash-looping, or not on `askdiq_internal`, so nginx cannot reach `askdiq-api:8000` / `askdiq-web:3000` | `docker ps -a --filter name=askdiq`; `docker logs --tail 100 askdiq-api` (or `askdiq-web`); check networks (4.1). Missing network: `docker network connect askdiq_internal <container>`, then fix the compose block and recreate. |
| **Redirect to `http://localhost:3000/login`** | nginx block lacks `proxy_redirect`; Next middleware builds redirects from its own listen address | Add the `proxy_redirect` line from 1c, `nginx -t`, reload. |
| **AADSTS50011** (redirect URI mismatch) | `ENTRA_REDIRECT_URI` / `OIDC_REDIRECT_URI` differs from the Entra registration (scheme, host, path, trailing slash), or the URI was not added | Register exactly `https://<DASHBOARD_DOMAIN>/api/auth/callback/microsoft-entra-id`; set the same value in the backend `.env`; recreate `askdiq-api`. If both `ENTRA_*` and `OIDC_*` are set, `ENTRA_*` wins. |
| **403 "Cross-site request rejected." on dashboard POST/PATCH/DELETE** | Backend `APP_BASE_URL` is not exactly the browser origin (CSRF Origin check) | `APP_BASE_URL=https://<DASHBOARD_DOMAIN>` in **both** `.env` files (no trailing slash, https); recreate both containers. |
| **Session cookie not set over https / login loops back to /login** | `SESSION_COOKIE_SECURE` mismatch, cookie name mismatch, or the site reached over plain http | `SESSION_COOKIE_SECURE=true` and the same `SESSION_COOKIE_NAME` in both `.env` files; always use `https://`; nginx sends `X-Forwarded-Proto $scheme`. Do not set SameSite to `Strict` (the Entra callback needs `Lax`). |
| **Backend stuck at "Waiting for connections to close"** on restart | Open SSE streams (live feed) keep connections alive; uvicorn waits `--timeout-graceful-shutdown 20` s, then cuts them. Streams also end by themselves after `SSE_MAX_STREAM_SECONDS` (120) | Normal for up to 20 s; compose `stop_grace_period: 30s` lets it finish before SIGKILL. If it hangs longer, `docker restart --time 30 askdiq-api`. Do not raise `SSE_MAX_STREAM_SECONDS` far above the grace period. |
| **`HeadersTimeoutError` / 504 in the dashboard** | Backend down, frozen, or its DB pool exhausted, so no response headers within `BACKEND_TIMEOUT_MS` | `curl -fsS https://askdiq.ethub.co/health`: check `database` and `pool.checked_out` vs `size + max_overflow`. Restart `askdiq-api`; check `docker logs` for pool timeouts; see pool sizing in section 7. |
| **"[Proxy] Failed to reach backend at ... (CODE)" in `askdiq-web` logs** | Current builds stay quiet when the browser cancels a request (navigation, reload). A logged line with a code (`ECONNREFUSED`, `ENOTFOUND`, `UND_ERR_*`) is a real outage | `ENOTFOUND`: wrong `API_BASE_URL` or `askdiq-web` not on `askdiq_internal`. `ECONNREFUSED`: `askdiq-api` down/restarting. Check `docker ps`, `/health`. |
| **Migrations fail: `askdiq-api` won't start** (`prestart FAILED`, exit 3; exit 2 = DB unreachable) | Bad migration, conflicting manual schema change, two heads, or DB unreachable | `docker logs --tail 200 askdiq-api`. Two heads: stale file, re-run 3.2 rsync with `--delete`. Otherwise roll back (section 5) or restore the dump. Never set `RUN_MIGRATIONS=false` to "get it up" on new code. |
| **Ingestion fails** (`prestart ingestion failed ...; API starts anyway`) | Embedding API error, quota, timeout (`INGESTION_TIMEOUT_SECONDS` 900), or KB file missing. Non-fatal: the API serves the previous KB | Fix the cause, then `docker exec askdiq-api python scripts/ingest_real_docs.py` (unchanged docs are skipped by hash). |
| **`next start` errors / "does not work with output: standalone"** | The image is a standalone build; `next start` and `node_modules` are not in it | The container command is `node server.js` (default `CMD`); do not override it in compose. Locally, `npm start` runs `scripts/start-standalone.mjs`. |
| **Container unhealthy / restart loop** | API: still migrating/ingesting (healthcheck start period), DB down, crash on startup config check. Web: `/login` failing (bad env) | `docker inspect --format '{{json .State.Health}}' <name>`; `docker logs`. Autoheal restarts unhealthy containers every minute, so fix the cause, not the symptom; to pause it: `crontab -e` and comment the line. |
| **`database: unreachable`, logs show `localhost`/`127.0.0.1:5432`** | `DATABASE_URL` points at `localhost` (inside a container that is the container itself), usually from an uploaded dev `.env` | Restore the line from `~/askdiq.env.bak.*` or `$BK/backend.env`; it must be `...@et-platform-postgres-1:5432/askdiq`; recreate. |
| **Wrong network** (the 2026-10-16 outage: 502 + DB unreachable) | Compose replaced by a repo file, so the container lost `et-platform_internal` or `askdiq_internal` | Restore `$BK/docker-compose.prod.yml`; hot fix: `docker network connect et-platform_internal askdiq-api` (and/or `askdiq_internal`), then recreate from the correct file. |
| **Port clash** (`bind: address already in use`) | A `ports:` line was added; 3001-3600, 5000-5600 and 5501 are taken | Remove `ports:` from `askdiq-web` (none needed). Debug only: `127.0.0.1:5500:3000`, after `ss -ltnp | grep 5500` shows nothing. |
| **CORS errors** | Not needed: the browser only calls its own origin (`/api/proxy/*`, `/auth/*`); the Next server calls `askdiq-api:8000` server-to-server, where CORS does not apply. The dashboard CSP (`connect-src 'self'`) blocks direct browser calls anyway | Do not add the dashboard to `CORS_ALLOWED_ORIGINS`. A CORS error means browser code is calling `https://askdiq.ethub.co` directly, which is a bug. |
| **Swagger "Try it out" returns "couldn't find relevant information" / `no_sources`** | Swagger pre-fills `"filters": {"additionalProp1": {}}` | Delete `filters` from the request body. The backend ignores unknown filter keys (`retrieval_filters_ignored` in logs). |
| **Live feed never updates, or arrives in bursts** | SSE location missing in nginx, so nginx buffers | Add the `location = /api/proxy/admin/governance/stream` block (1c), reload. |
| **Client IP is `172.x` everywhere** | `TRUST_PROXY_HEADERS` not `true`, or nginx not setting `X-Forwarded-For` | Set it in the backend `.env`; recreate `askdiq-api`. |

---

## 7. Day-2 operations

### Update procedure (any later release)

Section 2, then `bash ~/askdiq-tools/deploy.sh --kit "$KIT"` (3.0; re-extract
the scripts from the new kit first), then 4.4. Skip section 1, 3.3, 3.4 and
3.8 unless the release notes say a new env var or compose change is needed.
Frontend-only release: `deploy.sh --only-frontend` (no DB dump).
Backend-only: `deploy.sh --only-backend`. An `.env`-only change: `up -d --no-build --force-recreate <service>`
(no rebuild; frontend values are read at run time).

### Logs

```bash
docker logs -f --since 10m askdiq-api
docker logs -f --since 10m askdiq-web
docker logs --since 1h askdiq-api 2>&1 | grep -E 'ERROR|prestart|rate_limit_exceeded|production_startup_warning'
cat /opt/askdiq/ops/autoheal.log
```

### Restart

```bash
cd /opt/askdiq
docker compose -f docker-compose.prod.yml restart askdiq-web                  # dashboard only
docker compose -f docker-compose.prod.yml restart askdiq-api                  # re-runs prestart (idempotent)
docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate askdiq-api   # after an .env change
```

### Scaling note

- API workers: `WEB_CONCURRENCY` in the backend `.env` (default 1). Rate
  limits, OIDC state, the job queue and event fan-out are shared through
  Postgres, so more workers are safe.
- Pool budget (DB is shared with other et-platform apps):
  `workers x (DB_POOL_SIZE + DB_MAX_OVERFLOW) <= max_connections - 20`; check
  `docker exec et-platform-postgres-1 psql -U "$PGU" -d askdiq -c 'SHOW max_connections;'`.
- Each worker loads its own reranker model; budget RAM per worker.
- Jobs run in-process by default. A dedicated worker exists
  (`askdiq-worker`, compose profile `worker` in the example file) with
  `JOBS_WORKER_ENABLED=false` on the API.
- `askdiq-web` is stateless; one container is enough.

### Housekeeping

- Keep `$BK`, the dump and rollback images for a week, then
  `docker image rm askdiq-api:rollback-<ts> askdiq-web:rollback-<ts>` and
  `rm -rf ~/askdiq-release/<ts> ~/askdiq-backups/<ts>`.
- Certificate renewal: the existing certbot renewal covers the new cert; after
  renewal reload nginx (`docker exec et-platform-nginx-1 nginx -s reload`).

### Where the docs live (repo)

| File | What |
|---|---|
| `deploy/DEPLOYMENT_GUIDE.md` | This guide |
| `deploy/RUNBOOK.md` | Detailed runbook (routing rationale, edge cases) |
| `deploy/server/deploy.sh` | Server-side deploy: backups, extract, build, start, wait, verify, summary |
| `deploy/server/rollback.sh` | Server-side rollback (images, source, `--db downgrade` / `--db restore`) |
| `deploy/compose/askdiq-web.service.yml` | Exact `askdiq-web` block to paste into the server compose |
| `deploy/nginx/askdiq-admin.append.conf` | Ready-to-append nginx blocks (PART 1 HTTP, PART 2 HTTPS) |
| `deploy/frontend.env.prod.example` | Frontend `.env` template |
| `frontend/Dockerfile.prod` | Multi-stage standalone build: `next build` at build time, `node server.js` at run time, non-root, HEALTHCHECK `/login` |
| `backend/Dockerfile.prod`, `backend/docker-entrypoint.sh`, `backend/scripts/prestart.py` | API image and start-up (migrations under lock, ingestion) |
| `deploy/docker-compose.prod.example.yml` | Reference compose (never copy over the server file) |
| `deploy/nginx/askdiq-admin.conf.example` | Annotated reference nginx block |
| `deploy/build_kit.ps1`, `deploy/askdiq-autoheal.sh` | Kit builder, watchdog |
| `backend/.env.example` | Every backend setting |
| `docs/technical/08-deployment-operations.md` | Operations, incident history, lessons learned |
