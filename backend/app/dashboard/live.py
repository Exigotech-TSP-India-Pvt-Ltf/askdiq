"""Console live stream: tells open consoles which data just changed.

Sources (``TOPIC_CONSOLE_CHANGED`` over LISTEN/NOTIFY, so every replica sees
every change):

- ``data``: the jobs worker saw new chats, feedback, evaluations, ratings
  or taxonomy changes (data stamp, checked every ``DASHBOARD_WATCH_SECONDS``);
- ``alerts``: an alert was raised, re-seen, acknowledged or resolved;
- ``<snapshot kind>``: a precomputed aggregate was recomputed.

Events within ``CONSOLE_STREAM_COALESCE_SECONDS`` go out as one ``changed``
frame ``{"scopes": [...], "at": ...}``; the browser refetches the queries of
those scopes (the reads are snapshots, so a refetch is a memory hit). Only
scope names are sent, never data.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.events import TOPIC_CONSOLE_CHANGED, Event, EventBus, get_event_bus
from app.core.sse import SSEEvent

# Upper bound on scopes in one frame (kinds are a small fixed set).
MAX_SCOPES_PER_FRAME = 64


async def _drain(queue: asyncio.Queue[Event], scopes: set[str], seconds: float) -> None:
    """Collect further events for ``seconds`` into ``scopes``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while len(scopes) < MAX_SCOPES_PER_FRAME:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        try:
            event = await asyncio.wait_for(queue.get(), timeout=remaining)
        except TimeoutError:
            return
        scopes.add(event.id)


async def console_events(bus: EventBus | None = None) -> AsyncIterator[SSEEvent]:
    """``ready`` once, then one coalesced ``changed`` frame per burst."""
    bus = bus or get_event_bus()
    coalesce = get_settings().console_stream_coalesce_seconds
    queue = bus.subscribe(TOPIC_CONSOLE_CHANGED)
    try:
        yield SSEEvent(data={"at": datetime.now(UTC).isoformat()}, event="ready")
        while True:
            first = await queue.get()
            scopes = {first.id}
            if coalesce > 0:
                await _drain(queue, scopes, coalesce)
            yield SSEEvent(
                data={"scopes": sorted(scopes), "at": datetime.now(UTC).isoformat()},
                event="changed",
            )
    finally:
        bus.unsubscribe(TOPIC_CONSOLE_CHANGED, queue)
