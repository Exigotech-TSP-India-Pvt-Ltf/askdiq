"""Event-loop blocking detector (plan B1.7.2).

Turns on asyncio debug mode with ``slow_callback_duration = 0.05`` and fails
when any callback held the loop for longer than that.
"""

from __future__ import annotations

import asyncio
import logging

SLOW_CALLBACK_SECONDS = 0.05


class LoopBlockingDetector(logging.Handler):
    """Context manager; use inside a running event loop."""

    def __init__(self, threshold: float = SLOW_CALLBACK_SECONDS) -> None:
        super().__init__(level=logging.WARNING)
        self.threshold = threshold
        self.offenders: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._previous: tuple[bool, float] = (False, 0.1)

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("Executing ") and " took " in message:
            self.offenders.append(message)

    def __enter__(self) -> LoopBlockingDetector:
        self._loop = asyncio.get_running_loop()
        self._previous = (self._loop.get_debug(), self._loop.slow_callback_duration)
        logging.getLogger("asyncio").addHandler(self)
        self._loop.set_debug(True)
        self._loop.slow_callback_duration = self.threshold
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self._loop is not None
        self._loop.set_debug(self._previous[0])
        self._loop.slow_callback_duration = self._previous[1]
        logging.getLogger("asyncio").removeHandler(self)
        if exc_type is None:
            assert not self.offenders, (
                "Event loop blocked for more than "
                f"{int(self.threshold * 1000)} ms:\n" + "\n".join(self.offenders)
            )
