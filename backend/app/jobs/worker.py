"""Job worker runner.

Runs in-process from the FastAPI lifespan when ``JOBS_WORKER_ENABLED=true``,
or standalone as a compose service: ``python -m app.jobs.worker``.

- Claims at most ``JOBS_MAX_CONCURRENCY`` jobs at a time (SKIP LOCKED, so any
  number of workers and replicas can run side by side).
- Each handler runs under ``JOBS_LOCK_TIMEOUT_SECONDS`` so it never outlives
  its lock; locks older than that (crashed worker) are reclaimed.
- Shutdown stops claiming, then waits up to ``JOBS_SHUTDOWN_TIMEOUT_SECONDS``
  for in-flight jobs; anything still running is reclaimed later.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import uuid
from collections.abc import Awaitable
from typing import TYPE_CHECKING, TypeVar

from app.azure_insights.scheduler import SCHEDULE_CHECK_SECONDS, ensure_azure_scheduled
from app.core.config import configure_logging, get_settings
from app.core.events import get_event_bus
from app.core.logging_config import get_logger
from app.core.state import get_state_backend
from app.jobs import handlers, queue  # noqa: F401 - registers built-in handlers
from app.jobs.registry import (
    ALERTS_EVALUATE_JOB,
    CHAT_PRUNE_EMPTY_SESSIONS_JOB,
    STATE_CLEANUP_JOB,
    get_handler,
    registered_types,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = get_logger(__name__)
T = TypeVar("T")

_ERROR_BACKOFF_MAX_SECONDS = 30.0


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class JobWorker:
    """Claims and runs jobs until ``stop`` is set."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        concurrency: int | None = None,
        poll_interval: float | None = None,
        lock_timeout: float | None = None,
        shutdown_timeout: float | None = None,
        types: list[str] | None = None,
        schedule_periodic: bool = True,
        engine: AsyncEngine | None = None,
    ) -> None:
        settings = get_settings()
        self.worker_id = worker_id or default_worker_id()
        self.concurrency = max(1, concurrency or settings.jobs_max_concurrency)
        self.poll_interval = poll_interval or settings.jobs_poll_interval_seconds
        self.lock_timeout = lock_timeout or float(settings.jobs_lock_timeout_seconds)
        self.shutdown_timeout = (
            shutdown_timeout
            if shutdown_timeout is not None
            else settings.jobs_shutdown_timeout_seconds
        )
        self.cleanup_interval = float(settings.state_cleanup_interval_seconds)
        self.alerts_poll_interval = max(
            1.0, settings.alerts_evaluate_interval_seconds / 4
        )
        self._types = types
        self._schedule_periodic = schedule_periodic
        self._engine = engine
        self._inflight: set[asyncio.Task[None]] = set()
        self.processed = 0

    @property
    def types(self) -> list[str]:
        return self._types if self._types is not None else registered_types()

    async def run(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        logger.info("jobs_worker_started", worker_id=self.worker_id)
        try:
            async with asyncio.timeout(None) as drain_deadline:
                async with asyncio.TaskGroup() as tg:
                    await self._loop(stop, tg)
                    # Stop claiming; give in-flight handlers a bounded drain.
                    drain_deadline.reschedule(loop.time() + self.shutdown_timeout)
        except TimeoutError:
            logger.warning(
                "jobs_worker_drain_timeout",
                worker_id=self.worker_id,
                abandoned=len(self._inflight),
            )
        logger.info("jobs_worker_stopped", worker_id=self.worker_id)

    async def _loop(self, stop: asyncio.Event, tg: asyncio.TaskGroup) -> None:
        loop = asyncio.get_running_loop()
        next_reclaim = 0.0
        next_schedule = 0.0
        next_alerts = 0.0
        next_azure = 0.0
        error_delay = self.poll_interval
        while not stop.is_set():
            claimed = 0
            try:
                now = loop.time()
                if now >= next_reclaim:
                    await queue.reclaim_stale(self.lock_timeout, engine=self._engine)
                    next_reclaim = now + max(1.0, min(60.0, self.lock_timeout / 2))
                if self._schedule_periodic and now >= next_schedule:
                    await self._schedule_cleanup()
                    await self._schedule_session_prune()
                    next_schedule = now + self.cleanup_interval
                if self._schedule_periodic and now >= next_alerts:
                    # Polled at a quarter of the interval; put_once spaces runs.
                    await self._schedule_alerts()
                    next_alerts = now + self.alerts_poll_interval
                if self._schedule_periodic and now >= next_azure:
                    # Self-heal: a lost azure.* job is re-queued (dedupe no-op).
                    await self._schedule_azure()
                    next_azure = now + SCHEDULE_CHECK_SECONDS
                free = self.concurrency - len(self._inflight)
                if free > 0:
                    jobs = await queue.claim(
                        free, self.worker_id, self.types, engine=self._engine
                    )
                    claimed = len(jobs)
                    for job in jobs:
                        task = tg.create_task(self._execute(job), name=f"job-{job.id}")
                        self._inflight.add(task)
                        task.add_done_callback(self._inflight.discard)
                error_delay = self.poll_interval
            except Exception as exc:
                logger.warning(
                    "jobs_worker_poll_failed", worker_id=self.worker_id, error=str(exc)
                )
                error_delay = min(error_delay * 2, _ERROR_BACKOFF_MAX_SECONDS)
                await _wait(stop, error_delay)
                continue
            if claimed == 0 or len(self._inflight) >= self.concurrency:
                await _wait(stop, self.poll_interval)

    async def _schedule_cleanup(self) -> None:
        if get_handler(STATE_CLEANUP_JOB) is None:
            return
        await queue.enqueue(
            STATE_CLEANUP_JOB, {}, dedupe_key=STATE_CLEANUP_JOB, engine=self._engine
        )

    async def _schedule_session_prune(self) -> None:
        """Queue ``chat.prune_empty_sessions`` at most once per day (all workers).

        Checked on the cleanup cadence; the shared ``put_once`` claim spaces
        runs by ``session_prune_interval_seconds`` across workers and restarts.
        """
        settings = get_settings()
        if (
            not settings.session_prune_enabled
            or get_handler(CHAT_PRUNE_EMPTY_SESSIONS_JOB) is None
        ):
            return
        claimed = await get_state_backend().put_once(
            CHAT_PRUNE_EMPTY_SESSIONS_JOB,
            {"worker": self.worker_id},
            ttl=float(settings.session_prune_interval_seconds),
        )
        if claimed:
            await queue.enqueue(
                CHAT_PRUNE_EMPTY_SESSIONS_JOB,
                {},
                dedupe_key=CHAT_PRUNE_EMPTY_SESSIONS_JOB,
                engine=self._engine,
            )

    async def _schedule_alerts(self) -> None:
        """Queue ``alerts.evaluate`` at most once per interval across all workers.

        The shared-state ``put_once`` claim (a Postgres row with a TTL) picks a
        single scheduler per interval; the queue's dedupe key keeps at most one
        evaluation pending.
        """
        settings = get_settings()
        if not settings.alerts_enabled or get_handler(ALERTS_EVALUATE_JOB) is None:
            return
        claimed = await get_state_backend().put_once(
            ALERTS_EVALUATE_JOB,
            {"worker": self.worker_id},
            ttl=float(settings.alerts_evaluate_interval_seconds),
        )
        if claimed:
            await queue.enqueue(
                ALERTS_EVALUATE_JOB,
                {},
                dedupe_key=ALERTS_EVALUATE_JOB,
                engine=self._engine,
            )

    async def _schedule_azure(self) -> None:
        if get_settings().azure_insights_configured:
            await ensure_azure_scheduled(engine=self._engine)

    async def _execute(self, job: queue.Job) -> None:
        handler = get_handler(job.type)
        try:
            if handler is None:
                raise LookupError(f"No handler registered for '{job.type}'")
            async with asyncio.timeout(self.lock_timeout):
                await handler(job.payload)
        except asyncio.CancelledError:
            # Shutdown: the lock expires and another worker reclaims the job.
            raise
        except queue.JobDeferredError as deferred:
            # Not a failure (e.g. budget exhausted): re-queue without
            # consuming an attempt.
            await self._safe(
                queue.defer(
                    job, deferred.run_after, deferred.reason, engine=self._engine
                )
            )
            logger.info(
                "job_deferred",
                job_id=str(job.id),
                job_type=job.type,
                reason=deferred.reason,
                run_after=deferred.run_after.isoformat(),
            )
            return
        except Exception as exc:
            status = await self._safe(
                queue.fail(job, f"{type(exc).__name__}: {exc}", engine=self._engine)
            )
            logger.warning(
                "job_failed",
                job_id=str(job.id),
                job_type=job.type,
                attempts=job.attempts,
                status=status,
                error=str(exc),
            )
            return
        await self._safe(queue.complete(job.id, engine=self._engine))
        self.processed += 1

    async def _safe(self, coro: Awaitable[T]) -> T | None:
        try:
            return await coro
        except Exception as exc:
            logger.error("job_state_update_failed", error=str(exc))
            return None


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


async def _main() -> None:
    configure_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    bus = get_event_bus()
    await bus.start()
    try:
        await JobWorker().run(stop)
    finally:
        await bus.stop()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
