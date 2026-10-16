#!/usr/bin/env bash
# Roll AskDIQ back to the state recorded by deploy.sh.
#
# Usage:
#   bash rollback.sh [--ts <backup ts>] [--only-backend | --only-frontend]
#                    [--db none|downgrade|restore] [--keep-source] [--yes]
#
#   --ts            backup id under ~/askdiq-backups (default: the newest)
#   --db none       (default) images only; refuses if migrations were applied
#   --db downgrade  `alembic downgrade <revision before deploy>` with the NEW
#                   image, then swap images (data in dropped columns is lost)
#   --db restore    pg_restore the deploy's dump (needs --yes; everything
#                   written since the dump is lost)
#   --keep-source   do not restore /opt/askdiq/{backend,frontend} source from
#                   src-before.tgz (default: restore it, .env files untouched)
#
# Only askdiq-api / askdiq-web are touched; compose, nginx and .env files are
# never written.
set -euo pipefail

ROOT=${ASKDIQ_ROOT:-/opt/askdiq}
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"
PG_CONTAINER=${PG_CONTAINER:-et-platform-postgres-1}
PUBLIC_API_URL=${PUBLIC_API_URL:-https://askdiq.ethub.co}

TS=""
DO_API=true
DO_WEB=true
DB_MODE=none
WEB_WAS_RUNNING=false
KEEP_SOURCE=false
YES=false

usage() { sed -n '2,20p' "$0"; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --ts) TS=${2:?--ts needs a value}; shift 2 ;;
    --only-backend) DO_WEB=false; shift ;;
    --only-frontend) DO_API=false; shift ;;
    --db) DB_MODE=${2:?--db needs none|downgrade|restore}; shift 2 ;;
    --keep-source) KEEP_SOURCE=true; shift ;;
    --yes) YES=true; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 2 ;;
  esac
done
case "$DB_MODE" in none|downgrade|restore) ;; *) echo "bad --db $DB_MODE" >&2; exit 2 ;; esac
if ! $DO_API && ! $DO_WEB; then echo "--only-backend and --only-frontend are exclusive" >&2; exit 2; fi

log() { printf '\n==> [%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
ok() { printf '    OK  %s\n' "$*"; }
die() { printf '    FAIL %s\n' "$*" >&2; exit 1; }
dc() { docker compose -f "$COMPOSE_FILE" "$@"; }
trap 'echo "!!! rollback failed (line $LINENO). Containers: docker ps -a --filter name=^askdiq-" >&2' ERR

if [ -z "$TS" ]; then
  TS=$(ls -1 "$HOME/askdiq-backups" 2>/dev/null | sort | tail -1 || true)
fi
BK="$HOME/askdiq-backups/$TS"
[ -n "$TS" ] && [ -d "$BK" ] || die "backup dir not found: $BK"
log "Rolling back to backup $TS"

wait_healthy() {
  local name=$1 limit=$2 waited=0 state
  while :; do
    state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || echo missing)
    [ "$state" = healthy ] && { ok "$name healthy"; return 0; }
    [ "$waited" -ge "$limit" ] && { docker logs --tail 60 "$name" >&2 || true; die "$name not healthy after ${limit}s"; }
    sleep 5; waited=$((waited + 5))
  done
}

restore_source() {
  local d=$1 tmp
  $KEEP_SOURCE && return 0
  [ -f "$BK/src-before.tgz" ] || { echo "    (no src-before.tgz: source not restored)"; return 0; }
  tmp=$(mktemp -d)
  if tar -xzf "$BK/src-before.tgz" -C "$tmp" "$d" 2>/dev/null; then
    rsync -a --delete --exclude='/.env' --exclude='/.env.*' "$tmp/$d/" "$ROOT/$d/"
    ok "$d/ source restored (.env kept)"
  else
    echo "    ($d not in src-before.tgz: source not restored)"
  fi
  rm -rf "$tmp"
}

# --------------------------------------------------------------- dashboard
if $DO_WEB; then
  log "askdiq-web"
  restore_source frontend
  if docker image inspect "askdiq-web:rollback-$TS" >/dev/null 2>&1; then
    docker tag "askdiq-web:rollback-$TS" askdiq-web:latest
    dc up -d --no-build --force-recreate --no-deps askdiq-web
    wait_healthy askdiq-web 120
  else
    echo "    no askdiq-web:rollback-$TS (first dashboard deploy): stopping askdiq-web"
    dc stop askdiq-web
  fi
fi

# ----------------------------------------------------------------- backend
if $DO_API; then
  log "askdiq-api"
  docker image inspect "askdiq-api:rollback-$TS" >/dev/null 2>&1 || die "image askdiq-api:rollback-$TS not found"
  PREV=$(grep -oE '^[0-9a-f]{12}' "$BK/alembic-before.txt" 2>/dev/null | head -1 || true)
  CUR=$(docker exec askdiq-api python -m alembic current 2>/dev/null | grep -oE '^[0-9a-f]{12}' | head -1 || true)
  echo "    alembic before deploy: ${PREV:-unknown}   now: ${CUR:-unknown (container down?)}"

  case "$DB_MODE" in
    none)
      if [ -n "$PREV" ] && [ -n "$CUR" ] && [ "$PREV" != "$CUR" ]; then
        die "migrations were applied ($PREV -> $CUR). Re-run with --db downgrade or --db restore --yes"
      fi
      ;;
    downgrade)
      [ -n "$PREV" ] || die "no alembic-before.txt revision in $BK"
      echo "    downgrading with the CURRENT (new) image to $PREV"
      dc stop askdiq-api
      dc run --rm --no-deps askdiq-api python -m alembic downgrade "$PREV"
      ok "downgraded to $PREV"
      ;;
    restore)
      $YES || die "--db restore discards all data written since the dump; add --yes"
      [ -s "$BK/askdiq-db.dump" ] || die "no dump in $BK (deploy ran with --skip-backup?)"
      PGU=$(sed -n 's/^pg_user=//p' "$BK/manifest" 2>/dev/null | tail -1)
      [ -n "$PGU" ] || PGU=${PG_DUMP_USER:-$(sed -nE 's#^DATABASE_URL=["'"'"']?[a-z+0-9]+://([^:@/]+).*#\1#p' "$ROOT/backend/.env" | tail -1)}
      docker ps --format '{{.Names}}' | grep -qx askdiq-web && WEB_WAS_RUNNING=true
      dc stop askdiq-api
      if $WEB_WAS_RUNNING; then dc stop askdiq-web; fi
      docker exec -i "$PG_CONTAINER" pg_restore -U "$PGU" -d askdiq --clean --if-exists --no-owner --single-transaction < "$BK/askdiq-db.dump"
      ok "database restored from $BK/askdiq-db.dump"
      ;;
  esac

  restore_source backend
  docker tag "askdiq-api:rollback-$TS" askdiq-api:latest
  # The old image's prestart runs `alembic upgrade head` = the old head: a no-op now.
  dc up -d --no-build --force-recreate --no-deps askdiq-api
  wait_healthy askdiq-api 420
  if [ "$DB_MODE" = restore ] && $WEB_WAS_RUNNING; then
    dc up -d --no-build --no-deps askdiq-web
  fi
  curl -fsS --max-time 15 "$PUBLIC_API_URL/health" | grep -q '"database":"healthy"' && ok "public /health: database healthy"
  echo "    alembic now: $(docker exec askdiq-api python -m alembic current 2>/dev/null | head -1)"
fi

trap - ERR
log "Rollback done"
docker ps --filter name=^askdiq- --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
