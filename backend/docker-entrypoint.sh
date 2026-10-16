#!/bin/sh
# askdiq-api container entrypoint (backend/Dockerfile.prod).
#
# For the API (a command that runs uvicorn) it first runs scripts/prestart.py:
# wait for Postgres, `alembic upgrade head` under an advisory lock (fatal on
# error: the app is NOT started), knowledge-base ingestion (non-fatal) and the
# optional usage backfill. Toggles: RUN_MIGRATIONS, RUN_INGESTION,
# RUN_USAGE_BACKFILL (see scripts/prestart.py).
#
# Any other command (the job worker `python -m app.jobs.worker`, `alembic ...`,
# a shell) is exec'd directly with no pre-start, so the worker never migrates.
set -eu

case "$*" in
  *uvicorn*)
    python /app/scripts/prestart.py || {
      code=$?
      echo "entrypoint: prestart failed (exit ${code}); not starting the API" >&2
      exit "${code}"
    }
    ;;
esac

exec "$@"
