#!/usr/bin/env bash
# AskDIQ server-side deploy (askdiq-api + askdiq-web) on Internal-Dev-Ubuntu.
#
# Usage (as WebAdmin, from anywhere):
#   bash deploy.sh [--kit ~/askdiq-deploy-<sha>.tgz] [--skip-backup]
#                  [--only-backend | --only-frontend]
#
# Steps: preflight -> backups -> extract backend/ + frontend/ from the kit ->
# build -> up -d --force-recreate -> wait for healthy -> verification -> summary.
# Any failure stops the script and prints the rollback command.
#
# Safety:
#   * Only the services askdiq-api / askdiq-web are named in every compose
#     command; no other container, network or port is touched.
#   * Never writes docker-compose.prod.yml, nginx confs or any .env file.
#   * Secrets are never printed (only the DB user name is parsed from
#     DATABASE_URL; the API key is passed to curl, not echoed).
#   * --skip-backup skips the pg_dump and file copies only. Image rollback
#     tags and the alembic revision are always recorded (rollback needs them).
#
# Environment overrides: ASKDIQ_ROOT (/opt/askdiq), PUBLIC_API_URL
# (https://askdiq.ethub.co), PG_CONTAINER (et-platform-postgres-1),
# PG_DUMP_USER (default: user from DATABASE_URL), API_HEALTH_TIMEOUT (420),
# WEB_HEALTH_TIMEOUT (120).
set -euo pipefail

ROOT=${ASKDIQ_ROOT:-/opt/askdiq}
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"
PUBLIC_API_URL=${PUBLIC_API_URL:-https://askdiq.ethub.co}
PG_CONTAINER=${PG_CONTAINER:-et-platform-postgres-1}
API_HEALTH_TIMEOUT=${API_HEALTH_TIMEOUT:-420}
WEB_HEALTH_TIMEOUT=${WEB_HEALTH_TIMEOUT:-120}

KIT=""
SKIP_BACKUP=false
DO_API=true
DO_WEB=true

usage() { sed -n '2,25p' "$0"; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --kit) KIT=${2:?--kit needs a path}; shift 2 ;;
    --skip-backup) SKIP_BACKUP=true; shift ;;
    --only-backend) DO_WEB=false; shift ;;
    --only-frontend) DO_API=false; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 2 ;;
  esac
done
if ! $DO_API && ! $DO_WEB; then echo "--only-backend and --only-frontend are exclusive" >&2; exit 2; fi

TS=$(date +%Y%m%d-%H%M%S)
BK="$HOME/askdiq-backups/$TS"
REL="$HOME/askdiq-release/$TS"
STEP="init"
SERVICES=()
$DO_API && SERVICES+=(askdiq-api)
$DO_WEB && SERVICES+=(askdiq-web)
SCOPE_FLAG=""
$DO_API && ! $DO_WEB && SCOPE_FLAG=" --only-backend"
$DO_WEB && ! $DO_API && SCOPE_FLAG=" --only-frontend"

log() { printf '\n==> [%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
ok() { printf '    OK  %s\n' "$*"; }
warn() { printf '    WARN %s\n' "$*" >&2; }
die() { printf '    FAIL %s\n' "$*" >&2; exit 1; }
dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

on_error() {
  local code=$?
  trap - ERR
  {
    echo
    echo "!!! DEPLOY FAILED at step: $STEP (exit $code)"
    echo "    Backups: $BK"
    echo "    Logs:    docker logs --tail 200 askdiq-api ; docker logs --tail 100 askdiq-web"
    case "$STEP" in
      init|preflight|backup|extract|build)
        echo "    Running containers were NOT changed. Fix the cause and re-run deploy.sh." ;;
      *)
        echo "    Roll back with:"
        echo "      bash $(dirname "$(readlink -f "$0")")/rollback.sh --ts $TS$SCOPE_FLAG"
        echo "    (add --db downgrade or --db restore --yes if migrations were applied; see docs/deployment/deployment-guide.md section 5)" ;;
    esac
  } >&2
  exit "$code"
}
trap on_error ERR

