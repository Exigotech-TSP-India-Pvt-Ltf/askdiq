"""Built-in job handlers (imported by the worker to register them)."""

from __future__ import annotations

from typing import Any

# Feature job modules register their handlers on import.
from app.alerts import jobs as alerts_jobs  # noqa: F401 - alerts.evaluate / notify
from app.azure_insights import jobs as azure_jobs  # noqa: F401 - azure.* sources
from app.core.logging_config import get_logger
from app.core.state import PostgresState
from app.feedback import jobs as feedback_jobs  # noqa: F401 - registers eval.golden_run
from app.insights import jobs as insights_jobs  # noqa: F401
from app.jobs.queue import prune_finished
from app.jobs.registry import STATE_CLEANUP_JOB, job_handler

logger = get_logger(__name__)


@job_handler(STATE_CLEANUP_JOB)
async def state_cleanup_task(_payload: dict[str, Any]) -> None:
    """Delete expired rate-limit buckets / OIDC states and old finished jobs."""
    removed = await PostgresState().cleanup()
    pruned = await prune_finished()
    logger.info("state_cleanup_done", state_rows=removed, jobs_pruned=pruned)
