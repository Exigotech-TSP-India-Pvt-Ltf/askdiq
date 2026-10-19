"""Lifespan wiring for the platform background services.

Starts the LISTEN/NOTIFY event bus (cross-worker cache invalidation) and,
when ``JOBS_WORKER_ENABLED=true``, the in-process job worker.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.branding.service import BRANDING_CACHE_ID, invalidate_branding_cache
from app.core.cache import aggregation_cache, dashboard_cache
from app.core.config import get_settings
from app.core.events import TOPIC_CACHE_INVALIDATE, Event, EventBus, get_event_bus
from app.core.logging_config import get_logger
from app.dashboard.snapshots import SnapshotRefresher
from app.governance.audience import invalidate_audience_cache
from app.jobs.worker import JobWorker
from app.model_settings.service import (
    MODEL_SETTINGS_CACHE_ID,
    invalidate_model_settings_cache,
)

logger = get_logger(__name__)

AUDIENCE_CACHE_ID = "audience"


def _invalidate_local_caches(event: Event) -> None:
    if event.id == AUDIENCE_CACHE_ID:
        invalidate_audience_cache()
        return
    if event.id == BRANDING_CACHE_ID:
        invalidate_branding_cache()
        return
    if event.id == MODEL_SETTINGS_CACHE_ID:
        invalidate_model_settings_cache()
        return
    aggregation_cache.invalidate_prefix(event.id)
    dashboard_cache.invalidate_prefix(event.id)


# Upper bound on waiting for the job worker to stop cleanly at shutdown.
_WORKER_STOP_TIMEOUT_SECONDS = 5.0


class PlatformServices:
    """Owns the event-bus listener and the optional job worker task."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus or get_event_bus()
        self._stop = asyncio.Event()
        self._worker_task: asyncio.Task[None] | None = None
        self._snapshot_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        bus = self.bus

        def _on_invalidate(event: Event) -> None:
            # The publishing process already invalidated its own caches.
            if event.origin != bus.instance_id:
                _invalidate_local_caches(event)

        bus.on(TOPIC_CACHE_INVALIDATE, _on_invalidate)
        await bus.start()
        cfg = get_settings()
        if cfg.jobs_worker_enabled:
            self._worker_task = asyncio.create_task(
                JobWorker().run(self._stop), name="jobs-worker"
            )
            if cfg.dashboard_snapshot_refresh_seconds > 0:
                # Precomputed dashboards: backfills the defaults at startup,
                # then keeps the watched ones fresh (one worker, advisory lock).
                self._snapshot_task = asyncio.create_task(
                    SnapshotRefresher().run(self._stop), name="dashboard-snapshots"
                )

    async def aclose(self) -> None:
        self._stop.set()
        for task in (self._snapshot_task, self._worker_task):
            if task is not None:
                await self._stop_task(task)
        self._snapshot_task = self._worker_task = None
        await self.bus.stop()

    @staticmethod
    async def _stop_task(task: asyncio.Task[None]) -> None:
        # Give the task a moment to finish its current step, then cancel it.
        # A cancelled task is a normal stop (reload/shutdown), not an error.
        done, _ = await asyncio.wait({task}, timeout=_WORKER_STOP_TIMEOUT_SECONDS)
        if not done:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def start_platform_services() -> PlatformServices:
    services = PlatformServices()
    await services.start()
    return services
