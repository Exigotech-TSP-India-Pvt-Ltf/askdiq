"""Full evaluation runs started from the Evaluation page.

- POST /metrics/evaluation/runs                 start one (eval:run, rate-limited, 202 / 409)
- GET  /metrics/evaluation/runs                 history, newest first (dashboard:read)
- GET  /metrics/evaluation/runs/estimate        case count, cost and duration (dashboard:read)
- GET  /metrics/evaluation/runs/{run_id}        one run with its case results (dashboard:read)
- POST /metrics/evaluation/runs/{run_id}/cancel stop a queued / running run (eval:run)

The run executes in the jobs worker (job ``eval.full_run``), never on the
request path. Progress is announced on ``GET /admin/live`` with the scope
``evaluation.runs``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import aenforce_eval_run_rate_limit
from app.core.request_meta import get_client_ip
from app.evaluation import runs
from app.governance.audit import record_audit_event
from app.models.db_models import EvalRun
from app.schemas.evaluation_runs import (
    MAX_RUNS_PAGE,
    EvalCaseType,
    EvalRunCreate,
    EvalRunDetail,
    EvalRunEstimate,
    EvalRunPage,
    EvalRunRead,
    EvalRunStarted,
)

router = APIRouter(prefix="/metrics/evaluation/runs", tags=["metrics"])

_PROBLEM = {"content": {"application/problem+json": {}}}
_READ_ERRORS = {
    401: {"description": "Not signed in or missing service key", **_PROBLEM},
    403: {"description": "Requires the dashboard:read permission", **_PROBLEM},
}
_RUN_ERRORS = {
    401: {"description": "Not signed in or missing service key", **_PROBLEM},
    403: {"description": "Requires the eval:run permission", **_PROBLEM},
}
_NOT_FOUND = {404: {"description": "Run not found", **_PROBLEM}}

Reader = Annotated[AuthUser, Depends(require_permission(Permission.DASHBOARD_READ))]
Runner = Annotated[AuthUser, Depends(require_permission(Permission.EVAL_RUN))]


def _audit_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _get_or_404(db: AsyncSession, run_id: uuid.UUID) -> EvalRun:
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation run not found")
    return run


@router.post(
    "",
    response_model=EvalRunStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a full evaluation run",
    description=(
        "Queues the full benchmark (the same evaluator as "
        "`scripts/run_evaluation.py`) on the jobs worker. One run at a time "
        "across all replicas: 409 while another is queued or running. "
        "Rate-limited per admin. Audited as `eval_run_started`."
    ),
    responses={
        **_RUN_ERRORS,
        409: {"description": "A run is already queued or running", **_PROBLEM},
        429: {"description": "Too many starts; retry later", **_PROBLEM},
    },
)
async def start_evaluation_run(
    user: Runner,
    request: Request,
    body: EvalRunCreate,
    db: AsyncSession = Depends(get_db),
) -> EvalRunStarted:
    await aenforce_eval_run_rate_limit(
        user.email, get_settings().eval_run_rate_limit_per_minute
    )
    case_types = [t.value for t in body.case_types] if body.case_types else None
    try:
        run = await runs.start_run(db, user.email, body.judge, case_types)
    except runs.EvalRunConflictError as exc:
        active = exc.active.id if exc.active is not None else None
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                "An evaluation run is already in progress"
                + (f" ({active})." if active else ".")
            ),
        ) from exc
    await record_audit_event(
        db,
        request_id=_audit_id("eval-run"),
        actor=user.email,
        action="eval_run_started",
        detail={
            "run_id": str(run.id),
            "judge": body.judge,
            "case_types": case_types,
            "cases": run.cases_total,
        },
        ip_address=get_client_ip(request),
        commit=True,
    )
    await runs.announce_runs_changed()
    return EvalRunStarted(run_id=run.id, run=EvalRunRead.model_validate(run))


@router.get(
    "",
    response_model=EvalRunPage,
    summary="Evaluation run history",
    description="Runs newest first (without per-case results), plus the active run.",
    responses=_READ_ERRORS,
)
async def list_evaluation_runs(
    user: Reader,
    limit: int = Query(20, ge=1, le=MAX_RUNS_PAGE),
    offset: int = Query(0, ge=0, le=100_000),
    db: AsyncSession = Depends(get_db),
) -> EvalRunPage:
    del user
    rows, total = await runs.list_runs(db, limit, offset)
    active = await runs.active_run(db)
    return EvalRunPage(
        items=[EvalRunRead.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        active=EvalRunRead.model_validate(active) if active is not None else None,
    )


@router.get(
    "/estimate",
    response_model=EvalRunEstimate,
    summary="Estimate a full evaluation run",
    description=(
        "Case count, plus cost and duration from the model settings prices "
        "times the average tokens and seconds per case of recent succeeded "
        "runs (built-in guesses before the first run)."
    ),
    responses=_READ_ERRORS,
)
async def estimate_evaluation_run(
    user: Reader,
    judge: bool = Query(False),
    case_types: list[EvalCaseType] | None = Query(None, max_length=len(EvalCaseType)),
    db: AsyncSession = Depends(get_db),
) -> EvalRunEstimate:
    del user
    types = [t.value for t in case_types] if case_types else None
    return await runs.estimate(db, judge, types)


@router.get(
    "/{run_id}",
    response_model=EvalRunDetail,
    summary="One evaluation run with its summary and case results",
    responses={**_READ_ERRORS, **_NOT_FOUND},
)
async def get_evaluation_run(
    user: Reader,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> EvalRunDetail:
    del user
    return EvalRunDetail.model_validate(await _get_or_404(db, run_id))


@router.post(
    "/{run_id}/cancel",
    response_model=EvalRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cancel a queued or running evaluation run",
    description=(
        "A queued run is cancelled at once; a running one stops within a few "
        "seconds (`status` turns `cancelled`). Audited as `eval_run_cancelled`."
    ),
    responses={
        **_RUN_ERRORS,
        **_NOT_FOUND,
        409: {"description": "The run already finished", **_PROBLEM},
    },
)
async def cancel_evaluation_run(
    user: Runner,
    request: Request,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> EvalRunRead:
    run = await _get_or_404(db, run_id)
    try:
        await runs.request_cancel(db, run)
    except runs.EvalRunStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await record_audit_event(
        db,
        request_id=_audit_id("eval-run-cancel"),
        actor=user.email,
        action="eval_run_cancelled",
        detail={"run_id": str(run.id), "status": run.status},
        ip_address=get_client_ip(request),
        commit=True,
    )
    await runs.announce_runs_changed()
    return EvalRunRead.model_validate(run)
