"""Precomputed dashboard aggregates (app.dashboard.snapshots)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.cache import dashboard_cache
from app.core.config import get_settings
from app.dashboard import snapshots
from app.models.db_models import DashboardSnapshot
from tests.conftest import shared_session_runner

KIND = "test.kind"


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict]]:
    monkeypatch.setattr(get_settings(), "dashboard_snapshots_persist", True)
    seen: list[dict] = []

    async def build(_gather, params):
        seen.append(params)
        return {"n": len(seen), "at": datetime(2026, 1, 1, tzinfo=UTC), **params}

    original = dict(snapshots._KINDS)  # noqa: SLF001
    snapshots._KINDS.clear()  # noqa: SLF001 - only this kind in the refresher
    snapshots.register(KIND, build, [{"default": True}])
    yield seen
    snapshots._KINDS.clear()  # noqa: SLF001
    snapshots._KINDS.update(original)  # noqa: SLF001


async def test_first_read_computes_and_stores_then_reads_the_row(db_session, calls):
    runner = shared_session_runner(db_session)
    first = await snapshots.get_snapshot(KIND, {"a": 1}, runner.gather)
    assert first == {"n": 1, "at": "2026-01-01T00:00:00+00:00", "a": 1}
    row = await db_session.get(
        DashboardSnapshot, snapshots.snapshot_key(KIND, {"a": 1})
    )
    assert row is not None and row.payload == first and row.kind == KIND

    # A new process (empty memory cache) answers from the row, no rebuild.
    dashboard_cache.clear()
    assert await snapshots.get_snapshot(KIND, {"a": 1}, runner.gather) == first
    assert len(calls) == 1
    # Other parameters are another snapshot.
    await snapshots.get_snapshot(KIND, {"a": 2}, runner.gather)
    assert len(calls) == 2


async def test_refresher_backfills_defaults_and_refreshes_watched_rows(
    db_session, calls
):
    runner = shared_session_runner(db_session)
    refresher = snapshots.SnapshotRefresher(gather=runner.gather)
    built = await refresher._cycle()  # noqa: SLF001 - the lock needs its own connection
    assert built >= 1
    assert {"default": True} in calls

    await snapshots.get_snapshot(KIND, {"a": 1}, runner.gather)
    key = snapshots.snapshot_key(KIND, {"a": 1})
    # Make it old: a watched snapshot is recomputed on the next cycle.
    await db_session.execute(
        update(DashboardSnapshot)
        .where(DashboardSnapshot.key == key)
        .values(computed_at=datetime.now(UTC) - timedelta(hours=2))
    )
    before = len(calls)
    await refresher._cycle()  # noqa: SLF001
    assert len(calls) == before + 1
    fresh = await db_session.scalar(
        select(DashboardSnapshot.computed_at).where(DashboardSnapshot.key == key)
    )
    assert datetime.now(UTC) - fresh < timedelta(minutes=1)


async def test_delete_snapshots_forces_a_rebuild(db_session, calls):
    runner = shared_session_runner(db_session)
    await snapshots.get_snapshot(KIND, {"a": 1}, runner.gather)
    await snapshots.delete_snapshots(db_session, "test.")
    await snapshots.get_snapshot(KIND, {"a": 1}, runner.gather)
    assert len(calls) == 2


def test_keys_are_canonical() -> None:
    assert snapshots.snapshot_key(KIND, {"a": 1, "b": 2}) == snapshots.snapshot_key(
        KIND, {"b": 2, "a": 1}
    )
    assert snapshots.snapshot_key(KIND, {"a": 1}) != snapshots.snapshot_key(
        "other", {"a": 1}
    )
