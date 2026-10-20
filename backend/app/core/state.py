"""Shared state backends (Postgres instead of Redis).

`StateBackend` covers the small amount of state that must be shared by every
uvicorn worker and replica:

- ``incr_window``: count hits on a key inside a sliding window (rate limits).
- ``put_once`` / ``take_once``: single-use values with a TTL (OIDC handshake).

`MemoryState` keeps everything in-process (unit tests, single-process dev).
`PostgresState` uses the UNLOGGED ``rate_limit_buckets`` table and the
``oidc_states`` table (alembic f8a9b0c1d2e3); it is the default outside
``ENV=test``.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy import text

from app.core import database
from app.core.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

# Sliding windows are approximated by this many fixed sub-windows.
SUB_WINDOWS_PER_WINDOW = 6


@runtime_checkable
class StateBackend(Protocol):
    """Shared-state operations used by the rate limiter and the OIDC flow."""

    async def incr_window(self, key: str, window_s: int) -> int:
        """Record one hit on ``key`` and return the hits inside the window."""
        ...

    async def put_once(self, key: str, value: dict[str, Any], ttl: float) -> bool:
        """Store ``value`` under ``key`` for ``ttl`` seconds; False if it exists."""
        ...

    async def take_once(self, key: str) -> dict[str, Any] | None:
        """Atomically remove and return the value, or None if absent/expired."""
        ...

    async def cleanup(self) -> int:
        """Delete expired entries; returns the number of rows removed."""
        ...


def hash_key(key: str) -> str:
    """Stable digest so raw identifiers (emails, IPs, OIDC state) are never stored."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def sub_window_seconds(window_s: int) -> int:
    """Width of one fixed sub-window (10 s for the one-minute window)."""
    return max(1, math.ceil(window_s / SUB_WINDOWS_PER_WINDOW))


class MemoryState:
    """In-process implementation: exact sliding window over monotonic time."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._values: dict[str, tuple[dict[str, Any], float]] = {}

    async def incr_window(self, key: str, window_s: int) -> int:
        now = time.monotonic()
        log = self._hits[key]
        while log and now - log[0] > window_s:
            log.popleft()
        log.append(now)
        return len(log)

    async def put_once(self, key: str, value: dict[str, Any], ttl: float) -> bool:
        now = time.monotonic()
        current = self._values.get(key)
        if current is not None and current[1] > now:
            return False
        self._values[key] = (dict(value), now + ttl)
        return True

    async def take_once(self, key: str) -> dict[str, Any] | None:
        entry = self._values.pop(key, None)
        if entry is None or entry[1] <= time.monotonic():
            return None
        return entry[0]

    async def cleanup(self) -> int:
        now = time.monotonic()
        removed = 0
        for key in [k for k, (_, exp) in self._values.items() if exp <= now]:
            del self._values[key]
            removed += 1
        for key in [k for k, log in self._hits.items() if not log]:
            del self._hits[key]
            removed += 1
        return removed


_INCR_SQL = text(
    """
    WITH ins AS (
        INSERT INTO rate_limit_buckets AS b (key, window_start, count)
        VALUES (
            :key,
            to_timestamp(
                floor(extract(epoch FROM clock_timestamp())::float8 / CAST(:sub AS float8))
                * CAST(:sub AS float8)
            ),
            1
        )
        ON CONFLICT (key, window_start)
        DO UPDATE SET count = b.count + 1
        RETURNING window_start, count
    )
    SELECT (ins.count + COALESCE((
        SELECT sum(p.count) FROM rate_limit_buckets p
        WHERE p.key = :key
          AND p.window_start < ins.window_start
          AND p.window_start > ins.window_start - make_interval(secs => :window)
    ), 0))::int
    FROM ins
    """
)

_PUT_SQL = text(
    """
    INSERT INTO oidc_states AS o (state_hash, payload, expires_at)
    VALUES (:h, CAST(:payload AS jsonb), now() + make_interval(secs => :ttl))
    ON CONFLICT (state_hash) DO UPDATE
        SET payload = EXCLUDED.payload, expires_at = EXCLUDED.expires_at
        WHERE o.expires_at <= now()
    RETURNING state_hash
    """
)

_TAKE_SQL = text(
    "DELETE FROM oidc_states WHERE state_hash = :h AND expires_at > now() "
    "RETURNING payload"
)

_CLEAN_BUCKETS_SQL = text(
    "DELETE FROM rate_limit_buckets "
    "WHERE window_start < now() - make_interval(secs => :keep)"
)
_CLEAN_STATES_SQL = text("DELETE FROM oidc_states WHERE expires_at <= now()")


class PostgresState:
    """Postgres implementation shared by every worker and replica."""

    def __init__(
        self, engine: AsyncEngine | None = None, bucket_retention_s: int = 3600
    ) -> None:
        if engine is None:
            engine = database.engine
        # One statement per call: autocommit avoids BEGIN/COMMIT round trips.
        self._engine = engine.execution_options(isolation_level="AUTOCOMMIT")
        self._bucket_retention_s = bucket_retention_s

    async def incr_window(self, key: str, window_s: int) -> int:
        async with self._engine.connect() as conn:
            count = await conn.scalar(
                _INCR_SQL,
                {
                    "key": hash_key(key),
                    "sub": float(sub_window_seconds(window_s)),
                    "window": float(window_s),
                },
            )
        return int(count or 0)

    async def put_once(self, key: str, value: dict[str, Any], ttl: float) -> bool:
        async with self._engine.connect() as conn:
            row = await conn.scalar(
                _PUT_SQL,
                {"h": hash_key(key), "payload": json.dumps(value), "ttl": float(ttl)},
            )
        return row is not None

    async def take_once(self, key: str) -> dict[str, Any] | None:
        async with self._engine.connect() as conn:
            payload = await conn.scalar(_TAKE_SQL, {"h": hash_key(key)})
        if payload is None:
            return None
        return payload if isinstance(payload, dict) else json.loads(payload)

    async def cleanup(self) -> int:
        async with self._engine.connect() as conn:
            buckets = await conn.execute(
                _CLEAN_BUCKETS_SQL, {"keep": float(self._bucket_retention_s)}
            )
            states = await conn.execute(_CLEAN_STATES_SQL)
        return int(buckets.rowcount or 0) + int(states.rowcount or 0)


_backend: StateBackend | None = None


def _build_default_backend() -> StateBackend:
    settings = get_settings()
    choice = settings.state_backend.strip().lower()
    if choice == "auto":
        choice = "memory" if settings.env == "test" else "postgres"
    if choice == "memory":
        return MemoryState()
    if choice == "postgres":
        return PostgresState()
    raise ValueError(f"Unknown STATE_BACKEND '{settings.state_backend}'")


def get_state_backend() -> StateBackend:
    """Process-wide backend, built lazily from settings."""
    global _backend  # noqa: PLW0603 - process-wide backend
    if _backend is None:
        _backend = _build_default_backend()
    return _backend


def set_state_backend(backend: StateBackend | None) -> None:
    """Override (or reset with None) the process-wide backend; used by tests."""
    global _backend  # noqa: PLW0603 - process-wide backend
    _backend = backend
