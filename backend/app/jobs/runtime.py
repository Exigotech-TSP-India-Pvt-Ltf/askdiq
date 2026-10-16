"""Lifespan wiring for the platform background services.

Starts the LISTEN/NOTIFY event bus (cross-worker cache invalidation) and,
when ``JOBS_WORKER_ENABLED=true``, the in-process job worker.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.cache import aggregation_cache
from app.core.config import get_settings
from app.core.events import TOPIC_CACHE_INVALIDATE, Event, EventBus, get_event_bus
from app.core.logging_config import get_logger
from app.governance.audience import invalidate_audience_cache
from app.jobs.worker import JobWorker

logger = get_logger(__name__)

AUDIENCE_CACHE_ID = "audience"


def _invalidate_local_caches(event: Event) -> None:
    if event.id == AUDIENCE_CACHE_ID:
        invalidate_audience_cache()
        return
    aggregation_cache.invalidate_prefix(event.id)


class PlatformServices:
    """Owns the event-bus listener and the optional job worker task."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus or get_event_bus()
        self._stop = asyncio.Event()
        self._worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        bus = self.bus

        def _on_invalidate(event: Event) -> None:
            # The publishing process already invalidated its own caches.
            if event.origin != bus.instance_id:
                _invalidate_local_caches(event)

        bus.on(TOPIC_CACHE_INVALIDATE, _on_invalidate)
        await bus.start()
        if get_settings().jobs_worker_enabled:
            self._worker_task = asyncio.create_task(
                JobWorker().run(self._stop), name="jobs-worker"
            )

    async def aclose(self) -> None:
        self._stop.set()
        if self._worker_task is not None:
            with contextlib.suppress(Exception):
                await self._worker_task
            self._worker_task = None
        await self.bus.stop()


async def start_platform_services() -> PlatformServices:
    services = PlatformServices()
    await services.start()
    return services
