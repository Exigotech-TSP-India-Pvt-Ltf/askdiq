"""Unit tests for SingleFlight Concurrency Control and TTL Cache (Task 1.3)."""

import asyncio
import time

import pytest
from pydantic import BaseModel

from app.core.cache import TTLCache, make_cache_key
from app.core.concurrency import SingleFlight


@pytest.mark.asyncio
async def test_single_flight_collapses_50_concurrent_requests():
    """Verify that 50 concurrent tasks requesting identical keys execute underlying fn only once."""
    sf: SingleFlight[dict[str, str]] = SingleFlight()
    execution_count = 0

    async def expensive_query() -> dict[str, str]:
        nonlocal execution_count
        execution_count += 1
        await asyncio.sleep(0.05)  # Simulate DB query delay
        return {"status": "ok", "count": 42}

    # Spawn 50 concurrent calls to sf.run with the same key
    tasks = [sf.run("telemetry_key_1", expensive_query) for _ in range(50)]
    results = await asyncio.gather(*tasks)

    # Underlying fn executed exactly once
    assert execution_count == 1
    # All 50 callers received the identical result dictionary
    assert len(results) == 50
    for r in results:
        assert r == {"status": "ok", "count": 42}

    # Subsequent call after completion executes a new invocation
    second_result = await sf.run("telemetry_key_1", expensive_query)
    assert execution_count == 2
    assert second_result == {"status": "ok", "count": 42}


@pytest.mark.asyncio
async def test_single_flight_propagates_exceptions_and_resets():
    """Verify error propagation across all awaiting callers without deadlocking the key."""
    sf: SingleFlight[str] = SingleFlight()
    attempt = 0

    async def failing_query() -> str:
        nonlocal attempt
        attempt += 1
        await asyncio.sleep(0.02)
        raise ValueError("Database connection timeout")

    tasks = [sf.run("error_key", failing_query) for _ in range(5)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert attempt == 1
    assert len(results) == 5
    for r in results:
        assert isinstance(r, ValueError)
        assert str(r) == "Database connection timeout"

    # Subsequent call should not be permanently locked
    async def recovered_query() -> str:
        return "success"

    recovered = await sf.run("error_key", recovered_query)
    assert recovered == "success"


@pytest.mark.asyncio
async def test_single_flight_cancellation_shielding():
    """Verify cancelling one caller does not cancel underlying task or other awaiting callers."""
    sf: SingleFlight[str] = SingleFlight()

    async def slow_query() -> str:
        await asyncio.sleep(0.05)
        return "completed"

    task1 = asyncio.create_task(sf.run("cancel_key", slow_query))
    task2 = asyncio.create_task(sf.run("cancel_key", slow_query))

    # Cancel task1 immediately
    task1.cancel()

    # task2 should still complete successfully
    result2 = await task2
    assert result2 == "completed"

    # task1 was cancelled
    with pytest.raises(asyncio.CancelledError):
        await task1


def test_ttl_cache_operations_and_expiry():
    """Verify basic set/get, TTL expiration, and metrics."""
    cache = TTLCache(default_ttl=0.1, max_size=10)

    # Miss
    assert cache.get("missing") is None
    assert cache.misses == 1
    assert cache.hits == 0

    # Set and hit
    cache.set("k1", "v1")
    assert cache.get("k1") == "v1"
    assert cache.hits == 1
    assert cache.hit_rate == 0.5

    # Expiry
    time.sleep(0.12)
    assert cache.get("k1") is None
    assert cache.misses == 2

    # Clear
    cache.set("k2", "v2")
    assert cache.size == 1
    cache.clear()
    assert cache.size == 0
    assert cache.hits == 0


def test_ttl_cache_prefix_invalidation():
    """Verify targeted prefix invalidation (e.g. invalidating 'summary:' after test IP changes)."""
    cache = TTLCache(default_ttl=60.0)
    cache.set("summary:overview", {"users": 10})
    cache.set("summary:users", {"users": 8})
    cache.set("detail:turn_123", {"cost": 0.02})

    removed = cache.invalidate_prefix("summary:")
    assert removed == 2
    assert cache.get("summary:overview") is None
    assert cache.get("summary:users") is None
    assert cache.get("detail:turn_123") == {"cost": 0.02}


@pytest.mark.asyncio
async def test_ttl_cache_get_or_compute_stampede_protection():
    """Verify that get_or_compute deduplicates concurrent calls and serves from cache thereafter."""
    cache = TTLCache(default_ttl=10.0)
    compute_count = 0

    async def fetch_summary() -> dict[str, int]:
        nonlocal compute_count
        compute_count += 1
        await asyncio.sleep(0.04)
        return {"total_cost": 150}

    # 20 concurrent callers
    tasks = [cache.get_or_compute("agg:summary", fetch_summary) for _ in range(20)]
    results = await asyncio.gather(*tasks)

    # Computed exactly once
    assert compute_count == 1
    assert len(results) == 20
    for r in results:
        assert r == {"total_cost": 150}

    # 21st call is an immediate cache hit
    cached_val = await cache.get_or_compute("agg:summary", fetch_summary)
    assert compute_count == 1
    assert cached_val == {"total_cost": 150}


def test_make_cache_key():
    """Verify deterministic cache key generation with primitives and Pydantic models."""

    class DummyFilter(BaseModel):
        limit: int = 10
        status: str = "active"

    f = DummyFilter()
    k1 = make_cache_key("users", audience="overview", filter=f)
    k2 = make_cache_key("users", filter=f, audience="overview")  # swapped order

    # Deterministic alphabetical ordering
    assert k1 == k2
    assert 'audience="overview"' in k1
    assert "users" in k1
