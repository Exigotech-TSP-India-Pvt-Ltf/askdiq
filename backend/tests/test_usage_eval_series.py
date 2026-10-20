"""Usage & Cost and Evaluation (Live) chart additions.

Cost by stage over time, month-end forecast, cache-hit ratio, evaluation
quality dimensions (current vs previous run) and the pass-rate trend. Pure
logic runs without a database; endpoint tests need Postgres (CI).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete

from app.api.routes import metrics
from app.core.config import get_settings
from app.dashboard import eval_series, usage_series
from app.models.db_models import EvaluationRecord
from tests.dashboard_helpers import exchange, iso, session_at, unique_day, unique_ip

# ---------------------------------------------------------------------------
# Usage (pure)
# ---------------------------------------------------------------------------


def test_stage_costs_split_cached_prompt_tokens() -> None:
    cfg = get_settings().model_copy(
        update={
            "price_embed_per_million": 0.02,
            "price_prompt_per_million": 0.25,
            "price_output_per_million": 2.0,
            "price_cached_per_million": 0.125,
        }
    )
    costs = usage_series.stage_costs(
        {
            "prompt": 1_000_000,
            "cached": 400_000,
            "completion": 300_000,
            "reasoning": 100_000,
            "query_embed": 500_000,
            "guardrail_embed": 250_000,
        },
        0.5,
        cfg,
    )
    assert costs == {
        "query_embed": 0.01,
        "guardrail_embed": 0.005,
        "prompt": 0.15,
        "cached_prompt": 0.05,
        "reasoning": 0.2,
        "visible": 0.4,
        "ingestion": 0.5,
    }


def test_series_bucket_and_zero_fill() -> None:
    end = datetime(2026, 10, 16, 12)
    assert usage_series.series_bucket(None, end) == "day"
    assert usage_series.series_bucket(end - timedelta(hours=48), end) == "hour"
    assert usage_series.series_bucket(end - timedelta(days=3), end) == "day"
    starts = usage_series.zero_fill_starts(
        "day", None, end, [datetime(2026, 10, 14)], max_days=400
    )
    assert starts == [datetime(2026, 10, d) for d in (14, 15, 16)]
    assert usage_series.zero_fill_starts("day", None, end, [], max_days=400) == []
    capped = usage_series.zero_fill_starts(
        "day", datetime(2020, 1, 1), end, [], max_days=2
    )
    assert capped[0] == datetime(2026, 10, 14)


def test_cache_ratio_null_without_prompt_tokens() -> None:
    assert usage_series.cache_stats(0, 0) == {
        "cached_prompt_tokens": 0,
        "prompt_tokens": 0,
        "hit_ratio": None,
    }
    assert usage_series.cache_stats(25, 100)["hit_ratio"] == 0.25


# ---------------------------------------------------------------------------
# Evaluation runs (pure)
# ---------------------------------------------------------------------------

FALLBACK = datetime(2026, 10, 1)


def test_run_from_report_maps_dimensions() -> None:
    run = eval_series.run_from_report(
        "evaluation_report",
        {
            "pass_rate": 0.86,
            "n_cases": 69,
            "retrieval_recall": 0.98,
            "groundedness_accuracy": 0.9,
            "judge": {"faithfulness": 4.5},
            "citation": {"precision": 0.44},
            "safety": {"passed": 0.89},
            "timestamp": "2026-10-13T15:20:00Z",
        },
        FALLBACK,
    )
    assert run is not None
    assert run.started_at == datetime(2026, 10, 13, 15, 20)
    assert run.n_cases == 69
    assert run.scores == {
        "retrieval_recall": 0.98,
        "groundedness": 0.9,
        "faithfulness": 0.9,
        "citation_precision": 0.44,
        "safety": 0.89,
        "pass_rate": 0.86,
    }


def test_report_without_measurements_is_null_not_invented() -> None:
    run = eval_series.run_from_report(
        "r", {"pass_rate": 0.5, "judge": {}, "cases": [{}, {}]}, FALLBACK
    )
    assert run is not None
    assert run.started_at == FALLBACK
    assert run.n_cases == 2
    assert run.scores["faithfulness"] is None
    assert run.scores["citation_precision"] is None
    assert eval_series.run_from_report("r", {"retrieval_recall": 1.0}, FALLBACK) is None
    assert eval_series.run_from_report("r", {"pass_rate": True}, FALLBACK) is None


def _run(run_id: str, day: int, pass_rate: float) -> eval_series.Run:
    return eval_series.Run(
        run_id,
        datetime(2026, 1, 1) + timedelta(days=day),
        10,
        pass_rate,
        {"pass_rate": pass_rate, "groundedness": None},
    )


def test_insight_fields_current_previous_and_trend() -> None:
    runs = [_run(f"r{i}", i, i / 40) for i in range(1, 34)]
    fields = eval_series.insight_fields(runs)
    trend = fields["pass_rate_trend"]
    assert len(trend) == eval_series.MAX_RUNS
    assert trend[0]["run_id"] == "r4"
    assert trend[-1] == {
        "run_id": "r33",
        "started_at": "2026-02-03T00:00:00Z",
        "n_cases": 10,
        "pass_rate": 0.825,
    }
    dims = {d["key"]: d for d in fields["quality_dimensions"]["dimensions"]}
    assert set(dims) == {k for k, _ in eval_series.DIMENSIONS}
    assert dims["pass_rate"]["current"] == 0.825
    assert dims["pass_rate"]["previous"] == 0.8
    assert dims["groundedness"]["current"] is None
    assert fields["quality_dimensions"]["current_run"]["run_id"] == "r33"
    assert fields["quality_dimensions"]["previous_run"]["run_id"] == "r32"


def test_insight_fields_single_and_no_runs() -> None:
    single = eval_series.insight_fields([_run("only", 2, 0.5)])
    assert single["quality_dimensions"]["previous_run"] is None
    assert all(
        d["previous"] is None for d in single["quality_dimensions"]["dimensions"]
    )
    empty = eval_series.insight_fields([])
    assert empty == {
        "quality_dimensions": {
            "current_run": None,
            "previous_run": None,
            "dimensions": [],
        },
        "pass_rate_trend": [],
    }


def _write(path: Path, payload: object, mtime: float) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, (mtime, mtime))


async def test_report_runs_read_files_oldest_first(tmp_path: Path) -> None:
    base = datetime(2026, 10, 1, tzinfo=UTC).timestamp()
    _write(tmp_path / "eval_report.json", {"pass_rate": 0.8, "n_cases": 13}, base)
    _write(
        tmp_path / "evaluation_report.json",
        {"pass_rate": 0.9, "n_cases": 69},
        base + 3600,
    )
    _write(tmp_path / "eval_report_bad.json", ["not", "a", "report"], base)
    (tmp_path / "eval_report_broken.json").write_text("{", encoding="utf-8")
    _write(tmp_path / "conc_10.json", {"pass_rate": 1.0}, base)
    runs = await eval_series.report_runs([tmp_path, tmp_path / "missing"])
    assert [r.run_id for r in runs] == ["eval_report", "evaluation_report"]
    assert runs[0].started_at == datetime(2026, 10, 1)


# ---------------------------------------------------------------------------
# GET /admin/usage/summary additions (real Postgres)
# ---------------------------------------------------------------------------


async def test_usage_summary_series_cache_and_forecast(client, db_session) -> None:
    cfg = get_settings()
    d0 = unique_day()
    session = await session_at(db_session, d0, unique_ip())
    tokens = {
        "prompt": 1000,
        "completion": 300,
        "total": 1300,
        "reasoning": 100,
        "embedding": 50,
        "guardrail": 20,
        "details": {"cached_tokens": 400},
    }
    await exchange(db_session, session, 0, d0 + timedelta(hours=2), tokens=tokens)
    await exchange(
        db_session,
        session,
        1,
        d0 + timedelta(days=2, hours=1),
        tokens={**tokens, "details": {"cached_tokens": "n/a"}},
    )
    params = {"from_date": iso(d0), "to_date": iso(d0 + timedelta(days=2, hours=23))}
    resp = await client.get("/admin/usage/summary", params=params)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["cache"] == {
        "cached_prompt_tokens": 400,
        "prompt_tokens": 2000,
        "hit_ratio": 0.2,
    }
    stages = data["financial"]["stage_costs"]
    assert stages["cached_prompt_cost"] == pytest.approx(
        400 * cfg.price_cached_per_million / 1e6
    )
    assert stages["prompt_uncached_cost"] == pytest.approx(
        1600 * cfg.price_prompt_per_million / 1e6
    )
    assert data["tokens"]["llm_tokens"]["cached_prompt_tokens"] == 400

    series = data["cost_by_stage_series"]
    assert series["bucket"] == "day"
    points = series["points"]
    assert [p["t"] for p in points] == [
        (d0 + timedelta(days=i)).date().isoformat() for i in range(3)
    ]
    assert points[1] == {
        "t": (d0 + timedelta(days=1)).date().isoformat(),
        "query_embed": 0.0,
        "guardrail_embed": 0.0,
        "prompt": 0.0,
        "cached_prompt": 0.0,
        "reasoning": 0.0,
        "visible": 0.0,
        "ingestion": 0.0,
    }
    assert points[0]["cached_prompt"] == pytest.approx(
        400 * cfg.price_cached_per_million / 1e6
    )
    assert points[2]["cached_prompt"] == 0.0
    assert points[2]["prompt"] == pytest.approx(
        1000 * cfg.price_prompt_per_million / 1e6
    )
    # The series reconciles with the summary's stage totals.
    for key, stage in (
        ("prompt", "prompt_uncached_cost"),
        ("cached_prompt", "cached_prompt_cost"),
        ("reasoning", "reasoning_cost"),
        ("visible", "visible_cost"),
        ("query_embed", "query_embed_cost"),
        ("guardrail_embed", "guardrail_embed_cost"),
    ):
        assert sum(p[key] for p in points) == pytest.approx(stages[stage], abs=2e-6)

    forecast = data["forecast"]
    today = datetime.now(UTC).date()
    assert forecast["currency"] == "USD"
    assert forecast["method"] == "linear"
    assert forecast["month"] == f"{today.year:04d}-{today.month:02d}"
    assert len(forecast["daily"]) >= 28
    assert forecast["daily"][today.day - 1]["actual_usd"] == forecast["mtd_usd"]


async def test_usage_summary_hourly_series_for_short_range(client, db_session) -> None:
    d0 = unique_day()
    session = await session_at(db_session, d0, unique_ip())
    await exchange(
        db_session,
        session,
        0,
        d0 + timedelta(hours=3),
        tokens={"prompt": 10, "completion": 5},
    )
    params = {"from_date": iso(d0), "to_date": iso(d0 + timedelta(hours=5))}
    data = (await client.get("/admin/usage/summary", params=params)).json()
    series = data["cost_by_stage_series"]
    assert series["bucket"] == "hour"
    assert len(series["points"]) == 6
    assert series["points"][3]["prompt"] > 0


# ---------------------------------------------------------------------------
# GET /metrics/evaluation/live additions (real Postgres)
# ---------------------------------------------------------------------------


async def test_live_evaluation_runs_are_utc_days(client, db_session) -> None:
    day1 = datetime(2099, 3, 1) + timedelta(days=uuid.uuid4().int % 200)
    day2 = day1 + timedelta(days=1)
    # The live view reads the latest 300 records: rows other tests committed
    # (with measured stage latencies) must not count. Rolled back afterwards.
    await db_session.execute(delete(EvaluationRecord))
    for created, passed, grounded, faith in (
        (day1 + timedelta(hours=1), True, True, 5.0),
        (day1 + timedelta(hours=2), False, False, 3.0),
        (day2 + timedelta(hours=1), True, True, 4.0),
    ):
        db_session.add(
            EvaluationRecord(
                request_id=f"ev-{uuid.uuid4().hex[:12]}",
                query="q",
                answer="a",
                passed=passed,
                grounded=grounded,
                faithfulness=faith,
                retrieval_recall=1.0,
                safety_passed=True,
                created_at=created,
            )
        )
    await db_session.flush()
    resp = await client.get("/metrics/evaluation/live", params={"audience": "users"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    qd = data["quality_dimensions"]
    assert qd["current_run"]["run_id"] == f"live-{day2.date().isoformat()}"
    assert qd["current_run"]["n_cases"] == 1
    assert qd["previous_run"]["run_id"] == f"live-{day1.date().isoformat()}"
    dims = {d["key"]: d for d in qd["dimensions"]}
    assert dims["pass_rate"] == {
        "key": "pass_rate",
        "label": "Pass rate",
        "current": 1.0,
        "previous": 0.5,
    }
    assert dims["groundedness"]["previous"] == 0.5
    assert dims["faithfulness"]["current"] == pytest.approx(0.8)
    assert dims["faithfulness"]["previous"] == pytest.approx(0.8)
    assert dims["citation_precision"]["current"] is None
    trend = data["pass_rate_trend"]
    assert trend[-1]["run_id"] == qd["current_run"]["run_id"]
    assert trend[-2]["pass_rate"] == 0.5
    # Unmeasured live metrics are absent, never constants or multiples.
    assert data["retrieval"]["mrr"] is None
    assert data["retrieval"]["recall@1"] is None
    assert data["citation"]["quality"]["correct_document"] is None
    assert data["safety"]["injection_compliance_rate"] is None
    assert data["robustness"]["by_perturbation"] == {}
    assert data["abstention"]["false_abstention_rate"] is None
    assert "embedding" not in data["operational"]["latency_ms"]
    assert "retrieval.mrr" in data["not_measured"]


async def test_benchmark_runs_come_from_report_files(
    client, db_session, tmp_path, monkeypatch
) -> None:
    base = datetime(2026, 10, 1, tzinfo=UTC).timestamp()
    _write(
        tmp_path / "eval_report.json",
        {"pass_rate": 0.8, "n_cases": 13, "retrieval_recall": 0.9, "cases": []},
        base,
    )
    _write(
        tmp_path / "evaluation_report.json",
        {"pass_rate": 0.9, "n_cases": 69, "retrieval_recall": 0.95, "cases": []},
        base + 60,
    )
    monkeypatch.setattr(metrics, "_eval_report_dirs", lambda: [tmp_path])
    resp = await client.get("/metrics/evaluation/live", params={"audience": "testing"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    qd = data["quality_dimensions"]
    assert qd["current_run"] == {
        "run_id": "evaluation_report",
        "started_at": "2026-10-01T00:01:00Z",
        "n_cases": 69,
    }
    assert qd["previous_run"]["run_id"] == "eval_report"
    dims = {d["key"]: d for d in qd["dimensions"]}
    assert dims["retrieval_recall"]["current"] == 0.95
    assert dims["retrieval_recall"]["previous"] == 0.9
    assert dims["safety"]["current"] is None
    assert [p["pass_rate"] for p in data["pass_rate_trend"]] == [0.8, 0.9]
