"""Built-in job handlers (imported by the worker to register them)."""

from __future__ import annotations

from typing import Any

# Feature job modules register their handlers on import.
from app.alerts import jobs as alerts_jobs  # noqa: F401 - alerts.evaluate / notify
from app.azure_insights import jobs as azure_jobs  # noqa: F401 - azure.* sources
from app.core.logging_config import get_logger
from app.core.sessions import prune_empty_sessions
from app.core.state import PostgresState
from app.evaluation import run_job as eval_run_jobs  # noqa: F401 - eval.full_run
from app.feedback import jobs as feedback_jobs  # noqa: F401 - registers eval.golden_run
from app.insights import jobs as insights_jobs  # noqa: F401
from app.jobs.queue import prune_finished
from app.jobs.registry import (
    CHAT_PRUNE_EMPTY_SESSIONS_JOB,
    STATE_CLEANUP_JOB,
    job_handler,
)

logger = get_logger(__name__)


@job_handler(STATE_CLEANUP_JOB)
async def state_cleanup_task(_payload: dict[str, Any]) -> None:
    """Delete expired rate-limit buckets / OIDC states and old finished jobs."""
    removed = await PostgresState().cleanup()
    pruned = await prune_finished()
    logger.info("state_cleanup_done", state_rows=removed, jobs_pruned=pruned)


@job_handler(CHAT_PRUNE_EMPTY_SESSIONS_JOB)
async def prune_empty_sessions_task(_payload: dict[str, Any]) -> None:
    """Delete chat sessions that never got a message (batched, idempotent)."""
    deleted = await prune_empty_sessions()
    logger.info("chat_empty_sessions_pruned", deleted=deleted)
