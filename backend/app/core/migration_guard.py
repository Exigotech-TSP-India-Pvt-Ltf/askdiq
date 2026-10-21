"""Lock-safe Alembic upgrades (used by ``alembic/env.py``).

An ``ALTER TABLE`` needs an ACCESS EXCLUSIVE lock. While it *waits* for one
(behind any open transaction on the table) Postgres queues every later query
on that table behind it, so one blocked migration hangs every API request.
``lock_timeout`` makes the migration give up quickly instead; the whole
upgrade is then retried with exponential backoff until the lock is free.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol, TypeVar

from sqlalchemy import text

logger = logging.getLogger("alembic.env.lock_guard")

# Postgres SQLSTATE ``lock_not_available`` (lock_timeout / NOWAIT).
LOCK_NOT_AVAILABLE_SQLSTATE = "55P03"
MAX_BACKOFF_SECONDS = 60.0

T = TypeVar("T")


class _Executes(Protocol):
    def execute(self, statement: object) -> object: ...

    def commit(self) -> None: ...


def apply_migration_timeouts(
    connection: _Executes, *, lock_timeout_ms: int, statement_timeout_ms: int
) -> None:
    """Session-level ``lock_timeout`` / ``statement_timeout`` on ``connection``.

    Committed straight away so the settings outlive the (autobegun)
    transaction they ran in and cover every migration on this connection.
    """
    # Integers from typed settings; SET cannot take bind parameters.
    connection.execute(text(f"SET lock_timeout = {int(lock_timeout_ms)}"))  # nosec B608
    connection.execute(
        text(f"SET statement_timeout = {int(statement_timeout_ms)}")  # nosec B608
    )
    connection.commit()


def is_lock_timeout(exc: BaseException) -> bool:
    """True when ``exc`` (or its DBAPI cause) is SQLSTATE 55P03."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for candidate in (current, getattr(current, "orig", None)):
            code = getattr(candidate, "pgcode", None) or getattr(
                candidate, "sqlstate", None
            )
            if code == LOCK_NOT_AVAILABLE_SQLSTATE:
                return True
        current = current.__cause__ or current.__context__
    return False


def backoff_seconds(attempt: int, base_seconds: float) -> float:
    """Delay before retry ``attempt`` (1-based): base, 2x base, ... capped."""
    return min(base_seconds * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)


def run_with_lock_retries(
    upgrade: Callable[[], T],
    *,
    retries: int,
    base_backoff_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``upgrade``; on a lock timeout retry it up to ``retries`` times.

    Any other error, or a lock timeout on the last attempt, propagates (the
    container prestart then refuses to start the API).
    """
    attempt = 0
    while True:
        try:
            return upgrade()
        except Exception as exc:
            if not is_lock_timeout(exc):
                raise
            attempt += 1
            if attempt > retries:
                logger.error(
                    "migration lock not available after %d attempt(s); giving up. "
                    "Another session holds a lock on a table being migrated "
                    "(check pg_stat_activity / pg_locks).",
                    attempt,
                )
                raise
            delay = backoff_seconds(attempt, base_backoff_seconds)
            logger.warning(
                "migration lock not available (SQLSTATE %s, lock_timeout hit); "
                "migration rolled back, retry %d/%d in %.1fs",
                LOCK_NOT_AVAILABLE_SQLSTATE,
                attempt,
                retries,
                delay,
            )
            sleep(delay)
