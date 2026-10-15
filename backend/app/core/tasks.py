"""Supervised fire-and-forget background tasks (OWASP A10).

``spawn()`` replaces bare ``asyncio.create_task``: it keeps a strong
reference (so the task is not garbage collected mid-flight), logs any
exception instead of letting it vanish, and ``drain()`` lets the lifespan
shutdown wait (bounded) for in-flight work before the process exits.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from app.core.logging_config import get_logger
from app.governance.pii import redact_pii

logger = get_logger(__name__)

_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
    """Schedule ``coro`` as a supervised background task."""
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


def _on_task_done(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        logger.warning("background_task_cancelled", task_name=task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        masked, _ = redact_pii(str(exc))
        logger.error(
            "background_task_failed",
            task_name=task.get_name(),
            error_type=type(exc).__name__,
            error=masked,
        )


def pending_tasks() -> int:
    """Number of supervised tasks still running."""
    return sum(1 for t in _tasks if not t.done())


async def drain(grace_seconds: float) -> int:
    """Wait up to ``grace_seconds`` seconds for in-flight tasks, then cancel the rest.

    Returns the number of tasks that had to be cancelled.
    """
    pending = {t for t in _tasks if not t.done()}
    if not pending:
        return 0
    logger.info(
        "background_tasks_draining", pending=len(pending), grace_seconds=grace_seconds
    )
    _done, still_running = await asyncio.wait(pending, timeout=grace_seconds)
    for task in still_running:
        task.cancel()
    if still_running:
        await asyncio.gather(*still_running, return_exceptions=True)
        logger.warning("background_tasks_drain_timeout", cancelled=len(still_running))
    return len(still_running)
