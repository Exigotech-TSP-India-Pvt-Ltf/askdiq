"""Background job: re-run one golden (promoted) case through the pipeline."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.core import database
from app.core.logging_config import get_logger
from app.jobs.queue import enqueue
from app.jobs.registry import job_handler
from app.models.db_models import EvalPromotedCase

logger = get_logger(__name__)

GOLDEN_RUN_JOB = "eval.golden_run"


def golden_run_dedupe_key(case_id: str) -> str:
    return f"{GOLDEN_RUN_JOB}:{case_id}"


async def enqueue_golden_run(case_id: str, actor: str) -> uuid.UUID | None:
    """Queue a run; None when one is already queued or running (dedupe)."""
    return await enqueue(
        GOLDEN_RUN_JOB,
        {"case_id": case_id, "requested_by": actor},
        dedupe_key=golden_run_dedupe_key(case_id),
    )


@job_handler(GOLDEN_RUN_JOB)
async def golden_run_task(payload: dict[str, Any]) -> None:
    """Evaluate the case and store the outcome on its row."""
    # Heavy pipeline imports stay out of the API process start-up path.
    from app.evaluation.dataset import EvalCase  # noqa: PLC0415 - heavy, worker-only
    from app.evaluation.evaluator import evaluate_case  # noqa: PLC0415 - heavy, worker-only
    from app.retrieval.vector_store import VectorStore  # noqa: PLC0415 - heavy, worker-only

    case_id = str(payload.get("case_id") or "")
    async with database.AsyncSessionLocal() as db:
        case = await db.get(EvalPromotedCase, case_id)
        if case is None:
            logger.warning("golden_run_case_missing", case_id=case_id)
            return
        result = await evaluate_case(
            EvalCase(
                id=case.id,
                query=case.query,
                case_type=case.case_type,
                expect_grounded=case.expect_grounded,
            ),
            VectorStore(db),
        )
        case.last_run_at = datetime.utcnow()
        case.last_run_passed = bool(result.passed)
        case.last_run_detail = {
            "grounded": result.grounded,
            "confidence": result.confidence,
            "guardrail_flags": list(result.guardrail_flags),
            "latency_ms": result.latency_ms,
            "error": result.error,
        }
        await db.commit()
    logger.info("golden_run_done", case_id=case_id, passed=bool(result.passed))
