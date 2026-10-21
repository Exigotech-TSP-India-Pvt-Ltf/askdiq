"""Full evaluation runs (``eval_runs``): start, cancel, history, estimate.

The run itself executes in the jobs worker (``app.evaluation.run_job``,
job type ``eval.full_run``); this module is what the API needs and stays
free of the pipeline imports.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.events import (
    TOPIC_CACHE_INVALIDATE,
    TOPIC_CONSOLE_CHANGED,
    publish_event,
)
from app.core.logging_config import get_logger
from app.dashboard.snapshots import CACHE_PREFIX, drop_snapshots
from app.evaluation.dataset import EVAL_CASES, EvalCase
from app.evaluation.dataset_extra import load_promoted_cases, merge_cases
from app.jobs.queue import enqueue_in
from app.model_settings.service import get_model_settings
from app.models.db_models import EvalRun
from app.schemas.evaluation_runs import ACTIVE_STATUSES, EvalRunEstimate, EvalRunStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

FULL_RUN_JOB = "eval.full_run"
# Console live-stream scope: a run was queued, progressed or finished.
SCOPE_EVAL_RUNS = "evaluation.runs"
# The Evaluation page's precomputed snapshot kind (app.api.routes.metrics).
LIVE_EVAL_KIND = "evaluation.live"
REPORT_DIR = Path(__file__).resolve().parents[2] / "reports"
REPORT_PREFIX = "eval_report_run_"

# Bounded payloads: per-case answers are cut to this many characters.
MAX_ANSWER_CHARS = 8_000
MAX_ERROR_CHARS = 2_000
# Estimates average over this many recent succeeded runs.
ESTIMATE_HISTORY_RUNS = 10
# Built-in guesses before any run has finished (one pipeline call per case).
DEFAULT_INPUT_TOKENS_PER_CASE = 2_500.0
DEFAULT_OUTPUT_TOKENS_PER_CASE = 350.0
DEFAULT_SECONDS_PER_CASE = 6.0
# The judge adds one LLM call per case (prompt = question + context + answer).
JUDGE_TOKEN_FACTOR = 2.0
JUDGE_TIME_FACTOR = 1.6
_PER_MILLION = 1_000_000


class EvalRunConflictError(Exception):
    """A run is already queued or running."""

    def __init__(self, active: EvalRun | None) -> None:
        super().__init__("An evaluation run is already queued or running")
        self.active = active


class EvalRunStateError(Exception):
    """The run is not in a state that allows the operation."""


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def select_cases(
    promoted: Iterable[EvalCase], case_types: Iterable[str] | None = None
) -> list[EvalCase]:
    """The benchmark (+ promoted golden cases), optionally of ``case_types``."""
    cases = merge_cases(EVAL_CASES, promoted)
    wanted = set(case_types or ())
    return [c for c in cases if c.case_type in wanted] if wanted else cases


async def load_cases(
    db: AsyncSession, case_types: Iterable[str] | None = None
) -> list[EvalCase]:
    return select_cases(await load_promoted_cases(EvalCase, db), case_types)


# ---------------------------------------------------------------------------
# Start / cancel
# ---------------------------------------------------------------------------


async def active_run(db: AsyncSession) -> EvalRun | None:
    return await db.scalar(
        select(EvalRun)
        .where(EvalRun.status.in_([s.value for s in ACTIVE_STATUSES]))
        .limit(1)
    )


_REAP_SQL = text(
    """
    UPDATE eval_runs SET status = 'failed', finished_at = :now,
        error = 'The evaluation worker stopped before the run finished.'
    WHERE status IN ('queued', 'running')
      AND NOT EXISTS (
          SELECT 1 FROM jobs
          WHERE jobs.id = eval_runs.job_id AND jobs.status IN ('queued', 'running')
      )
    """
)


async def reap_orphaned(db: AsyncSession) -> None:
    """Fail active runs whose job is gone (worker crash, container recreate),
    so a lost run never blocks the next one."""
    await db.execute(_REAP_SQL, {"now": utcnow()})


async def start_run(
    db: AsyncSession,
    actor: str,
    judge: bool,
    case_types: list[str] | None,
) -> EvalRun:
    """Insert a queued run and its job in the caller's transaction.

    Raises ``EvalRunConflictError`` when a run is already active (checked,
    then enforced by the partial unique index and the job's dedupe key).
    """
    await reap_orphaned(db)
    current = await active_run(db)
    if current is not None:
        raise EvalRunConflictError(current)
    options: dict[str, Any] = {
        "judge": judge,
        "case_types": sorted(set(case_types)) if case_types else None,
    }
    run = EvalRun(
        id=uuid.uuid4(),
        started_by=actor,
        status=EvalRunStatus.QUEUED.value,
        options=options,
        created_at=utcnow(),
        cases_total=len(await load_cases(db, options["case_types"])),
    )
    try:
        async with db.begin_nested():
            db.add(run)
            await db.flush()
            job_id = await enqueue_in(
                db,
                FULL_RUN_JOB,
                {"run_id": str(run.id)},
                dedupe_key=FULL_RUN_JOB,
                max_attempts=1,
            )
            if job_id is None:
                raise EvalRunConflictError(None)
            run.job_id = job_id
            await db.flush()
    except IntegrityError as exc:
        raise EvalRunConflictError(None) from exc
    return run


_SKIP_JOB_SQL = text(
    "UPDATE jobs SET status = 'done', finished_at = :now, "
    "last_error = 'cancelled before start' WHERE id = :id AND status = 'queued'"
)


async def request_cancel(db: AsyncSession, run: EvalRun) -> EvalRun:
    """Queued: cancelled at once. Running: the worker stops within seconds."""
    if run.status == EvalRunStatus.QUEUED.value:
        run.status = EvalRunStatus.CANCELLED.value
        run.finished_at = utcnow()
        run.cancel_requested = True
        if run.job_id is not None:
            # Frees the job's dedupe key so the next run can be queued.
            await db.execute(_SKIP_JOB_SQL, {"id": run.job_id, "now": run.finished_at})
    elif run.status == EvalRunStatus.RUNNING.value:
        run.cancel_requested = True
    else:
        raise EvalRunStateError(f"Run is already {run.status}")
    await db.flush()
    return run


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def list_runs(
    db: AsyncSession, limit: int, offset: int
) -> tuple[list[EvalRun], int]:
    total = int(await db.scalar(select(func.count()).select_from(EvalRun)) or 0)
    rows = (
        await db.scalars(
            select(EvalRun)
            .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return list(rows), total


async def latest_succeeded(db: AsyncSession) -> EvalRun | None:
    return await db.scalar(
        select(EvalRun)
        .where(EvalRun.status == EvalRunStatus.SUCCEEDED.value)
        .order_by(EvalRun.finished_at.desc().nulls_last())
        .limit(1)
    )


def run_label(run: EvalRun) -> str:
    stamp = run.finished_at.strftime("%Y-%m-%d %H:%M UTC") if run.finished_at else ""
    return f"Evaluation run {str(run.id)[:8]} {stamp}".strip()


async def latest_report(db: AsyncSession) -> tuple[dict, str] | None:
    """The newest succeeded run as a report dict (the JSON report's shape)."""
    run = await latest_succeeded(db)
    if run is None or run.summary is None:
        return None
    return {**run.summary, "cases": list(run.case_results or [])}, run_label(run)


# ---------------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PerCase:
    input_tokens: float
    output_tokens: float
    seconds: float
    sample_runs: int


def average_per_case(rows: Iterable[Any], judge: bool) -> PerCase | None:
    """Token and time averages per case over ``rows`` (succeeded runs).

    Runs with the same judge setting are preferred; otherwise the other
    setting's averages are scaled by the judge factors.
    """
    usable = [r for r in rows if r.cases_total and r.input_tokens is not None]
    same = [r for r in usable if bool((r.options or {}).get("judge")) == judge]
    pool = same or usable
    if not pool:
        return None
    cases = sum(r.cases_total for r in pool)
    tokens_in = sum(r.input_tokens or 0 for r in pool) / cases
    tokens_out = sum(r.output_tokens or 0 for r in pool) / cases
    seconds = sum(r.duration_seconds or 0.0 for r in pool) / cases
    if not same:
        token_scale = JUDGE_TOKEN_FACTOR if judge else 1 / JUDGE_TOKEN_FACTOR
        time_scale = JUDGE_TIME_FACTOR if judge else 1 / JUDGE_TIME_FACTOR
        tokens_in *= token_scale
        tokens_out *= token_scale
        seconds *= time_scale
    return PerCase(tokens_in, tokens_out, seconds, len(pool))


def default_per_case(judge: bool) -> PerCase:
    token_scale = JUDGE_TOKEN_FACTOR if judge else 1.0
    time_scale = JUDGE_TIME_FACTOR if judge else 1.0
    return PerCase(
        DEFAULT_INPUT_TOKENS_PER_CASE * token_scale,
        DEFAULT_OUTPUT_TOKENS_PER_CASE * token_scale,
        DEFAULT_SECONDS_PER_CASE * time_scale,
        0,
    )


def build_estimate(
    case_count: int,
    judge: bool,
    per_case: PerCase | None,
    price_in: float,
    price_out: float,
) -> EvalRunEstimate:
    basis = "history" if per_case is not None else "default"
    per = per_case or default_per_case(judge)
    cost_per_case = (
        per.input_tokens * price_in + per.output_tokens * price_out
    ) / _PER_MILLION
    return EvalRunEstimate(
        case_count=case_count,
        judge=judge,
        avg_input_tokens_per_case=round(per.input_tokens, 1),
        avg_output_tokens_per_case=round(per.output_tokens, 1),
        avg_seconds_per_case=round(per.seconds, 2),
        price_prompt_per_million=price_in,
        price_output_per_million=price_out,
        estimated_cost_usd=round(cost_per_case * case_count, 4),
        estimated_duration_seconds=round(per.seconds * case_count, 1),
        basis=basis,
        sample_runs=per.sample_runs,
    )


async def estimate(
    db: AsyncSession, judge: bool, case_types: list[str] | None
) -> EvalRunEstimate:
    rows = (
        await db.scalars(
            select(EvalRun)
            .where(EvalRun.status == EvalRunStatus.SUCCEEDED.value)
            .order_by(EvalRun.finished_at.desc().nulls_last())
            .limit(ESTIMATE_HISTORY_RUNS)
        )
    ).all()
    prices = await get_model_settings(db)
    return build_estimate(
        len(await load_cases(db, case_types)),
        judge,
        average_per_case(rows, judge),
        prices.price_prompt_per_million,
        prices.price_output_per_million,
    )


# ---------------------------------------------------------------------------
# Report persistence helpers (used by the job and the CLI)
# ---------------------------------------------------------------------------


def jsonable(value: Any) -> Any:
    """JSON-safe copy: non-finite floats become None, unknown types str."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def bounded_case(case: dict[str, Any]) -> dict[str, Any]:
    answer = case.get("answer")
    if isinstance(answer, str) and len(answer) > MAX_ANSWER_CHARS:
        case = {**case, "answer": answer[:MAX_ANSWER_CHARS]}
    return case


def report_payload(report: Any) -> dict[str, Any]:
    """The JSON report written by ``scripts/run_evaluation.py --json``."""
    return {**report.summary(), "cases": [r.to_dict() for r in report.results]}


def report_path(run_id: uuid.UUID, directory: Path | None = None) -> Path:
    """Server-chosen file name (the run's UUID); no request input reaches it."""
    return (directory or REPORT_DIR) / f"{REPORT_PREFIX}{run_id.hex}.json"


def write_report_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


async def mark_progress(
    db: AsyncSession, run_id: uuid.UUID, done: int, passed: int
) -> bool:
    """Store progress; returns whether a cancel was requested."""
    result = await db.execute(
        update(EvalRun)
        .where(EvalRun.id == run_id)
        .values(cases_done=done, cases_passed=passed)
        .returning(EvalRun.cancel_requested)
    )
    return bool(result.scalar())


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


async def announce_runs_changed() -> None:
    await publish_event(TOPIC_CONSOLE_CHANGED, SCOPE_EVAL_RUNS)


async def invalidate_evaluation_views() -> None:
    """Once per finished run: the Evaluation page's snapshots and caches go
    (every process), then open consoles refetch."""
    try:
        await drop_snapshots(LIVE_EVAL_KIND)
        await publish_event(TOPIC_CACHE_INVALIDATE, CACHE_PREFIX)
        await publish_event(TOPIC_CONSOLE_CHANGED, LIVE_EVAL_KIND)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("eval_run_invalidate_failed", exc_info=True)
    await announce_runs_changed()
