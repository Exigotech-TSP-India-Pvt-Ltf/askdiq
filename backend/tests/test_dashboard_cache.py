"""Stale-while-revalidate dashboard cache (app.core.cache.SWRCache)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from app.core import cache as cache_module
from app.core.cache import SWRCache, dashboard_cache, dashboard_cached


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    c = Clock()
    monkeypatch.setattr(cache_module.time, "monotonic", c)
    yield c


def counter(values: list[int]):
    async def compute() -> int:
        values.append(len(values) + 1)
        return values[-1]

    return compute


async def _drain(c: SWRCache) -> None:
    for _ in range(20):
        if not c._tasks:  # noqa: SLF001 - test helper
            return
        await asyncio.sleep(0)
    await asyncio.gather(*list(c._tasks))  # noqa: SLF001


async def test_fresh_entries_are_served_without_recomputing(clock: Clock) -> None:
    c = SWRCache()
    runs: list[int] = []
    assert (
        await c.get_or_compute("k", counter(runs), fresh_seconds=10, stale_seconds=60)
        == 1
    )
    clock.now += 5
    assert (
        await c.get_or_compute("k", counter(runs), fresh_seconds=10, stale_seconds=60)
        == 1
    )
    assert runs == [1]
    assert (c.hits, c.misses) == (1, 1)


async def test_stale_entries_return_at_once_and_refresh_in_background(
    clock: Clock,
) -> None:
    c = SWRCache()
    runs: list[int] = []
    await c.get_or_compute("k", counter(runs), fresh_seconds=10, stale_seconds=60)
    clock.now += 30
    # Stale: the old value comes back immediately ...
    assert (
        await c.get_or_compute("k", counter(runs), fresh_seconds=10, stale_seconds=60)
        == 1
    )
    await _drain(c)
    # ... and the background refresh stored the new one.
    assert runs == [1, 2]
    assert (
        await c.get_or_compute("k", counter(runs), fresh_seconds=10, stale_seconds=60)
        == 2
    )
    assert c.stale_hits == 1


async def test_expired_entries_are_recomputed_while_the_caller_waits(
    clock: Clock,
) -> None:
    c = SWRCache()
    runs: list[int] = []
    await c.get_or_compute("k", counter(runs), fresh_seconds=10, stale_seconds=60)
    clock.now += 61
    assert (
        await c.get_or_compute("k", counter(runs), fresh_seconds=10, stale_seconds=60)
        == 2
    )


async def test_concurrent_misses_share_one_computation() -> None:
    c = SWRCache()
    release = asyncio.Event()
    runs = 0

    async def slow() -> str:
        nonlocal runs
        runs += 1
        await release.wait()
        return "v"

    tasks = [
        asyncio.create_task(
            c.get_or_compute("k", slow, fresh_seconds=10, stale_seconds=60)
        )
        for _ in range(5)
    ]
    await asyncio.sleep(0.01)
    release.set()
    assert await asyncio.gather(*tasks) == ["v"] * 5
    assert runs == 1


async def test_a_failed_background_refresh_keeps_the_stale_value(clock: Clock) -> None:
    c = SWRCache()
    await c.get_or_compute("k", counter([]), fresh_seconds=10, stale_seconds=60)
    clock.now += 30

    async def boom() -> int:
        raise RuntimeError("db down")

    assert await c.get_or_compute("k", boom, fresh_seconds=10, stale_seconds=60) == 1
    await _drain(c)
    assert await c.get_or_compute("k", boom, fresh_seconds=10, stale_seconds=60) == 1


async def test_invalidate_prefix_and_capacity() -> None:
    c = SWRCache(max_size=2)
    for key in ("a:1", "a:2", "b:1"):
        await c.get_or_compute(key, counter([]), fresh_seconds=10, stale_seconds=60)
    assert c.size == 2  # the oldest entry made room
    assert c.invalidate_prefix("a:") == 1
    assert c.size == 1


async def test_fresh_zero_disables_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = cache_module.get_settings()
    monkeypatch.setattr(settings, "dashboard_cache_fresh_seconds", 0)
    runs: list[int] = []
    await dashboard_cached("off", counter(runs))
    await dashboard_cached("off", counter(runs))
    assert runs == [1, 2]
    assert dashboard_cache.size == 0
