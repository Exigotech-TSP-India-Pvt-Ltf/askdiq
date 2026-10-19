"""Governance Overall latency KPIs: p50 and p95 come from the same rows.

Regression for "Latency p50 3.4 ms" next to "p95 5.4 s": instant
conversational replies (no pipeline stage timed) were mixed into the
percentiles, so the median was a greeting while p95 was a RAG answer.

Pure-function tests run anywhere; the rollup test needs the CI Postgres
(``db_session`` fixture) and seeds rows inside the rolled-back transaction.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete

from app.governance import timeseries
from app.governance.overview import build_kpis, latency_population
from app.models.db_models import LatencyRecord


def _rollups(lat_total: dict, lat_hour: dict, start: datetime):
    lat = timeseries.Rollup(hours={start: lat_hour}, total=lat_total)
    empty = timeseries.Rollup(total={})
    return lat, empty


def test_latency_kpis_use_pipeline_rows_when_present():
    start = datetime(2026, 10, 19, 10)
    lat, empty = _rollups(
        {
            "p50_ms": 3.4,
            "p95_ms": 5400.0,
            "pipeline_requests": 40,
            "pipeline_p50_ms": 2100.0,
            "pipeline_p95_ms": 5400.0,
        },
        {
            "p50_ms": 3.1,
            "p95_ms": 5300.0,
            "pipeline_p50_ms": 2000.0,
            "pipeline_p95_ms": 5300.0,
        },
        start,
    )
    kpis = build_kpis([start], lat, empty, empty, empty)
    assert kpis["latency_p50"]["value"] == 2100.0
    assert kpis["latency_p95"]["value"] == 5400.0
    assert kpis["latency_p50"]["sparkline"] == [2000.0]
    assert kpis["latency_p95"]["sparkline"] == [5300.0]
    assert kpis["latency_p50"]["unit"] == kpis["latency_p95"]["unit"] == "ms"
    assert kpis["latency_p50"]["source"] == kpis["latency_p95"]["source"]
    assert kpis["latency_p50"]["source"] == "latency_metrics.pipeline"


def test_latency_kpis_fall_back_to_all_rows_without_pipeline_rows():
    start = datetime(2026, 10, 19, 10)
    lat, empty = _rollups(
        {"p50_ms": 120.0, "p95_ms": 300.0, "pipeline_requests": 0},
        {"p50_ms": 120.0, "p95_ms": 300.0},
        start,
    )
    assert latency_population(lat) == (("p50_ms", "p95_ms"), "latency_metrics")
    kpis = build_kpis([start], lat, empty, empty, empty)
    assert kpis["latency_p50"]["value"] == 120.0
    assert kpis["latency_p95"]["value"] == 300.0
    assert kpis["latency_p50"]["source"] == "latency_metrics"


async def test_latency_rollup_pipeline_percentiles_skip_instant_replies(db_session):
    await db_session.execute(delete(LatencyRecord))
    now = datetime.utcnow()
    rows = [
        # Conversational intercepts: a few ms, no stage timed.
        LatencyRecord(
            id=uuid.uuid4(), request_id=f"conv-{i}", created_at=now, total_ms=3.0
        )
        for i in range(30)
    ] + [
        LatencyRecord(
            id=uuid.uuid4(),
            request_id=f"rag-{i}",
            created_at=now,
            total_ms=float(1000 + i * 100),
            guardrail_pre_ms=1.0,
            llm_ms=float(800 + i * 100),
        )
        for i in range(11)
    ]
    db_session.add_all(rows)
    await db_session.flush()

    start = timeseries.hour_starts(now)[0]
    lat = await timeseries.latency_rollup(
        db_session, "overview", start, now - timedelta(minutes=5)
    )
    assert lat.total["requests"] == 41
    assert lat.total["p50_ms"] == 3.0  # the mixed population: the old bug
    assert lat.total["pipeline_requests"] == 11
    assert lat.total["pipeline_p50_ms"] == 1500.0
    assert lat.total["pipeline_p95_ms"] == 1950.0