# Value of KEY in an env file, without echoing it; strips surrounding quotes.
env_get() {
  local key=$1 file=$2
  sed -n "s/^${key}=//p" "$file" | tail -1 | sed -E "s/^[\"']//; s/[\"']\$//; s/\r\$//"
}

health_of() {
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo missing
}

wait_healthy() {
  local name=$1 limit=$2 waited=0 state
  while :; do
    state=$(health_of "$name")
    [ "$state" = healthy ] && { ok "$name healthy after ${waited}s"; return 0; }
    if [ "$waited" -ge "$limit" ]; then
      docker logs --tail 60 "$name" >&2 || true
      die "$name not healthy after ${limit}s (state: $state)"
    fi
    printf '    ... %s: %s (%ss)\n' "$name" "$state" "$waited"
    sleep 5; waited=$((waited + 5))
  done
}

# ---------------------------------------------------------------- preflight
STEP=preflight
log "Preflight (services: ${SERVICES[*]})"
command -v docker >/dev/null || die "docker not found"
docker compose version >/dev/null || die "docker compose plugin not found"
command -v rsync >/dev/null || die "rsync not found (sudo apt-get install -y rsync)"
[ -f "$COMPOSE_FILE" ] || die "missing $COMPOSE_FILE"
if [ -z "$KIT" ]; then
  KIT=$(ls -t "$HOME"/askdiq-deploy-*.tgz 2>/dev/null | head -1 || true)
fi
[ -n "$KIT" ] && [ -f "$KIT" ] || die "kit not found (pass --kit <path>)"
ok "kit $KIT"

defined=$(dc config --services)
for s in "${SERVICES[@]}"; do
  grep -qx "$s" <<<"$defined" || die "service $s is not in $COMPOSE_FILE (paste infra/compose/askdiq-web.service.yml first)"
done
ok "compose defines ${SERVICES[*]}"
if dc config | grep -qE '^\s+published:'; then
  dc config | grep -nE 'published|host_ip' >&2
  warn "compose publishes host ports (check none belongs to askdiq-web)"
fi

[ -f "$ROOT/backend/.env" ] || die "missing $ROOT/backend/.env"
grep -qE '^DATABASE_URL=.*@et-platform-postgres-1:5432/askdiq' "$ROOT/backend/.env" \
  || die "backend DATABASE_URL does not point at et-platform-postgres-1:5432/askdiq"
ok "backend .env present, DATABASE_URL host OK"
if $DO_WEB; then
  [ -f "$ROOT/frontend/.env" ] || die "missing $ROOT/frontend/.env (cp deploy-kit/infra/frontend.env.prod.example and fill it)"
  [ "$(env_get API_BASE_URL "$ROOT/frontend/.env")" = "http://askdiq-api:8000" ] \
    || die "frontend API_BASE_URL must be http://askdiq-api:8000"
  [ -n "$(env_get API_KEY "$ROOT/frontend/.env")" ] || die "frontend API_KEY is empty"
  [ "$(env_get API_KEY "$ROOT/frontend/.env")" = "$(env_get API_KEY_NAME "$ROOT/backend/.env")" ] \
    || die "frontend API_KEY differs from backend API_KEY_NAME"
  [ "$(env_get APP_BASE_URL "$ROOT/frontend/.env")" = "$(env_get APP_BASE_URL "$ROOT/backend/.env")" ] \
    || die "APP_BASE_URL differs between frontend and backend .env"
  ok "frontend .env present and consistent with backend"
fi

# ------------------------------------------------------------------- backup
STEP=backup
log "Backups -> $BK"
mkdir -p "$BK"; chmod 700 "$BK"
{
  echo "ts=$TS"; echo "kit=$KIT"; echo "services=${SERVICES[*]}"
} > "$BK/manifest"

if docker ps --format '{{.Names}}' | grep -qx askdiq-api; then
  docker exec askdiq-api python -m alembic current 2>/dev/null > "$BK/alembic-before.txt" || true
  ok "alembic before: $(grep -oE '^[0-9a-f]{12}' "$BK/alembic-before.txt" | head -1 || echo unknown)"
