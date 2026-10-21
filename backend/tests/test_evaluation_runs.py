"""Full evaluation runs: routes (authz, 202/409, cancel, history, estimate,
report source), the ``eval.full_run`` job over a tiny fake case set
(summary, progress, cancel, failure) and the migration round trip."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Header
from httpx import AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import authenticate_session
from app.auth.permissions import Role, UserStatus
from app.auth.principal import AuthUser
from app.core.config import get_settings
from app.core.rate_limit import _eval_run_log
from app.evaluation import evaluator, run_job, runs
from app.evaluation.dataset import EvalCase
from app.jobs import queue
from app.jobs.registry import get_timeout
from app.models.db_models import AuditLog, EvalRun
from main import app
from tests.pg_helpers import new_engine, require_postgres

BACKEND_DIR = Path(__file__).resolve().parents[1]
RUNS = "/metrics/evaluation/runs"


@pytest.fixture(autouse=True)
def _reset_eval_rate_limit():
    _eval_run_log.clear()
    yield
    _eval_run_log.clear()


def _actor() -> str:
    return f"eval-{uuid.uuid4().hex[:8]}@example.com"


def _finished_run(**values: Any) -> EvalRun:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "started_by": "ops@example.com",
        "status": "succeeded",
        "options": {"judge": False, "case_types": None},
        "created_at": now - timedelta(minutes=10),
        "started_at": now - timedelta(minutes=10),
        "finished_at": now,
        "cases_total": 4,
        "cases_done": 4,
        "cases_passed": 3,
        "pass_rate": 0.75,
        "duration_seconds": 40.0,
        "input_tokens": 8_000,
        "output_tokens": 1_200,
        "cost_usd": 0.01,
    }
    base.update(values)
    return EvalRun(**base)


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


def test_estimate_maths_from_history() -> None:
    rows = [
        _finished_run(
            cases_total=10,
            input_tokens=20_000,
            output_tokens=3_000,
            duration_seconds=50.0,
        ),
        _finished_run(
            cases_total=10,
            input_tokens=40_000,
            output_tokens=5_000,
            duration_seconds=70.0,
        ),
    ]
    per_case = runs.average_per_case(rows, judge=False)
    assert per_case is not None
    assert per_case.input_tokens == 3_000
    assert per_case.output_tokens == 400
    assert per_case.seconds == 6.0
    assert per_case.sample_runs == 2
    est = runs.build_estimate(50, False, per_case, price_in=0.25, price_out=2.0)
    # 50 x (3000 x 0.25 + 400 x 2.0) / 1e6
    assert est.estimated_cost_usd == pytest.approx(0.0775)
    assert est.estimated_duration_seconds == 300.0
    assert est.basis == "history"


def test_estimate_scales_other_judge_setting_and_has_defaults() -> None:
    rows = [
        _finished_run(
            cases_total=10,
            input_tokens=10_000,
            output_tokens=1_000,
            duration_seconds=10.0,
        )
    ]
    judged = runs.average_per_case(rows, judge=True)
    assert judged is not None
    assert judged.input_tokens == 1_000 * runs.JUDGE_TOKEN_FACTOR
    assert judged.seconds == pytest.approx(runs.JUDGE_TIME_FACTOR)
    assert runs.average_per_case([], judge=False) is None
    est = runs.build_estimate(2, True, None, price_in=1.0, price_out=1.0)
    assert est.basis == "default"
    assert est.sample_runs == 0
    expected = (
        2
        * (runs.DEFAULT_INPUT_TOKENS_PER_CASE + runs.DEFAULT_OUTPUT_TOKENS_PER_CASE)
        * runs.JUDGE_TOKEN_FACTOR
        / 1_000_000
    )
    assert est.estimated_cost_usd == pytest.approx(expected, abs=1e-4)


def test_case_type_filter_and_safe_payloads() -> None:
    every = runs.select_cases([])
    only = runs.select_cases([], ["negative"])
    assert only and all(c.case_type == "negative" for c in only)
    assert len(only) < len(every)
    assert runs.jsonable({"a": float("nan"), "b": {1, 2}, "c": (1.5,)}) == {
        "a": None,
        "b": [1, 2],
        "c": [1.5],
    }
    long = runs.bounded_case({"answer": "x" * (runs.MAX_ANSWER_CHARS + 10)})
    assert len(long["answer"]) == runs.MAX_ANSWER_CHARS
    run_id = uuid.uuid4()
    path = runs.report_path(run_id, Path("reports"))
    assert path.name == f"eval_report_run_{run_id.hex}.json"


def test_full_run_job_may_outlive_the_lock_timeout() -> None:
    assert get_timeout(runs.FULL_RUN_JOB) == float(
        get_settings().eval_run_timeout_seconds
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _as_role(role: Role) -> Callable[..., Any]:
    async def _user(
        x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
    ) -> AuthUser:
        email = x_client_id or "operator@example.com"
        return AuthUser(
            id=uuid.uuid5(uuid.NAMESPACE_URL, email),
            email=email,
            name=email,
            role=role,
            status=UserStatus.ACTIVE,
            version=1,
        )

    return _user


async def test_operator_can_read_but_not_start_or_cancel(client: AsyncClient) -> None:
    app.dependency_overrides[authenticate_session] = _as_role(Role.OPERATOR)
    try:
        assert (await client.post(RUNS, json={})).status_code == 403
        cancel = await client.post(f"{RUNS}/{uuid.uuid4()}/cancel")
        assert cancel.status_code == 403
        assert (await client.get(RUNS)).status_code == 200
        assert (await client.get(f"{RUNS}/estimate")).status_code == 200
    finally:
        app.dependency_overrides.pop(authenticate_session, None)


async def test_start_is_202_then_409_while_active_and_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = _actor()
    client.headers["X-Client-Id"] = actor
    started = await client.post(
        RUNS, json={"judge": True, "case_types": ["negative", "spam"]}
    )
    assert started.status_code == 202, started.text
    body = started.json()
    run_id = uuid.UUID(body["run_id"])
    assert body["run"]["status"] == "queued"
    assert body["run"]["options"] == {"judge": True, "case_types": ["negative", "spam"]}
    assert body["run"]["cases_total"] > 0

    run = await db_session.get(EvalRun, run_id)
    assert run is not None and run.job_id is not None
    job = (
        await db_session.execute(
            text("SELECT type, dedupe_key, payload FROM jobs WHERE id = :id"),
            {"id": run.job_id},
        )
    ).one()
    assert job.type == runs.FULL_RUN_JOB
    assert job.dedupe_key == runs.FULL_RUN_JOB
    assert job.payload == {"run_id": str(run_id)}

    again = await client.post(RUNS, json={})
    assert again.status_code == 409
    listing = (await client.get(RUNS)).json()
    assert listing["active"]["id"] == str(run_id)

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.actor == actor, AuditLog.action == "eval_run_started"
        )
    )
    assert audit is not None and audit.detail["run_id"] == str(run_id)


async def test_start_validates_the_body(client: AsyncClient) -> None:
    client.headers["X-Client-Id"] = _actor()
    bad_type = await client.post(RUNS, json={"case_types": ["../../etc"]})
    assert bad_type.status_code == 422
    extra = await client.post(RUNS, json={"report_file": "x.json"})
    assert extra.status_code == 422


async def test_start_is_rate_limited(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "eval_run_rate_limit_per_minute", 1)
    client.headers["X-Client-Id"] = _actor()
    first = await client.post(RUNS, json={})
    assert first.status_code == 202
    second = await client.post(RUNS, json={})
    assert second.status_code == 429
    assert "Retry-After" in second.headers


async def test_cancel_queued_run_then_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = _actor()
    client.headers["X-Client-Id"] = actor
    run_id = (await client.post(RUNS, json={})).json()["run_id"]
    cancelled = await client.post(f"{RUNS}/{run_id}/cancel")
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelled"
    assert (await client.post(f"{RUNS}/{run_id}/cancel")).status_code == 409
    assert (await client.post(f"{RUNS}/{uuid.uuid4()}/cancel")).status_code == 404
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.actor == actor, AuditLog.action == "eval_run_cancelled"
        )
    )
    assert audit is not None
    # The slot is free again.
    assert (await client.post(RUNS, json={})).status_code == 202


async def test_running_cancel_only_flags_the_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    run = _finished_run(status="running", finished_at=None, pass_rate=None)
    db_session.add(run)
    await db_session.flush()
    response = await client.post(f"{RUNS}/{run.id}/cancel")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert body["cancel_requested"] is True


async def test_orphaned_active_run_does_not_block_a_new_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    orphan = _finished_run(status="running", finished_at=None, job_id=uuid.uuid4())
    db_session.add(orphan)
    await db_session.flush()
    client.headers["X-Client-Id"] = _actor()
    assert (await client.post(RUNS, json={})).status_code == 202
    await db_session.refresh(orphan)
    assert orphan.status == "failed"


async def test_history_is_paginated_newest_first(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await db_session.execute(delete(EvalRun))
    base = datetime.now(UTC) - timedelta(days=1)
    made = [_finished_run(created_at=base + timedelta(minutes=i)) for i in range(3)]
    db_session.add_all(made)
    await db_session.flush()
    first = (await client.get(f"{RUNS}?limit=2&offset=0")).json()
    assert first["total"] == 3
    assert [r["id"] for r in first["items"]] == [str(made[2].id), str(made[1].id)]
    assert "case_results" not in first["items"][0]
    second = (await client.get(f"{RUNS}?limit=2&offset=2")).json()
    assert [r["id"] for r in second["items"]] == [str(made[0].id)]
    assert (await client.get(f"{RUNS}?limit=1000")).status_code == 422
    detail = await client.get(f"{RUNS}/{made[0].id}")
    assert detail.status_code == 200
    assert (await client.get(f"{RUNS}/{uuid.uuid4()}")).status_code == 404
    assert (await client.get(f"{RUNS}/not-a-uuid")).status_code == 422


async def test_estimate_route_uses_history(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await db_session.execute(delete(EvalRun))
    db_session.add(
        _finished_run(
            cases_total=10,
            input_tokens=30_000,
            output_tokens=4_000,
            duration_seconds=60.0,
        )
    )
    await db_session.flush()
    body = (await client.get(f"{RUNS}/estimate?judge=false")).json()
    assert body["basis"] == "history"
    assert body["avg_input_tokens_per_case"] == 3_000
    assert body["avg_seconds_per_case"] == 6.0
    assert body["case_count"] >= len(runs.select_cases([]))
    filtered = (await client.get(f"{RUNS}/estimate?case_types=negative")).json()
    assert filtered["case_count"] < body["case_count"]


async def test_report_source_prefers_latest_succeeded_run(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    summary = {"pass_rate": 0.4321, "n_cases": 1, "citation": {}, "judge": {}}
    case = {
        "id": "db_case",
        "type": "negative",
        "query": "q",
        "passed": False,
        "known_issue": None,
    }
    db_session.add_all(
        [
            _finished_run(
                finished_at=datetime.now(UTC) - timedelta(days=2),
                summary={**summary, "pass_rate": 0.1},
                case_results=[case],
            ),
            _finished_run(summary=summary, case_results=[case]),
            _finished_run(status="failed", summary={"pass_rate": 0.9}),
        ]
    )
    await db_session.flush()
    body = (await client.get("/metrics/evaluation?audience=testing")).json()
    assert body["pass_rate"] == 0.4321
    assert body["cases"][0]["id"] == "db_case"
    assert body["report_file"].startswith("Evaluation run ")


# ---------------------------------------------------------------------------
# The job (committed rows on a real database)
# ---------------------------------------------------------------------------

_CASES = [
    EvalCase(id=f"fake_{i}", query=f"question {i}", case_type="negative")
    for i in range(3)
]


def _result(case: EvalCase, passed: bool) -> evaluator.EvalResult:
    return evaluator.EvalResult(
        case=case,
        grounded=passed,
        confidence=0.9,
        guardrail_flags=[],
        matched_docs=set(),
        latency_ms=12.0,
        stage_timings={},
        answer_word_count=3,
        answer="an answer here",
        retrieval_ok=None,
        grounded_ok=passed,
        flags_ok=True,
        input_tokens=100,
        output_tokens=20,
    )


@pytest.fixture
async def job_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = new_engine()
    await require_postgres(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        await db.execute(
            text("DELETE FROM eval_runs WHERE status IN ('queued', 'running')")
        )
        await db.commit()
    try:
        yield factory
    finally:
        async with factory() as db:
            await db.execute(delete(EvalRun).where(EvalRun.started_by == "job@test"))
            await db.commit()
        await engine.dispose()


async def _queued(
    factory: async_sessionmaker[AsyncSession], **options: Any
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            EvalRun(
                id=run_id,
                started_by="job@test",
                status="queued",
                options={"judge": False, "case_types": None, **options},
            )
        )
        await db.commit()
    return run_id


async def _row(factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> EvalRun:
    async with factory() as db:
        row = await db.get(EvalRun, run_id)
    assert row is not None
    return row


@pytest.fixture
def fake_cases(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(delay=0.0, fail=False, progress=[])

    async def _load(_db: AsyncSession, case_types: Any = None) -> list[EvalCase]:
        del case_types
        return list(_CASES)

    async def _evaluate(case: EvalCase, _store: Any, **_kwargs: Any) -> Any:
        await asyncio.sleep(state.delay)
        if state.fail:
            raise RuntimeError("model unavailable")
        return _result(case, passed=case.id != "fake_2")

    real_mark = runs.mark_progress

    async def _mark(
        db: AsyncSession, run_id: uuid.UUID, done: int, passed: int
    ) -> bool:
        state.progress.append((done, passed))
        return await real_mark(db, run_id, done, passed)

    monkeypatch.setattr(runs, "load_cases", _load)
    monkeypatch.setattr(evaluator, "evaluate_case", _evaluate)
    monkeypatch.setattr(runs, "mark_progress", _mark)
    monkeypatch.setattr(get_settings(), "eval_run_progress_seconds", 0.05)
    monkeypatch.setattr(get_settings(), "eval_run_concurrency", 1)
    return state


@pytest.mark.db
async def test_job_runs_fake_cases_and_persists_the_summary(
    job_db: async_sessionmaker[AsyncSession],
    fake_cases: SimpleNamespace,
    tmp_path: Path,
) -> None:
    fake_cases.delay = 0.4
    run_id = await _queued(job_db)
    await run_job.execute_run(run_id, session_factory=job_db, report_dir=tmp_path)
    row = await _row(job_db, run_id)
    assert row.status == "succeeded", row.error
    assert row.cases_total == 3 and row.cases_done == 3 and row.cases_passed == 2
    assert row.pass_rate == pytest.approx(2 / 3)
    assert row.summary is not None and row.summary["n_cases"] == 3
    assert "cases" not in row.summary
    assert [c["id"] for c in row.case_results or []] == ["fake_0", "fake_1", "fake_2"]
    assert row.input_tokens == 300 and row.output_tokens == 60
    assert row.started_at is not None and row.finished_at is not None
    assert row.report_file == f"eval_report_run_{run_id.hex}.json"
    assert await asyncio.to_thread((tmp_path / row.report_file).is_file)
    # Progress was stored while the cases ran, not only at the end.
    assert any(0 < done < 3 for done, _ in fake_cases.progress)
    assert fake_cases.progress[-1] == (3, 2)


@pytest.mark.db
async def test_job_stops_on_cancel_request(
    job_db: async_sessionmaker[AsyncSession],
    fake_cases: SimpleNamespace,
    tmp_path: Path,
) -> None:
    fake_cases.delay = 5.0
    run_id = await _queued(job_db)

    async def _cancel_soon() -> None:
        await asyncio.sleep(0.2)
        async with job_db() as db:
            await db.execute(
                text("UPDATE eval_runs SET cancel_requested = true WHERE id = :id"),
                {"id": run_id},
            )
            await db.commit()

    canceller = asyncio.create_task(_cancel_soon())
    await asyncio.wait_for(
        run_job.execute_run(run_id, session_factory=job_db, report_dir=tmp_path),
        timeout=4,
    )
    await canceller
    row = await _row(job_db, run_id)
    assert row.status == "cancelled"
    assert row.summary is None
    assert not await asyncio.to_thread(lambda: list(tmp_path.iterdir()))


@pytest.mark.db
async def test_job_records_a_failure(
    job_db: async_sessionmaker[AsyncSession],
    fake_cases: SimpleNamespace,
    tmp_path: Path,
) -> None:
    fake_cases.fail = True
    run_id = await _queued(job_db)
    await run_job.execute_run(run_id, session_factory=job_db, report_dir=tmp_path)
    row = await _row(job_db, run_id)
    assert row.status == "failed"
    assert "model unavailable" in (row.error or "")


@pytest.mark.db
async def test_job_skips_a_run_cancelled_before_it_started(
    job_db: async_sessionmaker[AsyncSession],
    fake_cases: SimpleNamespace,
    tmp_path: Path,
) -> None:
    run_id = await _queued(job_db)
    async with job_db() as db:
        run = await db.get(EvalRun, run_id)
        assert run is not None
        run.status = "cancelled"
        await db.commit()
    await run_job.execute_run(run_id, session_factory=job_db, report_dir=tmp_path)
    assert (await _row(job_db, run_id)).status == "cancelled"
    assert fake_cases.progress == []


@pytest.mark.db
async def test_heartbeat_refreshes_the_job_lock() -> None:
    engine = new_engine()
    await require_postgres(engine)
    job_type = f"test.heartbeat.{uuid.uuid4().hex[:8]}"
    try:
        await queue.enqueue(job_type, {}, engine=engine)
        (job,) = await queue.claim(1, "w1", [job_type], engine=engine)
        later = queue.utcnow() + timedelta(minutes=30)
        assert await queue.heartbeat(job.id, "w1", now=later, engine=engine)
        assert not await queue.heartbeat(job.id, "other", engine=engine)
        async with engine.connect() as conn:
            locked = await conn.scalar(
                text("SELECT locked_at FROM jobs WHERE id = :id"), {"id": job.id}
            )
        assert locked == later
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM jobs WHERE type = :t"), {"t": job_type}
            )
        await engine.dispose()


# ---------------------------------------------------------------------------
# Migration round trip
# ---------------------------------------------------------------------------

REVISION = "041c4335565e"
PREVIOUS = "144fe4240293"


def _alembic(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


@pytest.mark.db
async def test_eval_runs_migration_round_trip() -> None:
    engine = new_engine()
    await require_postgres(engine)
    table = text("SELECT to_regclass('public.eval_runs') IS NOT NULL")
    index = text("SELECT to_regclass('public.uq_eval_runs_one_active') IS NOT NULL")
    try:
        chain = await asyncio.to_thread(_alembic, "history", "-r", f"{PREVIOUS}:head")
        assert REVISION in chain
        await asyncio.to_thread(_alembic, "downgrade", PREVIOUS)
        async with engine.connect() as conn:
            assert await conn.scalar(table) is False
        await engine.dispose()
    finally:
        await asyncio.to_thread(_alembic, "upgrade", "head")
    async with engine.connect() as conn:
        assert await conn.scalar(table) is True
        assert await conn.scalar(index) is True
    await engine.dispose()
