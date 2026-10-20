"""Job queue: enqueue/claim with SKIP LOCKED, retries, worker."""

from __future__ import annotations

import asyncio
import os
import random
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.jobs import queue
from app.jobs.registry import (
    STATE_CLEANUP_JOB,
    get_handler,
    job_handler,
    registered_types,
    unregister,
)
from app.jobs.worker import JobWorker
from tests.pg_helpers import new_engine, require_postgres

settings = get_settings()
BACKEND_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Pure logic (no DB)
# ---------------------------------------------------------------------------


def test_backoff_is_exponential_with_jitter_and_capped():
    rng = random.Random(7)  # noqa: S311 - deterministic jitter in a test
    assert queue.compute_backoff(1, base=2, cap=600, rng=rng) == 2
    for attempts in range(2, 8):
        delay = queue.compute_backoff(attempts, base=2, cap=600, rng=rng)
        assert 2 <= delay <= 2 * 2 ** (attempts - 1)
    assert queue.compute_backoff(30, base=2, cap=600, rng=rng) <= 600
    spread = {round(queue.compute_backoff(5, base=2, cap=600), 6) for _ in range(20)}
    assert len(spread) > 1, "jitter must spread retries"


def test_backoff_timestamps_follow_the_app_clock(frozen_time):
    start = queue.utcnow()
    frozen_time.advance(30)
    assert (queue.utcnow() - start).total_seconds() == 30


def test_handler_registry_rejects_duplicates():
    job_type = f"test.registry.{uuid.uuid4().hex}"

    @job_handler(job_type)
    async def _handler(_payload: dict) -> None:
        return None

    try:
        assert get_handler(job_type) is _handler
        assert job_type in registered_types()
        with pytest.raises(ValueError):

            @job_handler(job_type)
            async def _other(_payload: dict) -> None:
                return None

    finally:
        unregister(job_type)


def test_builtin_cleanup_handler_is_registered():
    from app.jobs import handlers  # noqa: F401

    assert get_handler(STATE_CLEANUP_JOB) is not None


# ---------------------------------------------------------------------------
# Postgres-backed queue (runs in CI)
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine():
    eng = new_engine(pool_size=10, max_overflow=5)
    await require_postgres(eng)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def job_type(engine):
    name = f"test.{uuid.uuid4().hex}"
    yield name
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM jobs WHERE type = :t"), {"t": name})


async def _job_row(engine, job_id):
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}
            )
        ).one()


async def test_four_concurrent_claimers_run_each_job_exactly_once(engine, job_type):
    for i in range(100):
        await queue.enqueue(job_type, {"n": i}, engine=engine)

    engines = [new_engine() for _ in range(4)]
    runs: list[uuid.UUID] = []

    async def claimer(index: int) -> None:
        while True:
            jobs = await queue.claim(
                7, f"claimer-{index}", [job_type], engine=engines[index]
            )
            if not jobs:
                return
            for job in jobs:
                runs.append(job.id)
                await asyncio.sleep(0)
                await queue.complete(job.id, engine=engines[index])

    try:
        await asyncio.gather(*(claimer(i) for i in range(4)))
    finally:
        for eng in engines:
            await eng.dispose()

    assert len(runs) == 100
    assert len(set(runs)) == 100
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT status, attempts FROM jobs WHERE type = :t"),
                {"t": job_type},
            )
        ).all()
    assert len(rows) == 100
    assert {(r.status, r.attempts) for r in rows} == {("done", 1)}


async def test_dedupe_key_blocks_a_second_pending_copy(engine, job_type):
    key = f"dedupe-{uuid.uuid4().hex}"
    first = await queue.enqueue(job_type, {}, dedupe_key=key, engine=engine)
    second = await queue.enqueue(job_type, {}, dedupe_key=key, engine=engine)
    assert first is not None
    assert second is None

    [job] = await queue.claim(1, "w", [job_type], engine=engine)
    assert await queue.enqueue(job_type, {}, dedupe_key=key, engine=engine) is None
    await queue.complete(job.id, engine=engine)
    assert await queue.enqueue(job_type, {}, dedupe_key=key, engine=engine)


