"""Job handler registry: ``@job_handler("insights.classify")``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

STATE_CLEANUP_JOB = "state.cleanup"

JobHandler = Callable[[dict[str, Any]], Awaitable[None]]

_HANDLERS: dict[str, JobHandler] = {}


def job_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    """Register an async handler for ``job_type``; one handler per type."""

    def _register(fn: JobHandler) -> JobHandler:
        existing = _HANDLERS.get(job_type)
        if existing is not None and existing is not fn:
            raise ValueError(f"Duplicate job handler for '{job_type}'")
        _HANDLERS[job_type] = fn
        return fn

    return _register


def get_handler(job_type: str) -> JobHandler | None:
    return _HANDLERS.get(job_type)


def registered_types() -> list[str]:
    return sorted(_HANDLERS)


def unregister(job_type: str) -> None:
    """Remove a handler (tests only)."""
    _HANDLERS.pop(job_type, None)
