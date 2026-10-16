"""Postgres job queue: enqueue, claim (``FOR UPDATE SKIP LOCKED``), retry.

All timestamps come from the application clock (``time.time``) so retry
backoff is deterministic under test and identical on every worker.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import String

from app.core import database
from app.core.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_DEAD = "dead"

_MAX_ERROR_CHARS = 4000


@dataclass(frozen=True, slots=True)
class Job:
    id: uuid.UUID
    type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


def utcnow() -> datetime:
    return datetime.fromtimestamp(time.time(), tz=UTC)


def _engine(engine: AsyncEngine | None) -> AsyncEngine:
    return engine if engine is not None else database.engine


def compute_backoff(
    attempts: int,
    base: float | None = None,
    cap: float | None = None,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with jitter: uniform(base, min(cap, base * 2^(n-1)))."""
    settings = get_settings()
    base = settings.jobs_backoff_base_seconds if base is None else base
    cap = settings.jobs_backoff_max_seconds if cap is None else cap
    ceiling = min(cap, base * (2 ** max(0, attempts - 1)))
    if ceiling <= base:
        return ceiling
    # Jitter only spreads retries; it is not security-sensitive.
    return (rng or random).uniform(base, ceiling)  # nosec B311


_ENQUEUE_SQL = text(
    """
    INSERT INTO jobs (type, payload, dedupe_key, run_after, max_attempts)
    VALUES (:type, CAST(:payload AS jsonb), :dedupe_key, :run_after, :max_attempts)
    ON CONFLICT (dedupe_key)
        WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running')
        DO NOTHING
    RETURNING id
    """
)


async def enqueue(
    job_type: str,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int | None = None,
    *,
    engine: AsyncEngine | None = None,
) -> uuid.UUID | None:
    """Queue a job; returns its id, or None when ``dedupe_key`` is already pending."""
    params = {
        "type": job_type,
        "payload": json.dumps(payload or {}),
        "dedupe_key": dedupe_key,
        "run_after": run_after or utcnow(),
        "max_attempts": max_attempts or get_settings().jobs_max_attempts,
    }
    async with _engine(engine).begin() as conn:
        return await conn.scalar(_ENQUEUE_SQL, params)


_CLAIM_SQL = text(
    """
    UPDATE jobs SET status = 'running', locked_by = :worker, locked_at = :now,
                    attempts = attempts + 1
    WHERE id IN (
        SELECT id FROM jobs
        WHERE status = 'queued' AND run_after <= :now AND type = ANY(:types)
        ORDER BY run_after, created_at
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id, type, payload, attempts, max_attempts
    """
).bindparams(bindparam("types", type_=ARRAY(String())))


def _row_to_job(row: Any) -> Job:
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    return Job(
        id=row.id,
        type=row.type,
        payload=payload,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
    )


async def claim(
    batch: int,
    worker_id: str,
    types: list[str],
    *,
    now: datetime | None = None,
    engine: AsyncEngine | None = None,
) -> list[Job]:
    """Atomically lock up to ``batch`` due jobs of ``types`` for ``worker_id``."""
    if batch <= 0 or not types:
        return []
    params = {
        "worker": worker_id,
        "now": now or utcnow(),
        "types": list(types),
        "limit": batch,
    }
    async with _engine(engine).begin() as conn:
        rows = (await conn.execute(_CLAIM_SQL, params)).all()
    return [_row_to_job(row) for row in rows]


async def complete(
    job_id: uuid.UUID,
    *,
    now: datetime | None = None,
    engine: AsyncEngine | None = None,
) -> None:
    async with _engine(engine).begin() as conn:
        await conn.execute(
            text(
                "UPDATE jobs SET status = 'done', finished_at = :now, "
                "locked_by = NULL, locked_at = NULL WHERE id = :id"
            ),
            {"id": job_id, "now": now or utcnow()},
        )


async def fail(
    job: Job,
    error: str,
    *,
    now: datetime | None = None,
    engine: AsyncEngine | None = None,
) -> str:
    """Record a failure: re-queue with backoff, or mark ``dead`` when exhausted."""
    now = now or utcnow()
    if job.attempts >= job.max_attempts:
        status, run_after, finished = STATUS_DEAD, now, now
    else:
        delay = compute_backoff(job.attempts)
        status, run_after, finished = (
            STATUS_QUEUED,
            now + timedelta(seconds=delay),
            None,
        )
    async with _engine(engine).begin() as conn:
        await conn.execute(
            text(
                "UPDATE jobs SET status = :status, run_after = :run_after, "
                "finished_at = :finished, last_error = :error, "
                "locked_by = NULL, locked_at = NULL WHERE id = :id"
            ),
            {
                "id": job.id,
                "status": status,
                "run_after": run_after,
                "finished": finished,
                "error": error[:_MAX_ERROR_CHARS],
            },
        )
    return status


_RECLAIM_SQL = text(
    """
    UPDATE jobs SET
        status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END,
        finished_at = CASE
            WHEN attempts >= max_attempts THEN CAST(:now AS timestamptz)
        END,
        last_error = 'lock expired (worker ' || COALESCE(locked_by, 'unknown') || ')',
        locked_by = NULL,
        locked_at = NULL,
        run_after = CAST(:now AS timestamptz)
    WHERE status = 'running' AND locked_at < CAST(:cutoff AS timestamptz)
    RETURNING id
    """
)


async def reclaim_stale(
    lock_timeout_s: float | None = None,
    *,
    now: datetime | None = None,
    engine: AsyncEngine | None = None,
) -> int:
    """Re-queue jobs whose worker died (lock older than the timeout)."""
    now = now or utcnow()
    timeout = (
        get_settings().jobs_lock_timeout_seconds
        if lock_timeout_s is None
        else lock_timeout_s
    )
    params = {"now": now, "cutoff": now - timedelta(seconds=timeout)}
    async with _engine(engine).begin() as conn:
        rows = (await conn.execute(_RECLAIM_SQL, params)).all()
    return len(rows)


async def prune_finished(
    retention_days: int | None = None,
    *,
    now: datetime | None = None,
    engine: AsyncEngine | None = None,
) -> int:
    """Delete ``done`` jobs older than the retention (``dead`` ones are kept)."""
    now = now or utcnow()
    days = (
        get_settings().jobs_retention_days if retention_days is None else retention_days
    )
    async with _engine(engine).begin() as conn:
        result = await conn.execute(
            text("DELETE FROM jobs WHERE status = 'done' AND finished_at < :cutoff"),
            {"cutoff": now - timedelta(days=days)},
        )
    return int(result.rowcount or 0)
