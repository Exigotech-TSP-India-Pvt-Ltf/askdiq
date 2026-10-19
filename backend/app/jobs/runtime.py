"""Lifespan wiring for the platform background services.

Starts the LISTEN/NOTIFY event bus (cross-worker cache invalidation) and,
when ``JOBS_WORKER_ENABLED=true``, the in-process job worker.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.alerts.sampler import AlertSampler
from app.branding.service import BRANDING_CACHE_ID, invalidate_branding_cache
from app.core.cache import aggregation_cache, dashboard_cache
from app.core.config import Settings, get_settings
from app.core.events import (
    TOPIC_CACHE_INVALIDATE,
    TOPIC_SNAPSHOT_UPDATED,
    Event,
    EventBus,
    get_event_bus,
)
from app.core.logging_config import get_logger
from app.dashboard.snapshots import SnapshotRefresher, on_snapshot_updated
from app.governance.audience import invalidate_audience_cache
from app.governance.metrics_sampler import MetricsSampler
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


def log_alert_delivery_status(settings: Settings) -> None:
    """One startup line saying whether alerts are emailed (never logs secrets)."""
    if not settings.alerts_enabled:
        logger.info("alerts_disabled", reason="ALERTS_ENABLED=false")
    elif settings.alert_email_configured:
        logger.info(
            "alerts_email_delivery_on",
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            recipients=len(settings.alert_email_recipients),
        )
    else:
        logger.info(
            "alerts_email_delivery_off",
            reason="SMTP_HOST, ALERT_EMAIL_FROM or ALERT_EMAIL_TO not set; "
            "alerts are stored and shown in the admin console only",
        )


# Upper bound on waiting for the job worker to stop cleanly at shutdown.
_WORKER_STOP_TIMEOUT_SECONDS = 5.0


class PlatformServices:
    """Owns the event-bus listener and the optional job worker task."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus or get_event_bus()
        self._stop = asyncio.Event()
        self._worker_task: asyncio.Task[None] | None = None
        self._sampler_task: asyncio.Task[None] | None = None
        self._snapshot_task: asyncio.Task[None] | None = None
        self._metrics_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        bus = self.bus

        def _on_invalidate(event: Event) -> None:
            # The publishing process already invalidated its own caches.
            if event.origin != bus.instance_id:
                _invalidate_local_caches(event)

        bus.on(TOPIC_CACHE_INVALIDATE, _on_invalidate)
        # Snapshots recomputed elsewhere replace this process's memory copy.
        bus.on(
            TOPIC_SNAPSHOT_UPDATED,
            lambda event: on_snapshot_updated(event, bus.instance_id),
        )
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
        log_alert_delivery_status(cfg)
        if cfg.alerts_enabled:
            self._sampler_task = asyncio.create_task(
                AlertSampler(bus=bus).run(self._stop), name="alerts-sampler"
            )
        if cfg.metrics_enabled:
            self._metrics_task = asyncio.create_task(
                MetricsSampler().run(self._stop), name="metrics-sampler"
            )

    async def aclose(self) -> None:
        self._stop.set()
        # Samplers loop until cancelled; the worker and the snapshot refresher
        # get a moment to finish their current step first.
        for attr in ("_sampler_task", "_metrics_task"):
            sampler = getattr(self, attr)
            setattr(self, attr, None)
            if sampler is not None:
                sampler.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await sampler
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
