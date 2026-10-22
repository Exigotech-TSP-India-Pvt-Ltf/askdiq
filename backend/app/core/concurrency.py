"""Event-loop thread pool sizing and concurrency control.

- ``SingleFlight``: collapses duplicate concurrent reads.
- ``gather_scoped``: parallel DB work, one ``AsyncSession`` per task.
- ``LLMGate`` / ``llm_capacity``: global LLM back-pressure (429 + Retry-After).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from fastapi import HTTPException, status

from app.core import database
from app.core.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SessionFactory = Callable[[], AbstractAsyncContextManager["AsyncSession"]]

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


_fanout: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def fanout_slots() -> asyncio.Semaphore:
    """Process-wide cap on sessions held by parallel aggregation.

    ``pool_size + max_overflow - AGG_POOL_RESERVE`` (at least 1), so concurrent
    dashboards queue here in order instead of draining the pool: sign-in
    checks, ``/health`` and plain reads always find a connection.
    """
    global _fanout  # noqa: PLW0603 - one cap per process (per event loop)
    loop = asyncio.get_running_loop()
    if _fanout is None or _fanout[0] is not loop:
        cfg = get_settings()
        slots = cfg.db_pool_size + cfg.db_max_overflow - cfg.agg_pool_reserve
        _fanout = (loop, asyncio.Semaphore(max(1, slots)))
    return _fanout[1]


async def gather_scoped(
    *factories: Callable[[AsyncSession], Awaitable[Any]],
    limit: int | None = None,
    session_factory: SessionFactory | None = None,
) -> list[Any]:
    """Run ``factories`` in parallel, each with its **own** short-lived session.

    An ``AsyncSession`` is not concurrency-safe, so every task opens one from
    ``session_factory``; at most ``limit`` (default ``AGG_MAX_CONCURRENCY``) run
    at once. Results keep the input order. The first error is re-raised and
    the remaining tasks are cancelled (``asyncio.TaskGroup`` semantics).
    """
    if session_factory is None:
        session_factory = database.AsyncSessionLocal
    semaphore = asyncio.Semaphore(max(1, limit or get_settings().agg_max_concurrency))
    shared = fanout_slots()
    results: list[Any] = [None] * len(factories)

    async def _run(index: int, factory: Callable[[AsyncSession], Awaitable[Any]]):
        async with semaphore, shared, session_factory() as session:
            results[index] = await factory(session)

    try:
        async with asyncio.TaskGroup() as tg:
            for index, factory in enumerate(factories):
                tg.create_task(_run(index, factory))
    except BaseExceptionGroup as group:
        raise group.exceptions[0] from None
    return results


@dataclass(frozen=True, slots=True)
class ScopedRunner:
    """Injectable handle on ``gather_scoped`` and short-lived sessions.

    Routes take it via ``Depends(get_scoped_runner)`` so tests can swap the
    session factory (e.g. one shared transactional session with ``limit=1``).
    """

    session_factory: SessionFactory | None = None
    limit: int | None = None

    def session(self) -> AbstractAsyncContextManager[AsyncSession]:
        factory = self.session_factory or database.AsyncSessionLocal
        return factory()

    async def gather(
        self, *factories: Callable[[AsyncSession], Awaitable[Any]]
    ) -> list[Any]:
        return await gather_scoped(
            *factories, limit=self.limit, session_factory=self.session_factory
        )


def get_scoped_runner() -> ScopedRunner:
    """FastAPI dependency: the default runner (one pooled session per task).

    Request-scoped, so its sessions carry ``DB_STATEMENT_TIMEOUT_MS``.
    """
    return ScopedRunner(session_factory=database.RequestSessionLocal)


class LLMGate:
    """Non-blocking global cap on concurrent LLM-bound requests."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self.in_use = 0

    def try_acquire(self) -> bool:
        if self.in_use >= self.limit:
            return False
        self.in_use += 1
        return True

    def release(self) -> None:
        self.in_use = max(0, self.in_use - 1)


_llm_gate: LLMGate | None = None


def get_llm_gate() -> LLMGate:
    global _llm_gate  # noqa: PLW0603 - process-wide gate
    if _llm_gate is None:
        _llm_gate = LLMGate(get_settings().llm_max_concurrency)
    return _llm_gate


def reset_llm_gate() -> None:
    """Rebuild the gate from settings on next use (tests / config reload)."""
    global _llm_gate  # noqa: PLW0603 - process-wide gate
    _llm_gate = None


async def llm_capacity() -> AsyncIterator[None]:
    """FastAPI dependency: hold one LLM slot for the request, or 429 if full."""
    gate = get_llm_gate()
    if not gate.try_acquire():
        retry_after = get_settings().llm_saturated_retry_after_seconds
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The assistant is at capacity. Please retry shortly.",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        yield
    finally:
        gate.release()
