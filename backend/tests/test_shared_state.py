"""Shared state: StateBackend, PostgresState, event bus, semaphores."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core import concurrency, rate_limit, state
from app.core.config import get_settings
from app.core.events import TOPIC_CACHE_INVALIDATE, Event, EventBus, asyncpg_dsn
from app.core.state import MemoryState, PostgresState, StateBackend
from tests.pg_helpers import new_engine, require_postgres

settings = get_settings()


@pytest.fixture
def restore_backend():
    yield
    state.set_state_backend(None)


# ---------------------------------------------------------------------------
# MemoryState and backend selection (no DB)
# ---------------------------------------------------------------------------


async def test_memory_state_counts_a_sliding_window(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(state.time, "monotonic", lambda: clock[0])
    backend = MemoryState()

    assert [await backend.incr_window("k", 60) for _ in range(3)] == [1, 2, 3]
    clock[0] += 61
    assert await backend.incr_window("k", 60) == 1
    assert await backend.incr_window("other", 60) == 1


async def test_memory_state_put_and_take_once(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(state.time, "monotonic", lambda: clock[0])
    backend = MemoryState()

    assert await backend.put_once("s", {"nonce": "n1"}, ttl=600) is True
    assert await backend.put_once("s", {"nonce": "n2"}, ttl=600) is False
    assert await backend.take_once("s") == {"nonce": "n1"}
    assert await backend.take_once("s") is None

    await backend.put_once("expiring", {"v": 1}, ttl=10)
    clock[0] += 11
    assert await backend.take_once("expiring") is None


async def test_memory_state_cleanup_drops_expired_values(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(state.time, "monotonic", lambda: clock[0])
    backend = MemoryState()
    await backend.put_once("a", {}, ttl=1)
    await backend.put_once("b", {}, ttl=100)
    clock[0] += 5
    assert await backend.cleanup() == 1
    assert await backend.take_once("b") == {}


def test_both_backends_implement_the_protocol():
    assert isinstance(MemoryState(), StateBackend)
    assert isinstance(PostgresState(new_engine()), StateBackend)


def test_backend_defaults(monkeypatch, restore_backend):
    monkeypatch.setattr(settings, "state_backend", "auto")
    monkeypatch.setattr(settings, "env", "test")
    state.set_state_backend(None)
    assert isinstance(state.get_state_backend(), MemoryState)

    monkeypatch.setattr(settings, "env", "development")
    state.set_state_backend(None)
    assert isinstance(state.get_state_backend(), PostgresState)

    monkeypatch.setattr(settings, "state_backend", "memory")
    state.set_state_backend(None)
    assert isinstance(state.get_state_backend(), MemoryState)

    monkeypatch.setattr(settings, "state_backend", "bogus")
    state.set_state_backend(None)
    with pytest.raises(ValueError):
        state.get_state_backend()


def test_sub_window_is_ten_seconds_for_a_minute():
    assert state.sub_window_seconds(60) == 10
    assert state.hash_key("user@example.com") != "user@example.com"


class _FakeSharedBackend:
    """Stand-in for a shared (non-memory) backend: a plain counter per key."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr_window(self, key: str, window_s: int) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def put_once(self, key: str, value: dict, ttl: float) -> bool:
        return True

    async def take_once(self, key: str) -> dict | None:
        return None

    async def cleanup(self) -> int:
        return 0


async def test_async_limiter_uses_the_shared_backend(monkeypatch, restore_backend):
    state.set_state_backend(_FakeSharedBackend())
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)

    await rate_limit.aenforce_rate_limit("shared@example.com")
    await rate_limit.aenforce_rate_limit("shared@example.com")
    with pytest.raises(HTTPException) as exc_info:
        await rate_limit.aenforce_rate_limit("shared@example.com")
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "10"
    # The in-process log is untouched: the shared backend is the source of truth.
    assert "shared@example.com" not in rate_limit._user_request_log


async def test_async_limiter_with_memory_backend_uses_the_sliding_window(
    monkeypatch, restore_backend
):
    rate_limit._feedback_user_log.clear()
    state.set_state_backend(MemoryState())
    await rate_limit.aenforce_feedback_rate_limit("fb@example.com", None, limit=1)
    with pytest.raises(HTTPException):
        await rate_limit.aenforce_feedback_rate_limit("fb@example.com", None, limit=1)
    assert len(rate_limit._feedback_user_log["fb@example.com"]) == 1


