"""Background job ``eval.full_run``: the full benchmark, in the jobs worker.

Same code path as ``scripts/run_evaluation.py`` (``run_evaluation`` over the
benchmark plus the promoted golden cases), with the options stored on the
``eval_runs`` row. Progress is written to the row and announced on the
console live stream every ``EVAL_RUN_PROGRESS_SECONDS``; a cancel request
on the row stops the run within that interval. The finished report goes to
the row (and, for back-compat, to a JSON file in ``reports/``), then the
Evaluation page's snapshots are invalidated once.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core import database
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.evaluation import runs
from app.jobs.registry import job_handler
from app.model_settings.service import get_model_settings
from app.models.db_models import EvalRun
from app.schemas.evaluation_runs import ACTIVE_STATUSES, EvalRunStatus

if TYPE_CHECKING:
    from app.evaluation.dataset import EvalCase

logger = get_logger(__name__)

SessionFactory = Callable[[], Any]
_STOPPED = "Stopped: the run hit EVAL_RUN_TIMEOUT_SECONDS or the worker shut down."


@dataclass(slots=True)
class _Progress:
    done: int = 0
    passed: int = 0


@dataclass(frozen=True, slots=True)
class _Plan:
    cases: list[EvalCase]
    judge: bool
    price_in: float
    price_out: float


def _timeout() -> float:
    return float(get_settings().eval_run_timeout_seconds)


@job_handler(runs.FULL_RUN_JOB, timeout_seconds=_timeout)
async def full_run_task(payload: dict[str, Any]) -> None:
    """Run the benchmark for the ``eval_runs`` row in the payload."""
    try:
        run_id = uuid.UUID(str(payload.get("run_id")))
    except ValueError:
        logger.warning("eval_run_bad_payload")
        return
    await execute_run(run_id)


async def execute_run(
    run_id: uuid.UUID,
    *,
    session_factory: SessionFactory | None = None,
    report_dir: Path | None = None,
) -> None:
    factory = session_factory or database.AsyncSessionLocal
    plan = await _begin(run_id, factory)
    if plan is None:
        return
    await runs.announce_runs_changed()
    progress = _Progress()
    task = asyncio.create_task(_evaluate(plan, progress, factory))
    try:
        cancelled = await _watch(run_id, task, progress, factory)
    except asyncio.CancelledError:
        await _stop_task(task)
        await _finish_failed(run_id, factory, _STOPPED)
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("eval_run_failed", run_id=str(run_id), error=str(exc))
        await _finish_failed(run_id, factory, f"{type(exc).__name__}: {exc}")
        return
    if cancelled:
        await _finish(run_id, factory, EvalRunStatus.CANCELLED, {})
        logger.info("eval_run_cancelled", run_id=str(run_id), done=progress.done)
        return
    await _succeed(run_id, task.result(), factory, report_dir)


async def _begin(run_id: uuid.UUID, factory: SessionFactory) -> _Plan | None:
    """Mark the row running and resolve its cases and prices (None: skip)."""
    async with factory() as db:
        run = await db.get(EvalRun, run_id)
        if run is None or run.status not in {s.value for s in ACTIVE_STATUSES}:
            logger.info("eval_run_skipped", run_id=str(run_id))
            return None
        if run.cancel_requested:
            run.status = EvalRunStatus.CANCELLED.value
            run.finished_at = runs.utcnow()
            await db.commit()
            return None
        options = run.options or {}
        cases = await runs.load_cases(db, options.get("case_types"))
        prices = await get_model_settings(db)
        run.status = EvalRunStatus.RUNNING.value
        run.started_at = runs.utcnow()
        run.cases_total = len(cases)
        run.cases_done = 0
        run.cases_passed = 0
        await db.commit()
    logger.info("eval_run_started", run_id=str(run_id), cases=len(cases))
    return _Plan(
        cases=cases,
        judge=bool(options.get("judge")),
        price_in=prices.price_prompt_per_million,
        price_out=prices.price_output_per_million,
    )


async def _evaluate(plan: _Plan, progress: _Progress, factory: SessionFactory) -> Any:
    # Pipeline imports stay out of the API process start-up path.
    # pylint: disable=import-outside-toplevel
    from app.evaluation.evaluator import run_evaluation  # noqa: PLC0415 - worker-only
    from app.retrieval.vector_store import VectorStore  # noqa: PLC0415 - worker-only

    def on_result(result: Any) -> None:
        progress.done += 1
        if result.passed:
            progress.passed += 1

    async with factory() as db:
        return await run_evaluation(
            plan.cases,
            VectorStore(db),
            use_judge=plan.judge,
            on_result=on_result,
            concurrency=get_settings().eval_run_concurrency,
            price_in_per_1m=plan.price_in,
            price_out_per_1m=plan.price_out,
            session_factory=factory,
        )


async def _watch(
    run_id: uuid.UUID,
    task: asyncio.Task[Any],
    progress: _Progress,
    factory: SessionFactory,
) -> bool:
    """Store progress until the task ends; True when a cancel stopped it."""
    interval = get_settings().eval_run_progress_seconds
    stored = (-1, -1)
    while True:
        done, _ = await asyncio.wait({task}, timeout=interval)
        current = (progress.done, progress.passed)
        cancel = False
        if current != stored or not done:
            async with factory() as db:
                cancel = await runs.mark_progress(db, run_id, *current)
                await db.commit()
            if current != stored:
                stored = current
                await runs.announce_runs_changed()
        if done:
            task.result()  # re-raise the evaluation's exception, if any
            return False
        if cancel:
            await _stop_task(task)
            return True


async def _stop_task(task: asyncio.Task[Any]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def _succeed(
    run_id: uuid.UUID, report: Any, factory: SessionFactory, report_dir: Path | None
) -> None:
    payload = runs.jsonable(runs.report_payload(report))
    cases = [runs.bounded_case(c) for c in payload.pop("cases", [])]
    operational = payload.get("operational") or {}
    tokens = operational.get("tokens") or {}
    path = runs.report_path(run_id, report_dir)
    try:
        await asyncio.to_thread(
            runs.write_report_file, path, {**payload, "cases": cases}
        )
        report_file: str | None = path.name
    except OSError:
        logger.warning("eval_run_report_file_failed", run_id=str(run_id), exc_info=True)
        report_file = None
    await _finish(
        run_id,
        factory,
        EvalRunStatus.SUCCEEDED,
        {
            "summary": payload,
            "case_results": cases,
            "pass_rate": payload.get("pass_rate"),
            "cost_usd": operational.get("cost_usd_total"),
            "input_tokens": tokens.get("input_total"),
            "output_tokens": tokens.get("output_total"),
            "duration_seconds": report.wall_seconds,
            "cases_done": len(cases),
            "cases_passed": sum(1 for c in cases if c.get("passed")),
            "report_file": report_file,
        },
    )
    await runs.invalidate_evaluation_views()
    logger.info(
        "eval_run_succeeded", run_id=str(run_id), pass_rate=payload.get("pass_rate")
    )


async def _finish_failed(
    run_id: uuid.UUID, factory: SessionFactory, error: str
) -> None:
    with contextlib.suppress(Exception):
        await _finish(
            run_id,
            factory,
            EvalRunStatus.FAILED,
            {"error": error[: runs.MAX_ERROR_CHARS]},
        )


async def _finish(
    run_id: uuid.UUID,
    factory: SessionFactory,
    status: EvalRunStatus,
    values: dict[str, Any],
) -> None:
    async with factory() as db:
        run: EvalRun | None = await db.get(EvalRun, run_id)
        if run is None:
            return
        _apply(run, status, values)
        await db.commit()
    if status != EvalRunStatus.SUCCEEDED:
        await runs.announce_runs_changed()


def _apply(run: EvalRun, status: EvalRunStatus, values: dict[str, Any]) -> None:
    run.status = status.value
    run.finished_at = runs.utcnow()
    if run.duration_seconds is None and run.started_at is not None:
        run.duration_seconds = (run.finished_at - run.started_at).total_seconds()
    for key, value in values.items():
        setattr(run, key, value)
