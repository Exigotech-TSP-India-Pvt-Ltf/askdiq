"""SSE responses end after SSE_MAX_STREAM_SECONDS so they never block a reload."""

import asyncio
from collections.abc import AsyncIterator

from app.core import sse


class _Req:
    async def is_disconnected(self) -> bool:
        return False


async def _forever() -> AsyncIterator[dict]:
    while True:
        await asyncio.sleep(3600)
        yield {}


async def test_stream_ends_at_max_lifetime() -> None:
    frames = []
    gen = sse._frames(_Req(), _forever(), heartbeat=0.05, retry_ms=1000, max_seconds=0.3)
    async with asyncio.timeout(5):
        async for frame in gen:
            frames.append(frame)
    assert frames[0].startswith("retry:")
    assert any(f == sse.KEEPALIVE_FRAME for f in frames)