async def test_exempt_actors_skip_the_per_user_limit(monkeypatch, restore_backend):
    rate_limit._user_request_log.clear()
    state.set_state_backend(MemoryState())
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    monkeypatch.setattr(settings, "rate_limit_exempt_actors", ["load@example.com"])
    for _ in range(5):
        await rate_limit.aenforce_rate_limit("load@example.com")
        rate_limit.enforce_rate_limit("load@example.com")
    await rate_limit.aenforce_rate_limit("normal@example.com")
    with pytest.raises(HTTPException):
        await rate_limit.aenforce_rate_limit("normal@example.com")


# ---------------------------------------------------------------------------
# Configurable semaphores and the global LLM gate (no DB)
# ---------------------------------------------------------------------------


def test_semaphores_follow_config():
    from app.api.routes import admin_usage
    from app.governance import online_eval

    assert admin_usage._agg_semaphore._value == settings.usage_agg_max_concurrency
    assert online_eval._eval_semaphore._value == settings.online_eval_max_concurrency


def test_llm_gate_limit_follows_config(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_concurrency", 3)
    concurrency.reset_llm_gate()
    try:
        assert concurrency.get_llm_gate().limit == 3
    finally:
        concurrency.reset_llm_gate()


def test_query_route_holds_an_llm_slot():
    from main import app

    route = next(r for r in app.routes if getattr(r, "path", None) == "/query")
    calls = [d.call for d in route.dependant.dependencies]
    assert concurrency.llm_capacity in calls


async def test_llm_saturation_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_concurrency", 2)
    monkeypatch.setattr(settings, "llm_saturated_retry_after_seconds", 7)
    concurrency.reset_llm_gate()
    release = asyncio.Event()
    both_entered = asyncio.Event()
    entered = 0

    mini = FastAPI()

    @mini.get("/llm", dependencies=[Depends(concurrency.llm_capacity)])
    async def _llm() -> dict[str, bool]:
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await release.wait()
        return {"ok": True}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=mini), base_url="http://t"
        ) as ac:
            holders = [asyncio.create_task(ac.get("/llm")) for _ in range(2)]
            await asyncio.wait_for(both_entered.wait(), timeout=5)
            rejected = await ac.get("/llm")
            assert rejected.status_code == 429
            assert rejected.headers["Retry-After"] == "7"
            release.set()
            assert [r.status_code for r in await asyncio.gather(*holders)] == [
                200,
                200,
            ]
            assert concurrency.get_llm_gate().in_use == 0
            assert (await ac.get("/llm")).status_code == 200
    finally:
        concurrency.reset_llm_gate()


# ---------------------------------------------------------------------------
# Event bus fan-out (no DB)
# ---------------------------------------------------------------------------


def test_cache_invalidation_events_reach_local_callbacks():
    from app.jobs.runtime import _invalidate_local_caches

    bus = EventBus(dsn="postgresql://unused")
    seen: list[Event] = []
    bus.on(TOPIC_CACHE_INVALIDATE, seen.append)
    bus.on(TOPIC_CACHE_INVALIDATE, _invalidate_local_caches)
    queue = bus.subscribe("*")

    from app.core.cache import aggregation_cache

    aggregation_cache.set("summary:x", 1)
    bus.dispatch(Event(topic=TOPIC_CACHE_INVALIDATE, id="summary:", origin="other"))

    assert seen and seen[0].id == "summary:"
    assert queue.get_nowait().topic == TOPIC_CACHE_INVALIDATE
    assert aggregation_cache.get("summary:x") is None


def test_asyncpg_dsn_strips_the_driver():
    assert (
        asyncpg_dsn("postgresql+asyncpg://u:p@h:5432/d") == "postgresql://u:p@h:5432/d"
    )


# ---------------------------------------------------------------------------
# PostgresState: multi-instance behaviour (real Postgres; runs in CI)
# ---------------------------------------------------------------------------


@pytest.fixture
async def two_instances():
    engine_a, engine_b = new_engine(), new_engine()
    await require_postgres(engine_a)
    try:
        yield PostgresState(engine_a), PostgresState(engine_b), engine_a
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


async def test_two_instances_share_one_rate_limit(two_instances):
    state_a, state_b, _ = two_instances
    key = f"test:{uuid.uuid4()}"
    allowed = 0
    for i in range(15):
        backend = state_a if i % 2 == 0 else state_b
        if await backend.incr_window(key, 60) <= 10:
            allowed += 1
    assert allowed == 10