async def test_failed_job_is_retried_only_after_its_backoff(
    engine, job_type, monkeypatch
):
    monkeypatch.setattr(settings, "jobs_backoff_base_seconds", 10.0)
    monkeypatch.setattr(settings, "jobs_backoff_max_seconds", 10.0)
    t0 = queue.utcnow()
    job_id = await queue.enqueue(job_type, {"x": 1}, engine=engine, run_after=t0)

    [job] = await queue.claim(1, "w1", [job_type], now=t0, engine=engine)
    assert job.id == job_id and job.attempts == 1 and job.payload == {"x": 1}
    assert await queue.fail(job, "ValueError: boom", now=t0, engine=engine) == "queued"

    row = await _job_row(engine, job_id)
    assert row.status == "queued"
    assert row.last_error == "ValueError: boom"
    assert row.run_after == t0 + timedelta(seconds=10)

    early = t0 + timedelta(seconds=5)
    assert await queue.claim(1, "w2", [job_type], now=early, engine=engine) == []
    late = t0 + timedelta(seconds=11)
    [retry] = await queue.claim(1, "w2", [job_type], now=late, engine=engine)
    assert retry.id == job_id and retry.attempts == 2


async def test_job_becomes_dead_after_max_attempts(engine, job_type):
    job_id = await queue.enqueue(job_type, {}, max_attempts=2, engine=engine)
    now = queue.utcnow()
    for attempt in (1, 2):
        [job] = await queue.claim(1, "w", [job_type], now=now, engine=engine)
        assert job.attempts == attempt
        status = await queue.fail(job, f"err {attempt}", now=now, engine=engine)
        now += timedelta(hours=1)
    assert status == "dead"
    row = await _job_row(engine, job_id)
    assert row.status == "dead" and row.finished_at is not None
    assert await queue.claim(1, "w", [job_type], now=now, engine=engine) == []


async def test_crashed_workers_job_is_reclaimed(engine, job_type):
    job_id = await queue.enqueue(job_type, {}, engine=engine)
    [job] = await queue.claim(1, "crashed-worker", [job_type], engine=engine)
    assert job.id == job_id

    await queue.reclaim_stale(300, engine=engine)
    assert (await _job_row(engine, job_id)).status == "running"
    later = queue.utcnow() + timedelta(seconds=301)
    assert await queue.reclaim_stale(300, now=later, engine=engine) >= 1

    row = await _job_row(engine, job_id)
    assert row.status == "queued" and row.locked_by is None
    assert "crashed-worker" in row.last_error
    [again] = await queue.claim(1, "healthy", [job_type], now=later, engine=engine)
    assert again.id == job_id and again.attempts == 2


async def _run_worker_until(worker: JobWorker, predicate, limit_s: float = 10.0):
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    try:
        deadline = asyncio.get_running_loop().time() + limit_s
        while not await predicate():
            assert asyncio.get_running_loop().time() < deadline, "worker timed out"
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)


async def test_worker_records_handler_error_and_retries(engine, job_type, monkeypatch):
    monkeypatch.setattr(settings, "jobs_backoff_base_seconds", 0.01)
    monkeypatch.setattr(settings, "jobs_backoff_max_seconds", 0.01)
    calls: list[dict] = []

    @job_handler(job_type)
    async def _flaky(payload: dict) -> None:
        calls.append(payload)
        if len(calls) == 1:
            raise RuntimeError("boom on first try")

    job_id = await queue.enqueue(job_type, {"k": "v"}, engine=engine)
    worker = JobWorker(
        types=[job_type], poll_interval=0.05, schedule_periodic=False, engine=engine
    )

    async def _done() -> bool:
        return (await _job_row(engine, job_id)).status == "done"

    try:
        await _run_worker_until(worker, _done)
    finally:
        unregister(job_type)

    row = await _job_row(engine, job_id)
    assert row.attempts == 2
    assert "boom on first try" in row.last_error
    assert calls == [{"k": "v"}, {"k": "v"}]


