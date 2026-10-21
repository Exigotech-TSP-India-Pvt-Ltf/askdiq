"""Job handler registry: ``@job_handler("insights.classify")``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

STATE_CLEANUP_JOB = "state.cleanup"
# Daily removal of chat sessions that never got a message (app.jobs.handlers).
CHAT_PRUNE_EMPTY_SESSIONS_JOB = "chat.prune_empty_sessions"
# Periodic alert evaluation (handler in app.alerts.jobs).
ALERTS_EVALUATE_JOB = "alerts.evaluate"

JobHandler = Callable[[dict[str, Any]], Awaitable[None]]

_HANDLERS: dict[str, JobHandler] = {}
# Per-type run time limits above JOBS_LOCK_TIMEOUT_SECONDS (long jobs; the
# worker keeps their lock alive with a heartbeat).
_TIMEOUTS: dict[str, Callable[[], float]] = {}


def job_handler(
    job_type: str, *, timeout_seconds: Callable[[], float] | None = None
) -> Callable[[JobHandler], JobHandler]:
    """Register an async handler for ``job_type``; one handler per type.

    ``timeout_seconds`` (read when the job runs) lets a long job outlive the
    lock timeout; its lock is then refreshed while it runs.
    """

    def _register(fn: JobHandler) -> JobHandler:
        existing = _HANDLERS.get(job_type)
        if existing is not None and existing is not fn:
            raise ValueError(f"Duplicate job handler for '{job_type}'")
        _HANDLERS[job_type] = fn
        if timeout_seconds is not None:
            _TIMEOUTS[job_type] = timeout_seconds
        return fn

    return _register


def get_handler(job_type: str) -> JobHandler | None:
    return _HANDLERS.get(job_type)


def get_timeout(job_type: str) -> float | None:
    """The type's own run time limit, if it registered one."""
    timeout = _TIMEOUTS.get(job_type)
    return float(timeout()) if timeout is not None else None


def registered_types() -> list[str]:
    return sorted(_HANDLERS)


def unregister(job_type: str) -> None:
    """Remove a handler (tests only)."""
    _HANDLERS.pop(job_type, None)
    _TIMEOUTS.pop(job_type, None)