async def test_rate_limiter_is_exact_across_two_instances(
    two_instances, monkeypatch, restore_backend
):
    state_a, state_b, _ = two_instances
    monkeypatch.setattr(settings, "rate_limit_per_minute", 10)
    actor = f"multi-{uuid.uuid4().hex[:12]}@example.com"
    outcomes = []
    for i in range(14):
        state.set_state_backend(state_a if i % 2 == 0 else state_b)
        try:
            await rate_limit.aenforce_rate_limit(actor)
            outcomes.append(200)
        except HTTPException as exc:
            outcomes.append(exc.status_code)
            assert exc.headers["Retry-After"]
    assert outcomes.count(200) == 10
    assert outcomes.count(429) == 4


async def test_take_once_is_single_use_under_20_concurrent_callers(two_instances):
    state_a, state_b, _ = two_instances
    key = f"oidc:{uuid.uuid4()}"
    assert await state_a.put_once(key, {"nonce": "abc", "verifier": "v"}, 600)
    assert await state_b.put_once(key, {"nonce": "other"}, 600) is False

    results = await asyncio.gather(
        *[(state_a if i % 2 else state_b).take_once(key) for i in range(20)]
    )
    winners = [r for r in results if r is not None]
    assert winners == [{"nonce": "abc", "verifier": "v"}]


async def test_expired_state_cannot_be_taken_and_is_cleaned_up(two_instances):
    state_a, _, engine = two_instances
    key = f"oidc:{uuid.uuid4()}"
    await state_a.put_once(key, {"v": 1}, ttl=0.05)
    await asyncio.sleep(0.2)
    assert await state_a.take_once(key) is None

    await state_a.put_once(f"oidc:{uuid.uuid4()}", {"v": 2}, ttl=0.01)
    await asyncio.sleep(0.1)
    assert await state_a.cleanup() >= 1
    async with engine.connect() as conn:
        expired = await conn.scalar(
            text("SELECT count(*) FROM oidc_states WHERE expires_at <= now()")
        )
    assert expired == 0


@pytest.mark.perf
async def test_rate_limit_check_adds_under_3ms_p95():
    engine = new_engine(pool_size=2, max_overflow=0)
    await require_postgres(engine)
    backend = PostgresState(engine)
    key = f"perf:{uuid.uuid4()}"
    try:
        for _ in range(20):
            await backend.incr_window(key, 60)
        samples = []
        for _ in range(200):
            start = time.perf_counter()
            await backend.incr_window(key, 60)
            samples.append((time.perf_counter() - start) * 1000)
    finally:
        await engine.dispose()
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    assert p95 < 3.0, f"rate-limit check p95 {p95:.2f} ms"


# ---------------------------------------------------------------------------
# LISTEN/NOTIFY event bus across two instances (real Postgres; runs in CI)
# ---------------------------------------------------------------------------


async def _wait_for(predicate, limit_s: float) -> None:
    deadline = asyncio.get_running_loop().time() + limit_s
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


@pytest.fixture
async def two_buses():
    engine_a, engine_b = new_engine(), new_engine()
    await require_postgres(engine_a)
    channel = f"askdiq_test_{uuid.uuid4().hex[:8]}"
    bus_a = EventBus(channel=channel, engine=engine_a, reconnect_max_seconds=1)
    bus_b = EventBus(channel=channel, engine=engine_b, reconnect_max_seconds=1)
    await bus_a.start()
    await bus_b.start()
    try:
        await asyncio.wait_for(bus_a.connected.wait(), 10)
        await asyncio.wait_for(bus_b.connected.wait(), 10)
        yield bus_a, bus_b, engine_a
    finally:
        await bus_a.stop()
        await bus_b.stop()
        await engine_a.dispose()
        await engine_b.dispose()


async def test_event_published_on_a_reaches_b_within_1s(two_buses):
    bus_a, bus_b, _ = two_buses
    queue = bus_b.subscribe("branding")

    started = time.perf_counter()
    await bus_a.publish("branding", 42)
    event = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert time.perf_counter() - started < 1.0
    assert event == Event(topic="branding", id="42", origin=bus_a.instance_id)


async def test_publish_rejects_oversized_payloads(two_buses):
    bus_a, _, _ = two_buses
    with pytest.raises(ValueError):
        await bus_a.publish("x", "y" * 9000)


async def test_dropped_listener_reconnects_and_resubscribes(two_buses):
    bus_a, bus_b, engine = two_buses
    queue = bus_b.subscribe("roles")
    pid = bus_b.backend_pid
    assert pid is not None

    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        await conn.commit()

    await _wait_for(
        lambda: bus_b.connect_count >= 2 and bus_b.connected.is_set(), limit_s=15
    )
    assert bus_b.backend_pid not in (None, pid)

    await bus_a.publish("roles", "user-7")
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.id == "user-7"
