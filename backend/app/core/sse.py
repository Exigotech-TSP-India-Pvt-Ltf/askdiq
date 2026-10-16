"""Server-Sent Events helper (live governance / monitoring streams).

``event_stream()`` wraps an async iterator of events in a
``text/event-stream`` response that:

- sends a ``retry:`` reconnect hint first,
- emits a ``: keepalive`` comment when idle for ``heartbeat`` seconds
  (keeps proxies and load balancers from closing the connection),
- stops and closes the source iterator on client disconnect, leaving no
  background task behind.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.core.compression import SSE_MEDIA_TYPE
from app.core.config import get_settings

KEEPALIVE_FRAME = ": keepalive\n\n"
_DONE = object()


@dataclass(frozen=True, slots=True)
class SSEEvent:
    data: Any
    event: str | None = None
    id: str | None = None


def format_sse(item: SSEEvent | dict[str, Any] | str) -> str:
    """Serialise one event to the SSE wire format."""
    if not isinstance(item, SSEEvent):
        item = SSEEvent(data=item)
    lines: list[str] = []
    if item.id is not None:
        lines.append(f"id: {item.id}")
    if item.event is not None:
        lines.append(f"event: {item.event}")
    data = item.data if isinstance(item.data, str) else json.dumps(item.data)
    lines.extend(f"data: {line}" for line in data.splitlines() or [""])
    return "\n".join(lines) + "\n\n"


@dataclass(frozen=True, slots=True)
class _Failure:
    error: Exception


async def _pump(source: AsyncIterator[Any], queue: asyncio.Queue[Any]) -> None:
    try:
        async for item in source:
            await queue.put(item)
    except Exception as exc:
        await queue.put(_Failure(exc))
        return
    await queue.put(_DONE)


async def _frames(
    request: Request,
    source: AsyncIterator[Any],
    heartbeat: float,
    retry_ms: int,
    max_seconds: float,
) -> AsyncIterator[str]:
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=100)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_seconds
    pump = asyncio.create_task(_pump(source, queue), name="sse-pump")
    try:
        yield f"retry: {retry_ms}\n\n"
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                # Bounded lifetime: the client reconnects after `retry_ms`.
                return
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=min(heartbeat, remaining)
                )
            except TimeoutError:
                if await request.is_disconnected() or loop.time() >= deadline:
                    return
                yield KEEPALIVE_FRAME
                continue
            if item is _DONE:
                return
            if isinstance(item, _Failure):
                raise item.error
            yield format_sse(item)
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


def event_stream(
    request: Request,
    source: AsyncIterator[Any],
    heartbeat: float | None = None,
    retry_ms: int | None = None,
) -> StreamingResponse:
    """Return a ``text/event-stream`` response fed by ``source``."""
    settings = get_settings()
    return StreamingResponse(
        _frames(
            request,
            source,
            heartbeat if heartbeat is not None else settings.sse_heartbeat_seconds,
            retry_ms if retry_ms is not None else settings.sse_retry_ms,
            settings.sse_max_stream_seconds,
        ),
        media_type=SSE_MEDIA_TYPE,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
