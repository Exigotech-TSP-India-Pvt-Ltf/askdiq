"""Event-loop thread pool sizing and concurrency control (Single-Flight deduplication)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Generic, TypeVar

from app.core.config import get_settings

T = TypeVar("T")


def configure_thread_pool() -> None:
    """Replace the running loop's default executor (cpu+4 workers) with a larger one."""
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(
            max_workers=get_settings().thread_pool_workers,
            thread_name_prefix="askdiq",
        )
    )


class SingleFlight(Generic[T]):
    """Collapses duplicate concurrent read requests for identical keys into a single execution.

    Prevents cache stampedes and connection pool exhaustion when multiple users
    or dashboard components request high-overhead analytical summaries simultaneously.
    """

    def __init__(self) -> None:
        self._in_flight: dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()

    async def run(self, key: str, coro_fn: Callable[[], Awaitable[T]]) -> T:
        """Executes coro_fn if key is not currently in flight; otherwise awaits existing in-flight execution."""
        async with self._lock:
            if key in self._in_flight:
                fut = self._in_flight[key]
            else:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._in_flight[key] = fut
                task = asyncio.create_task(self._execute(key, coro_fn, fut))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

        # Shield future from cancellation of this particular caller
        return await asyncio.shield(fut)

    async def _execute(
        self,
        key: str,
        coro_fn: Callable[[], Awaitable[T]],
        fut: asyncio.Future[T],
    ) -> None:
        try:
            res = await coro_fn()
            if not fut.done():
                fut.set_result(res)
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
        finally:
            async with self._lock:
                self._in_flight.pop(key, None)


# Default singleton instance for dashboard and analytical queries
single_flight: SingleFlight[Any] = SingleFlight()
