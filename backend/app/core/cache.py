"""TTL-bounded In-Memory Cache with Single-Flight Protection.

Provides short-TTL caching for high-overhead summaries (/admin/usage/summary, /admin/dashboard)
with single-flight deduplication to eliminate cache stampedes and DB connection pool exhaustion.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import ScopedRunner, single_flight
from app.core.config import get_settings
from app.core.logging_config import get_logger

T = TypeVar("T")
logger = get_logger(__name__)


def make_cache_key(prefix: str, **kwargs: Any) -> str:
    """Constructs a deterministic cache key string from prefix and keyword arguments."""
    serialized_parts: list[str] = []
    for k in sorted(kwargs.keys()):
        val = kwargs[k]
        if hasattr(val, "model_dump"):
            val = val.model_dump()
        serialized_parts.append(f"{k}={json.dumps(val, sort_keys=True, default=str)}")
    return f"{prefix}:{':'.join(serialized_parts)}"


class CacheEntry(Generic[T]):
    """Container for cached value and its monotonic expiration timestamp."""

    __slots__ = ("expires_at", "value")

    def __init__(self, value: T, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


class TTLCache:
    """Thread-safe, async-friendly in-memory cache with bounded TTL and capacity."""

    def __init__(self, default_ttl: float = 15.0, max_size: int = 1000) -> None:
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._store: dict[str, CacheEntry[Any]] = {}
        self._hits: int = 0
        self._misses: int = 0

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return (self._hits / total) if total > 0 else 0.0

    @property
    def size(self) -> int:
        return len(self._store)

    def get(self, key: str) -> Any | None:
        """Retrieves non-expired value from cache, returning None on miss or expiration."""
        entry = self._store.get(key)
        now = time.monotonic()
        if entry is None:
            self._misses += 1
            return None

        if entry.is_expired(now):
            self._store.pop(key, None)
            self._misses += 1
            return None

        self._hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """Stores value in cache with expiration timestamp."""
        effective_ttl = ttl if ttl is not None else self._default_ttl
        now = time.monotonic()

        # Evict random expired keys if cache reaches max capacity
        if len(self._store) >= self._max_size and key not in self._store:
            self._evict_expired(now)
            # If still over limit, drop oldest entry
            if len(self._store) >= self._max_size:
                first_key = next(iter(self._store))
                self._store.pop(first_key, None)

        self._store[key] = CacheEntry(value=value, expires_at=now + effective_ttl)

    def _evict_expired(self, now: float) -> None:
        expired_keys = [k for k, v in self._store.items() if v.is_expired(now)]
        for k in expired_keys:
            self._store.pop(k, None)

    def delete(self, key: str) -> bool:
        """Removes specific key from cache."""
        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        """Clears all cached entries and resets metrics."""
        self._store.clear()
        self._hits = 0
        self._misses = 0

    def invalidate_prefix(self, prefix: str) -> int:
        """Removes all keys matching prefix (e.g. invalidating 'summary:' after test IP changes)."""
        keys_to_remove = [k for k in self._store if k.startswith(prefix)]
        for k in keys_to_remove:
            self._store.pop(k, None)
        return len(keys_to_remove)

    async def get_or_compute(
        self,
        key: str,
        coro_fn: Callable[[], Awaitable[T]],
        ttl: float | None = None,
    ) -> T:
        """Combines cache lookup with SingleFlight stampede defense.

        1. Fast path: Returns cached value if available.
        2. Cache miss: Collapses concurrent callers into a single execution of coro_fn.
        3. Caches and returns the resulting value.
        """
        cached = self.get(key)
        if cached is not None:
            return cached

        async def _compute_and_cache() -> T:
            # Re-check cache inside single-flight execution
            val = self.get(key)
            if val is not None:
                return val
            computed = await coro_fn()
            self.set(key, computed, ttl=ttl)
            return computed

        return await single_flight.run(key, _compute_and_cache)


# Global singleton instance for dashboard aggregation and telemetry summaries
aggregation_cache = TTLCache(default_ttl=15.0)


class SWRCache:
    """Stale-while-revalidate cache for dashboard aggregates.

    A plain TTL cache makes every visit after the TTL pay the full
    aggregation again (seconds per endpoint, all at once when a dashboard
    fans out). Here an entry is *fresh* for ``fresh_seconds`` and then
    *stale* until ``stale_seconds``: a stale entry is returned at once while
    one background task (single-flight) recomputes it. Only a missing or
    expired entry makes the caller wait, and concurrent callers of the same
    key share that one computation.

    ``compute`` must not use a request-scoped session: a background refresh
    outlives the request that triggered it. Callers open their own session
    (``ScopedRunner.gather``).
    """

    def __init__(self, max_size: int = 512) -> None:
        self._max_size = max_size
        # key -> (value, computed_at monotonic)
        self._store: dict[str, tuple[Any, float]] = {}
        self._refreshing: set[str] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.hits = 0
        self.stale_hits = 0
        self.misses = 0

    @property
    def size(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()
        self.hits = self.stale_hits = self.misses = 0

    def invalidate_prefix(self, prefix: str) -> int:
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            self._store.pop(k, None)
        return len(keys)

    def _put(self, key: str, value: Any) -> None:
        if key not in self._store and len(self._store) >= self._max_size:
            # Drop the oldest computation first.
            oldest = min(self._store, key=lambda k: self._store[k][1])
            self._store.pop(oldest, None)
        self._store[key] = (value, time.monotonic())

    async def _compute(self, key: str, compute: Callable[[], Awaitable[T]]) -> T:
        value = await compute()
        self._put(key, value)
        return value

    def _refresh_in_background(
        self, key: str, compute: Callable[[], Awaitable[Any]]
    ) -> None:
        if key in self._refreshing:
            return
        self._refreshing.add(key)

        async def _run() -> None:
            try:
                await single_flight.run(
                    f"swr:{key}", lambda: self._compute(key, compute)
                )
            except Exception:  # pylint: disable=broad-exception-caught
                # Keep serving the stale value; the next stale read retries.
                logger.warning("swr_refresh_failed", key=key, exc_info=True)
            finally:
                self._refreshing.discard(key)

        task = asyncio.create_task(_run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def get_or_compute(
        self,
        key: str,
        compute: Callable[[], Awaitable[T]],
        *,
        fresh_seconds: float,
        stale_seconds: float,
    ) -> T:
        entry = self._store.get(key)
        if entry is not None:
            value, computed_at = entry
            age = time.monotonic() - computed_at
            if age < fresh_seconds:
                self.hits += 1
                return value
            if age < max(stale_seconds, fresh_seconds):
                self.stale_hits += 1
                self._refresh_in_background(key, compute)
                return value
            self._store.pop(key, None)
        self.misses += 1
        return await single_flight.run(
            f"swr:{key}", lambda: self._compute(key, compute)
        )


# Dashboard aggregates (insights, usage, governance, visitors): see SWRCache.
dashboard_cache = SWRCache()


async def dashboard_cached(key: str, compute: Callable[[], Awaitable[T]]) -> T:
    """``dashboard_cache`` with the configured fresh / stale windows.

    ``DASHBOARD_CACHE_FRESH_SECONDS = 0`` turns caching off (every call computes).
    """
    cfg = get_settings()
    if cfg.dashboard_cache_fresh_seconds <= 0:
        return await compute()
    return await dashboard_cache.get_or_compute(
        key,
        compute,
        fresh_seconds=cfg.dashboard_cache_fresh_seconds,
        stale_seconds=cfg.dashboard_cache_stale_seconds,
    )


async def dashboard_query(
    key: str,
    runner: ScopedRunner,
    query: Callable[[AsyncSession], Awaitable[T]],
) -> T:
    """``dashboard_cached`` for a read that needs a database session.

    ``query`` runs on its own short-lived session from ``runner`` (never the
    request's), so a background refresh can outlive the request.
    """

    async def compute() -> T:
        (result,) = await runner.gather(query)
        return result

    return await dashboard_cached(key, compute)