fi
for img in "${SERVICES[@]}"; do
  if docker image inspect "$img:latest" >/dev/null 2>&1; then
    docker tag "$img:latest" "$img:rollback-$TS"
    echo "image_$img=$img:rollback-$TS" >> "$BK/manifest"
    ok "tagged $img:rollback-$TS"
  else
    warn "no $img:latest yet (first deploy): nothing to tag"
  fi
done

if $SKIP_BACKUP; then
  warn "--skip-backup: no pg_dump, no file copies"
else
  cp -a "$COMPOSE_FILE" "$BK/docker-compose.prod.yml"
  cp -a "$ROOT/backend/.env" "$BK/backend.env"
  [ -f "$ROOT/frontend/.env" ] && cp -a "$ROOT/frontend/.env" "$BK/frontend.env"
  chmod 600 "$BK"/*.env
  dirs=(); for d in backend frontend; do [ -d "$ROOT/$d" ] && dirs+=("$d"); done
  tar --exclude='.env' --exclude='node_modules' --exclude='.next' -czf "$BK/src-before.tgz" -C "$ROOT" "${dirs[@]}"
  ok "compose, .env files and source saved"
  if $DO_API; then
    PGU=${PG_DUMP_USER:-$(sed -nE 's#^DATABASE_URL=["'"'"']?[a-z+0-9]+://([^:@/]+).*#\1#p' "$ROOT/backend/.env" | tail -1)}
    [ -n "$PGU" ] || die "could not parse the DB user from DATABASE_URL (set PG_DUMP_USER)"
    echo "    pg_dump as user: $PGU"
    docker exec "$PG_CONTAINER" pg_dump -U "$PGU" -d askdiq -Fc > "$BK/askdiq-db.dump"
    [ -s "$BK/askdiq-db.dump" ] || die "empty dump"
    docker exec -i "$PG_CONTAINER" pg_restore --list < "$BK/askdiq-db.dump" > /dev/null
    echo "pg_user=$PGU" >> "$BK/manifest"
    ok "database dump $(du -h "$BK/askdiq-db.dump" | cut -f1)"
  fi
fi

# ------------------------------------------------------------------ extract
STEP=extract
log "Extract kit -> $REL"
mkdir -p "$REL"
tar -xzf "$KIT" -C "$REL"
[ -f "$REL/deploy-kit/KIT_VERSION" ] && sed 's/^/    /' "$REL/deploy-kit/KIT_VERSION"
for d in backend frontend; do
  [ -d "$REL/$d" ] || die "kit has no $d/"
done
sync_dir() {
  local d=$1
  mkdir -p "$ROOT/$d"
  rsync -a --delete --exclude='/.env' --exclude='/.env.*' "$REL/$d/" "$ROOT/$d/"
  ok "$d/ synced (server .env kept)"
}
$DO_API && sync_dir backend
$DO_WEB && sync_dir frontend
cp "$REL/deploy-kit/KIT_VERSION" "$BK/KIT_VERSION" 2>/dev/null || true

# -------------------------------------------------------------------- build
STEP=build
log "Build ${SERVICES[*]} (running containers keep serving)"
dc build "${SERVICES[@]}"
ok "images built"

# -------------------------------------------------------------------- start
STEP=start
if $DO_API; then
  log "Start askdiq-api (prestart: alembic upgrade head under lock, then ingestion)"
  dc up -d --no-build --force-recreate --no-deps askdiq-api
  STEP=wait-api
  wait_healthy askdiq-api "$API_HEALTH_TIMEOUT"
  echo "    prestart log:"
  docker logs askdiq-api 2>&1 | grep -E 'prestart|Running upgrade' | tail -20 | sed 's/^/      /'
  if docker logs askdiq-api 2>&1 | grep -q 'prestart ingestion failed'; then
    warn "ingestion failed (non-fatal): docker exec askdiq-api python scripts/ingest_real_docs.py"
  fi
fi
if $DO_WEB; then
  STEP=start
  log "Start askdiq-web (node server.js, standalone build)"
  dc up -d --no-build --force-recreate --no-deps askdiq-web
  STEP=wait-web
  wait_healthy askdiq-web "$WEB_HEALTH_TIMEOUT"
fi

# ------------------------------------------------------------------- verify
STEP=verify
log "Verification"
RESULTS=()
if $DO_API; then
  docker exec askdiq-api curl -fsS http://localhost:8000/health | grep -q '"database":"healthy"' \
    || die "/health inside askdiq-api is not healthy"
  ok "/health (container): database healthy"
  body=$(curl -fsS --max-time 15 "$PUBLIC_API_URL/health") || die "$PUBLIC_API_URL/health failed"
  grep -q '"database":"healthy"' <<<"$body" || die "public /health: database not healthy"
  ok "public /health: $(grep -oE '"pool":\{[^}]*\}' <<<"$body" || echo 'pool n/a')"
  RESULTS+=("health OK")

  cur=$(docker exec askdiq-api python -m alembic current 2>/dev/null)
  heads=$(docker exec askdiq-api python -m alembic heads 2>/dev/null | grep -cE '^[0-9a-f]{12}' || true)
  [ "$heads" = 1 ] || die "alembic heads = $heads (expected 1; stale migration file?)"
  grep -q '(head)' <<<"$cur" || die "alembic current is not head: $cur"
  ok "alembic current = head ($(grep -oE '^[0-9a-f]{12}' <<<"$cur"))"
  RESULTS+=("alembic at head")

  key=$(env_get API_KEY_NAME "$ROOT/backend/.env")
  if [ -n "$key" ]; then
    resp=$(curl -s --max-time 120 -w '\n%{http_code}' -X POST "$PUBLIC_API_URL/query" \
      -H 'Content-Type: application/json' -H "X-API-Key: $key" \
      -d '{"query":"What is DeployIQ?","ip_address":"203.0.113.10"}')
    code=$(tail -1 <<<"$resp")
    [ "$code" = 200 ] || die "/query with API key returned $code (public website path broken: roll back)"
    grep -q '"ip_address":"203.0.113.10"' <<<"$resp" || warn "/query did not echo the forwarded ip_address"
    ok "/query with API key + forwarded ip_address: 200"
    RESULTS+=("/query API-key path 200")
  else
    warn "API_KEY_NAME empty: /query check skipped"
  fi
fi
if $DO_WEB; then
  docker exec askdiq-web node -e "fetch('http://askdiq-api:8000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))" \
    || die "askdiq-web cannot reach http://askdiq-api:8000 (network askdiq_internal?)"
  ok "askdiq-web -> askdiq-api:8000 reachable"
  dash=$(env_get APP_BASE_URL "$ROOT/frontend/.env")
  if [ -n "$dash" ]; then
    if code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$dash/login") && [ "$code" = 200 ]; then
      ok "$dash/login: 200"
      loc=$(curl -sI --max-time 15 "$dash/admin" | tr -d '\r' | sed -n 's/^[Ll]ocation: //p')
      case "$loc" in
        *localhost*|*127.0.0.1*|*askdiq-web*) warn "redirect leaks internal origin ($loc): add proxy_redirect to nginx" ;;
        *) ok "/admin redirect: ${loc:-none}" ;;
      esac
      RESULTS+=("dashboard /login 200")
    else
      warn "$dash/login returned ${code:-error} (nginx block / DNS / cert not live yet?)"
    fi
  fi
fi

# ------------------------------------------------------------------ summary
STEP=summary
trap - ERR
log "Summary"
docker ps --filter name=^askdiq- --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
echo
echo "  Deployed:  ${SERVICES[*]}  (ts $TS)"
[ -f "$BK/KIT_VERSION" ] && echo "  Kit:       $(head -1 "$BK/KIT_VERSION")"
echo "  Checks:    ${RESULTS[*]:-none}"
echo "  Backups:   $BK"
echo "  Rollback:  bash $(dirname "$(readlink -f "$0")")/rollback.sh --ts $TS$SCOPE_FLAG"
echo "  Manual:    SSO login, logout, widget, tours, live feed (docs/deployment/deployment-guide.md section 4.4)"
