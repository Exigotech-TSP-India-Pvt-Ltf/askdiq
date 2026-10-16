"""Pooled DB connections are always returned, also on cancellation and timeouts.

Regression for a leak seen in the dev log ("The garbage collector is trying to
clean up non-checked-in connection ...") that exhausted the pool, so backend
calls waited until the frontend's fetches failed with HeadersTimeoutError:

- ``GET /branding`` ran its single-flight load on the *caller's* session in a
  separate task; a caller cancelled mid-load (client disconnect) closed that
  session under the running load. The load now opens its own session and is
  bounded by ``BRANDING_LOAD_TIMEOUT_SECONDS``.
- The governance SSE producer had no bound on a snapshot; it now gives up
  after ``GOVERNANCE_STREAM_SNAPSHOT_TIMEOUT_SECONDS`` and retries next tick.

The unit tests use a recording session factory; the ``db`` tests check the
real pool's ``checkedout()`` count after cancelling mid-query (CI Postgres).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.branding import service as branding
from app.core import concurrency
from app.core.concurrency import ScopedRunner, fanout_slots, gather_scoped
from app.core.config import get_settings
from app.core.database import release_connection
from app.core.events import EventBus
from app.governance.overview import overview_payload
from app.governance.stream import GovernanceStreamHub
from tests.pg_helpers import new_engine, require_postgres

SLOW_QUERY = text("SELECT pg_sleep(5)")
CANCEL_AFTER_SECONDS = 0.3
RELEASE_WAIT_SECONDS = 5.0
POLL_SECONDS = 0.05


class RecordingFactory:
    """Session factory stand-in that counts opened and closed sessions."""

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0

    @contextlib.asynccontextmanager
    async def __call__(self) -> AsyncIterator[Any]:
        self.opened += 1
        try:
            yield object()
        finally:
            self.closed += 1


@pytest.fixture(autouse=True)
def _fresh_branding_cache() -> Iterator[None]:
    branding.invalidate_branding_cache()
    yield
    branding.invalidate_branding_cache()


async def test_branding_load_survives_a_cancelled_caller_and_closes_its_own_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_row(_db: object) -> None:
        await asyncio.sleep(0.1)

    monkeypatch.setattr(branding, "_fetch_row", slow_row)
    factory = RecordingFactory()

    first = asyncio.create_task(branding.get_branding(factory))
    await asyncio.sleep(0.02)
    first.cancel()  # the client went away mid-load
    with pytest.raises(asyncio.CancelledError):
        await first

    resolved = await branding.get_branding(factory)  # joins the same load
    assert resolved == branding.resolve({}, get_settings())
    assert factory.opened == factory.closed == 1


async def test_branding_load_is_bounded_and_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hung_row(_db: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(branding, "_fetch_row", hung_row)
    monkeypatch.setattr(get_settings(), "branding_load_timeout_seconds", 0.05)
    factory = RecordingFactory()

    async with asyncio.timeout(RELEASE_WAIT_SECONDS):
        resolved = await branding.get_branding(factory)

    assert resolved == branding.resolve({}, get_settings())
    assert factory.opened == factory.closed == 1
    assert branding._cache.get("branding:resolved") is None  # not cached


async def test_stream_snapshot_is_bounded_and_the_producer_keeps_going() -> None:
    factory = RecordingFactory()

    async def hung_snapshot(_audience: str) -> dict[str, Any]:
        async with factory():
            await asyncio.Event().wait()
        return {}

    hub = GovernanceStreamHub(
        snapshot_fn=hung_snapshot,
        interval=60,
        bus=EventBus(dsn="postgresql://unused"),
        snapshot_timeout=0.05,
    )
    async with asyncio.timeout(RELEASE_WAIT_SECONDS):
        await hub.tick("overview")
    assert hub.ticks["overview"] == 0
    assert factory.opened == factory.closed == 1
    await hub.aclose()


class FakeSession:
    def __init__(self, in_transaction: bool) -> None:
        self._in_transaction = in_transaction
        self.commits = 0

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def commit(self) -> None:
        self.commits += 1
        self._in_transaction = False


async def test_release_connection_ends_an_open_transaction_only() -> None:
    open_tx, idle = FakeSession(True), FakeSession(False)
    await release_connection(open_tx)  # type: ignore[arg-type]
    await release_connection(idle)  # type: ignore[arg-type]
    assert (open_tx.commits, idle.commits) == (1, 0)


async def test_fanout_is_capped_process_wide_leaving_a_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = get_settings()
    monkeypatch.setattr(cfg, "db_pool_size", 3)
    monkeypatch.setattr(cfg, "db_max_overflow", 1)
    monkeypatch.setattr(cfg, "agg_pool_reserve", 2)
    monkeypatch.setattr(concurrency, "_fanout", None)
    in_use = peak = 0

    @contextlib.asynccontextmanager
    async def factory() -> AsyncIterator[object]:
        nonlocal in_use, peak
        in_use += 1
        peak = max(peak, in_use)
        try:
            yield object()
        finally:
            in_use -= 1

    async def work(_session: object) -> int:
        await asyncio.sleep(0.01)
        return 1

    # Five "requests" fanning out at once, 4 sessions each, limit 4 per call.
    calls = [
        gather_scoped(work, work, work, work, session_factory=factory, limit=4)
        for _ in range(5)
    ]
    results = await asyncio.gather(*calls)
    assert all(r == [1, 1, 1, 1] for r in results)
    assert peak == 2  # 3 + 1 - 2 reserved for plain requests
    assert fanout_slots()._value == 2  # all slots returned
    monkeypatch.setattr(concurrency, "_fanout", None)


# --- real pool (CI Postgres) -------------------------------------------------


@pytest.fixture
async def pooled_engine() -> AsyncIterator[AsyncEngine]:
    engine = new_engine(pool_size=2, max_overflow=0, pool_timeout=2)
    try:
        await require_postgres(engine)
        yield engine
    finally:
        await engine.dispose()


async def _wait_until_released(engine: AsyncEngine) -> None:
    """Shielded closes finish shortly after a cancel: poll the pool briefly."""
    for _ in range(int(RELEASE_WAIT_SECONDS / POLL_SECONDS)):
        if not engine.pool.checkedout():  # type: ignore[attr-defined]
            return
        await asyncio.sleep(POLL_SECONDS)
    raise AssertionError(f"{engine.pool.checkedout()} connection(s) never returned")  # type: ignore[attr-defined]


async def _pool_still_serves(factory: async_sessionmaker[AsyncSession]) -> None:
    """Both pooled connections can be checked out again (pool_timeout=2 s)."""

    async def one(session: AsyncSession) -> int:
        return (await session.execute(text("SELECT 1"))).scalar_one()

    assert await gather_scoped(one, one, session_factory=factory) == [1, 1]


async def _cancel_midway(coro: Any) -> None:
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(CANCEL_AFTER_SECONDS)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.db
async def test_gather_scoped_cancelled_mid_query_returns_every_connection(
    pooled_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(pooled_engine, expire_on_commit=False)

    async def slow(session: AsyncSession) -> None:
        await session.execute(SLOW_QUERY)

    await _cancel_midway(gather_scoped(slow, slow, session_factory=factory))
    await _wait_until_released(pooled_engine)
    await _pool_still_serves(factory)


@pytest.mark.db
async def test_overview_cancelled_mid_flight_returns_every_connection(
    pooled_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(pooled_engine, expire_on_commit=False)
    runner = ScopedRunner(session_factory=factory, limit=2)
    for _ in range(3):  # the SSE producer is cancelled on every last-subscriber exit
        await _cancel_midway(overview_payload(runner, "overview"))
    await _wait_until_released(pooled_engine)
    await _pool_still_serves(factory)


@pytest.mark.db
async def test_stream_snapshot_timeout_returns_every_connection(
    pooled_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(pooled_engine, expire_on_commit=False)

    async def slow_snapshot(_audience: str) -> dict[str, Any]:
        async with factory() as session:
            await session.execute(SLOW_QUERY)
        return {}

    hub = GovernanceStreamHub(
        snapshot_fn=slow_snapshot,
        interval=60,
        bus=EventBus(dsn="postgresql://unused"),
        snapshot_timeout=CANCEL_AFTER_SECONDS,
    )
    await hub.tick("overview")
    await hub.aclose()
    await _wait_until_released(pooled_engine)
    await _pool_still_serves(factory)


@pytest.mark.db
async def test_branding_caller_cancelled_mid_load_returns_every_connection(
    pooled_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(pooled_engine, expire_on_commit=False)
    for _ in range(3):
        branding.invalidate_branding_cache()
        await _cancel_midway(branding.get_branding(factory))
    await branding.get_branding(factory)
    await _wait_until_released(pooled_engine)
    await _pool_still_serves(factory)


@pytest.mark.db
async def test_requests_that_release_before_fanning_out_never_deadlock_the_pool(
    pooled_engine: AsyncEngine,
) -> None:
    """Regression for the pool freeze: each request held its own connection
    (idle in transaction) while waiting for more for ``gather_scoped``; with
    as many requests as pool slots nobody could proceed until pool_timeout."""
    factory = async_sessionmaker(pooled_engine, expire_on_commit=False)

    async def part(session: AsyncSession) -> int:
        return (await session.execute(text("SELECT 1 FROM pg_sleep(0.1)"))).scalar_one()

    async def request() -> list[int]:
        async with factory() as db:
            await db.execute(text("SELECT 1"))  # e.g. the audience filter
            await release_connection(db)
            return await gather_scoped(part, part, session_factory=factory, limit=2)

    async with asyncio.timeout(RELEASE_WAIT_SECONDS * 2):
        results = await asyncio.gather(*(request() for _ in range(4)))
    assert results == [[1, 1]] * 4
    await _wait_until_released(pooled_engine)
