"""Event-loop blocking detector (plan B1.7.2).

A high-frequency ticker task runs on the loop under test and records how
late each tick wakes up. A late tick means some callback held the loop.
This measures real blocking directly, without asyncio debug mode, which adds
its own overhead and reports every slow callback on a noisy shared runner.

Thresholds:
- ``HARD_BLOCK_SECONDS`` (250 ms): any single stall this long is a real
  blocking call (a sync DB/HTTP/file call or ``time.sleep``). Fail at once.
- ``SLOW_BLOCK_SECONDS`` (50 ms): the B1.7.2 budget for one callback.
- ``MAX_SLOW_BLOCKS`` (3): shared CI runners get descheduled now and then
  (CPU steal, GC), so a few isolated 50-250 ms stalls are tolerated. A
  systematic offender trips it on every request, so it still fails.

Callers enter the detector only after a warm-up request, so one-off costs
(first imports, first DB connection, statement compilation) are not counted.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import TracebackType

TICK_SECONDS = 0.01
SLOW_BLOCK_SECONDS = 0.05
HARD_BLOCK_SECONDS = 0.25
MAX_SLOW_BLOCKS = 3


class LoopBlockingDetector:
    """Async context manager; fails on exit if the loop was blocked."""

    def __init__(
        self,
        slow_s: float = SLOW_BLOCK_SECONDS,
        hard_s: float = HARD_BLOCK_SECONDS,
        max_slow: int = MAX_SLOW_BLOCKS,
    ) -> None:
        self.slow_s = slow_s
        self.hard_s = hard_s
        self.max_slow = max_slow
        self.stalls: list[float] = []
        self._task: asyncio.Task[None] | None = None

    async def _tick(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            expected = loop.time() + TICK_SECONDS
            await asyncio.sleep(TICK_SECONDS)
            lag = loop.time() - expected
            if lag > self.slow_s:
                self.stalls.append(lag)

    async def __aenter__(self) -> LoopBlockingDetector:
        self._task = asyncio.create_task(self._tick(), name="loop-block-detector")
        await asyncio.sleep(0)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Let the ticker observe a stall that ended just before exit.
        await asyncio.sleep(TICK_SECONDS * 3)
        assert self._task is not None
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        if exc_type is not None:
            return
        stalls_ms = [round(s * 1000) for s in self.stalls]
        hard = [s for s in self.stalls if s > self.hard_s]
        assert not hard, (
            f"Event loop blocked > {self.hard_s * 1000:.0f} ms: {stalls_ms}"
        )
        assert len(self.stalls) <= self.max_slow, (
            f"Event loop blocked > {self.slow_s * 1000:.0f} ms "
            f"{len(self.stalls)} times (max {self.max_slow}): {stalls_ms}"
        )
