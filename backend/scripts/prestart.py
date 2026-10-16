"""Container pre-start: wait for Postgres, migrate, ingest, then hand over.

Run by docker-entrypoint.sh before uvicorn (never by the job worker):

1. Wait for the database (bounded retries).
2. ``alembic upgrade head`` under a Postgres advisory lock, so replicas
   starting together never race. Any failure exits non-zero and the
   entrypoint does NOT start the app.
3. Knowledge-base ingestion (``scripts/ingest_real_docs.py``) under its own
   advisory lock, taken with try-lock: if another replica is already
   ingesting, this one skips. Unchanged documents are skipped by content
   hash. Failure or timeout is logged and the API still starts.
4. Optional one-off ``scripts/backfill_usage_accounting.py --apply``
   (non-fatal), only when RUN_USAGE_BACKFILL=true.

Toggles (environment): RUN_MIGRATIONS (default true), RUN_INGESTION (default
true), RUN_USAGE_BACKFILL (default false), DB_WAIT_RETRIES (30),
DB_WAIT_INTERVAL_SECONDS (2), DB_CONNECT_TIMEOUT_SECONDS (20),
MIGRATION_LOCK_TIMEOUT_SECONDS (600),
INGESTION_TIMEOUT_SECONDS (900), BACKFILL_TIMEOUT_SECONDS (1800).

The database URL is read from DATABASE_URL and never logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import asyncpg

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Arbitrary, fixed advisory-lock keys (64-bit) private to this project.
MIGRATION_LOCK_KEY = 0x41534B4449510001  # "ASKDIQ" + 1
INGESTION_LOCK_KEY = 0x41534B4449510002  # "ASKDIQ" + 2
BACKFILL_LOCK_KEY = 0x41534B4449510003  # "ASKDIQ" + 3

EXIT_DB_UNREACHABLE = 2
EXIT_MIGRATION_FAILED = 3

logger = logging.getLogger("askdiq.prestart")


class PrestartError(RuntimeError):
    """A fatal pre-start failure: the app must not start."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        logger.warning("prestart invalid %s, using default %s", name, default)
        return default


def asyncpg_dsn() -> str:
    """DATABASE_URL in the plain ``postgresql://`` form asyncpg accepts."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise PrestartError("DATABASE_URL is not set", EXIT_DB_UNREACHABLE)
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


async def wait_for_db(dsn: str) -> asyncpg.Connection:
    retries = int(env_number("DB_WAIT_RETRIES", 30))
    interval = env_number("DB_WAIT_INTERVAL_SECONDS", 2)
    connect_timeout = env_number("DB_CONNECT_TIMEOUT_SECONDS", 20)
    for attempt in range(1, retries + 1):
        try:
            conn = await asyncpg.connect(dsn, timeout=connect_timeout)
        except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
            logger.info(
                "prestart waiting for database attempt=%d/%d error=%s",
                attempt,
                retries,
                type(exc).__name__,
            )
            await asyncio.sleep(interval)
        else:
            logger.info("prestart database reachable attempt=%d", attempt)
            return conn
    raise PrestartError(
        f"database unreachable after {retries} attempts", EXIT_DB_UNREACHABLE
    )


async def acquire_lock(conn: asyncpg.Connection, key: int, wait_seconds: float) -> bool:
    """Session advisory lock, polled until ``wait_seconds`` seconds pass."""
    deadline = time.monotonic() + wait_seconds
    waited = False
    while True:
        if await conn.fetchval("SELECT pg_try_advisory_lock($1)", key):
            return True
        if time.monotonic() >= deadline:
            return False
        if not waited:
            logger.info("prestart lock busy, waiting key=%#x", key)
            waited = True
        await asyncio.sleep(2)


async def release_lock(conn: asyncpg.Connection, key: int) -> None:
    await conn.fetchval("SELECT pg_advisory_unlock($1)", key)


async def run_step(name: str, args: list[str], limit_seconds: float) -> int:
    """Run a backend command (stdout/stderr pass through to the container log)."""
    logger.info("prestart %s started", name)
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, *args, cwd=str(BACKEND_DIR)
    )
    try:
        code = await asyncio.wait_for(proc.wait(), timeout=limit_seconds)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        logger.error("prestart %s timed out after %.0fs", name, limit_seconds)
        return -1
    logger.info(
        "prestart %s finished exit=%d seconds=%.1f",
        name,
        code,
        time.monotonic() - started,
    )
    return code


async def migrate(conn: asyncpg.Connection) -> None:
    timeout = env_number("MIGRATION_LOCK_TIMEOUT_SECONDS", 600)
    if not await acquire_lock(conn, MIGRATION_LOCK_KEY, timeout):
        raise PrestartError(
            f"migration lock not acquired within {timeout:.0f}s",
            EXIT_MIGRATION_FAILED,
        )
    try:
        code = await run_step(
            "alembic upgrade head", ["-m", "alembic", "upgrade", "head"], timeout
        )
        if code != 0:
            raise PrestartError(
                f"alembic upgrade head failed (exit {code})", EXIT_MIGRATION_FAILED
            )
        await run_step("alembic current", ["-m", "alembic", "current"], timeout)
    finally:
        await release_lock(conn, MIGRATION_LOCK_KEY)


async def ingest(conn: asyncpg.Connection) -> None:
    """Non-fatal: logs and returns on any failure."""
    if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", INGESTION_LOCK_KEY):
        logger.info("prestart ingestion skipped: another replica holds the lock")
        return
    try:
        code = await run_step(
            "ingestion",
            [str(BACKEND_DIR / "scripts" / "ingest_real_docs.py")],
            env_number("INGESTION_TIMEOUT_SECONDS", 900),
        )
        if code != 0:
            logger.error("prestart ingestion failed exit=%d; API starts anyway", code)
    finally:
        await release_lock(conn, INGESTION_LOCK_KEY)


async def backfill(conn: asyncpg.Connection) -> None:
    """Non-fatal one-off usage-accounting backfill (idempotent script)."""
    if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", BACKFILL_LOCK_KEY):
        logger.info("prestart usage backfill skipped: another replica holds the lock")
        return
    try:
        code = await run_step(
            "usage backfill",
            [str(BACKEND_DIR / "scripts" / "backfill_usage_accounting.py"), "--apply"],
            env_number("BACKFILL_TIMEOUT_SECONDS", 1800),
        )
        if code != 0:
            logger.error(
                "prestart usage backfill failed exit=%d; API starts anyway", code
            )
    finally:
        await release_lock(conn, BACKFILL_LOCK_KEY)


async def prestart() -> None:
    run_migrations = env_flag("RUN_MIGRATIONS", default=True)
    run_ingestion = env_flag("RUN_INGESTION", default=True)
    run_backfill = env_flag("RUN_USAGE_BACKFILL", default=False)
    logger.info(
        "prestart begin migrations=%s ingestion=%s usage_backfill=%s",
        run_migrations,
        run_ingestion,
        run_backfill,
    )
    if not (run_migrations or run_ingestion or run_backfill):
        return

    conn = await wait_for_db(asyncpg_dsn())
    try:
        if run_migrations:
            await migrate(conn)
        if run_ingestion:
            await ingest(conn)
        if run_backfill:
            await backfill(conn)
    finally:
        await conn.close()
    logger.info("prestart done")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    try:
        asyncio.run(prestart())
    except PrestartError as exc:
        logger.critical("prestart FAILED, app not started: %s", exc)
        return exc.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