async def test_worker_respects_max_concurrency_and_drains_on_shutdown(engine, job_type):
    active = 0
    peak = 0
    finished = 0

    @job_handler(job_type)
    async def _slow(_payload: dict) -> None:
        nonlocal active, peak, finished
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.2)
        active -= 1
        finished += 1

    for i in range(6):
        await queue.enqueue(job_type, {"i": i}, engine=engine)
    worker = JobWorker(
        types=[job_type],
        concurrency=2,
        poll_interval=0.02,
        shutdown_timeout=5,
        schedule_periodic=False,
        engine=engine,
    )

    async def _some_running() -> bool:
        return active > 0

    try:
        # Stop while jobs are in flight: shutdown must wait for them.
        await _run_worker_until(worker, _some_running)
    finally:
        unregister(job_type)

    assert peak <= 2
    assert active == 0
    async with engine.connect() as conn:
        running = await conn.scalar(
            text("SELECT count(*) FROM jobs WHERE type = :t AND status = 'running'"),
            {"t": job_type},
        )
    assert running == 0
    assert finished >= 1


async def test_cleanup_job_removes_expired_shared_state(engine):
    from app.core.state import PostgresState
    from app.jobs import handlers

    backend = PostgresState(engine)
    await backend.put_once(f"cleanup-{uuid.uuid4()}", {"v": 1}, ttl=0.01)
    await asyncio.sleep(0.1)
    # Only rows already expired when cleanup runs: rows other tests created
    # with short TTLs can expire between the cleanup and this check.
    async with engine.connect() as conn:
        cutoff = await conn.scalar(text("SELECT now()"))
    await handlers.state_cleanup_task({})
    async with engine.connect() as conn:
        expired = await conn.scalar(
            text("SELECT count(*) FROM oidc_states WHERE expires_at <= :cutoff"),
            {"cutoff": cutoff},
        )
    assert expired == 0


async def test_periodic_cleanup_is_enqueued_once_across_workers(engine):
    from app.jobs import handlers  # noqa: F401

    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM jobs WHERE type = :t"), {"t": STATE_CLEANUP_JOB}
        )
    workers = [
        JobWorker(types=[], schedule_periodic=True, engine=engine) for _ in range(3)
    ]
    for worker in workers:
        await worker._schedule_cleanup()
    async with engine.begin() as conn:
        pending = await conn.scalar(
            text(
                "SELECT count(*) FROM jobs WHERE type = :t "
                "AND status IN ('queued', 'running')"
            ),
            {"t": STATE_CLEANUP_JOB},
        )
        await conn.execute(
            text("DELETE FROM jobs WHERE type = :t"), {"t": STATE_CLEANUP_JOB}
        )
    assert pending == 1


def _alembic(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


async def test_migrations_upgrade_downgrade_upgrade(engine):
    """Both migrations round-trip on the test database."""
    await engine.dispose()
    heads = await asyncio.to_thread(_alembic, "heads")
    assert heads.count("(head)") == 1
    await asyncio.to_thread(_alembic, "downgrade", "d6e7f8a9b0c1")
    async with engine.connect() as conn:
        gone = await conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_name "
                "IN ('jobs', 'rate_limit_buckets', 'oidc_states')"
            )
        )
    await engine.dispose()
    assert gone == 0
    await asyncio.to_thread(_alembic, "upgrade", "head")
    async with engine.connect() as conn:
        persistence = dict(
            (
                await conn.execute(
                    text(
                        "SELECT relname, relpersistence::text FROM pg_class WHERE relname "
                        "IN ('jobs', 'rate_limit_buckets', 'oidc_states')"
                    )
                )
            ).all()
        )
    assert persistence == {"jobs": "p", "rate_limit_buckets": "u", "oidc_states": "p"}
