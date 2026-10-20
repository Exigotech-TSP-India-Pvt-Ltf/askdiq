"""Governance memory copies (Overall / Monitoring): serve, refresh, ETag."""

from __future__ import annotations

import asyncio

from app.governance.live_cache import LiveCache, clear_live_caches


def _cache(fresh: float = 60.0) -> tuple[LiveCache, list[str]]:
    calls: list[str] = []

    async def compute(audience: str, _runner: object) -> dict[str, object]:
        calls.append(audience)
        return {"audience": audience, "n": len(calls)}

    return LiveCache("test", compute, lambda: fresh), calls


async def test_first_read_computes_then_memory_serves():
    cache, calls = _cache()
    first = await cache.get("users")
    second = await cache.get("users")
    assert calls == ["users"]
    assert second is first
    assert first.body == b'{"audience":"users","n":1}'


async def test_stale_copy_is_served_and_refreshed_in_background():
    cache, calls = _cache(fresh=0.0)
    first = await cache.get("overview")
    served = await cache.get("overview")
    assert served is first  # never waits on the recomputation
    await asyncio.sleep(0.01)
    assert len(calls) == 2
    assert cache.peek("overview").payload["n"] == 2


async def test_unchanged_body_keeps_its_etag():
    cache, _ = _cache()
    one = cache.store("overview", {"a": 1})
    two = cache.store("overview", {"a": 1})
    three = cache.store("overview", {"a": 2})
    assert one.etag == two.etag != three.etag


async def test_recently_read_and_clear():
    cache, _ = _cache()
    assert not cache.recently_read("users", 60)
    await cache.get("users")
    assert cache.recently_read("users", 60)
    clear_live_caches()
    assert cache.peek("users") is None
    assert not cache.recently_read("users", 60)
