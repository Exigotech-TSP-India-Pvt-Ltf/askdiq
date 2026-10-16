"""Live Governance stream: one shared producer per worker (B4.2.5, §13.6).

``GovernanceStreamHub`` runs at most one producer task per audience in each
process, no matter how many SSE clients are connected. Every
``GOVERNANCE_STREAM_INTERVAL_SECONDS`` (or immediately when an incident
``NOTIFY`` arrives on the event bus) the producer computes one overview
snapshot and fans out, in memory, to every subscriber:

- ``snapshot``   the full overview payload (first tick / on connect),
- ``health``     when the Healthy / Degraded / Incident status changes,
- ``incident``   for each CRITICAL / ALERT row not seen in the last tick,
- ``monitoring`` the KPI values that changed since the last tick.

Writers publish ``TOPIC_GOVERNANCE_INCIDENT`` through ``app.core.events``
(Postgres NOTIFY), so producers on every replica wake up for new incidents.
DB load is therefore O(audiences x workers), never O(clients).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.core.concurrency import ScopedRunner
from app.core.config import get_settings
from app.core.events import TOPIC_GOVERNANCE_INCIDENT, EventBus, get_event_bus
from app.core.logging_config import get_logger
from app.core.sse import SSEEvent
from app.governance.overview import overview_payload

logger = get_logger(__name__)

SnapshotFn = Callable[[str], Awaitable[dict[str, Any]]]
_QUEUE_SIZE = 100


async def _default_snapshot(audience: str) -> dict[str, Any]:
    return await overview_payload(ScopedRunner(), audience)


def diff_events(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> list[SSEEvent]:
    """Events describing what changed between two overview snapshots."""
    if previous is None:
        return [SSEEvent(data=current, event="snapshot")]
    events: list[SSEEvent] = []
    old_status = previous["health"]["status"]
    new_status = current["health"]["status"]
    if old_status != new_status:
        events.append(
            SSEEvent(
                data={
                    "status": new_status,
                    "previous": old_status,
                    "health": current["health"],
                },
                event="health",
            )
        )
    seen = {item["id"] for item in previous.get("incidents", [])}
    events.extend(
        SSEEvent(data=item, event="incident", id=item["id"])
        for item in reversed(current.get("incidents", []))
        if item["id"] not in seen
    )
    old_kpis = previous.get("kpis", {})
    changed = {
        name: kpi["value"]
        for name, kpi in current.get("kpis", {}).items()
        if old_kpis.get(name, {}).get("value") != kpi["value"]
    }
    if changed:
        events.append(SSEEvent(data={"kpis": changed}, event="monitoring"))
    return events


class GovernanceStreamHub:
    """Per-process fan-out of governance snapshots to SSE subscribers."""

    def __init__(
        self,
        snapshot_fn: SnapshotFn | None = None,
        interval: float | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._snapshot_fn = snapshot_fn or _default_snapshot
        self._interval = (
            interval
            if interval is not None
            else get_settings().governance_stream_interval_seconds
        )
        self._bus = bus
        self._subscribers: dict[str, set[asyncio.Queue[SSEEvent]]] = defaultdict(set)
        self._producers: dict[str, asyncio.Task[None]] = {}
        self._latest: dict[str, dict[str, Any]] = {}
        self.ticks: dict[str, int] = defaultdict(int)

    # -- introspection (tests, metrics) -------------------------------------

    def subscriber_count(self, audience: str) -> int:
        return len(self._subscribers.get(audience, ()))

    def producer_count(self) -> int:
        return sum(1 for task in self._producers.values() if not task.done())

    # -- subscription ---------------------------------------------------------

    async def subscribe(self, audience: str) -> AsyncIterator[SSEEvent]:
        """Events for one client; leaving stops the producer when it was the last."""
        queue: asyncio.Queue[SSEEvent] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._subscribers[audience].add(queue)
        self._ensure_producer(audience)
        try:
            latest = self._latest.get(audience)
            if latest is not None:
                yield SSEEvent(data=latest, event="snapshot")
            while True:
                yield await queue.get()
        finally:
            self._subscribers[audience].discard(queue)
            if not self._subscribers[audience]:
                self._stop_producer(audience)

    def _ensure_producer(self, audience: str) -> None:
        task = self._producers.get(audience)
        if task is None or task.done():
            self._producers[audience] = asyncio.create_task(
                self._produce(audience), name=f"governance-stream-{audience}"
            )

    def _stop_producer(self, audience: str) -> None:
        task = self._producers.pop(audience, None)
        if task is not None:
            task.cancel()
        self._latest.pop(audience, None)

    def _broadcast(self, audience: str, event: SSEEvent) -> None:
        for queue in list(self._subscribers.get(audience, ())):
            if queue.full():
                # A slow client loses its oldest event, never blocks the producer.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

    # -- producer -------------------------------------------------------------

    async def tick(self, audience: str) -> None:
        """Compute one snapshot and fan out the resulting events."""
        try:
            snapshot = await self._snapshot_fn(audience)
        except Exception:
            logger.warning("governance_stream_snapshot_failed", exc_info=True)
            return
        events = diff_events(self._latest.get(audience), snapshot)
        self._latest[audience] = snapshot
        self.ticks[audience] += 1
        for event in events:
            self._broadcast(audience, event)

    async def _produce(self, audience: str) -> None:
        bus = self._bus or get_event_bus()
        incidents = bus.subscribe(TOPIC_GOVERNANCE_INCIDENT)
        try:
            while True:
                await self.tick(audience)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(incidents.get(), timeout=self._interval)
                    # Coalesce a burst of notifications into one snapshot.
                    while not incidents.empty():
                        incidents.get_nowait()
        finally:
            bus.unsubscribe(TOPIC_GOVERNANCE_INCIDENT, incidents)

    async def aclose(self) -> None:
        tasks = list(self._producers.values())
        self._producers.clear()
        self._latest.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


_hub: GovernanceStreamHub | None = None


def get_governance_hub() -> GovernanceStreamHub:
    """FastAPI dependency / process-wide hub (closed from the lifespan)."""
    global _hub  # noqa: PLW0603 - one shared producer set per process
    if _hub is None:
        _hub = GovernanceStreamHub()
    return _hub
