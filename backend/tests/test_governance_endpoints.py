"""Governance endpoints (planchanges4 B4.2.1-B4.2.5, newplan §4.5).

Pure tests (cursor, thresholds, buckets, stream hub, validation) run anywhere;
tests using ``client`` / ``db_session`` need the CI Postgres and seed their
rows inside the rolled-back test transaction.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api.routes.admin_governance import _csv_line
from app.auth.permissions import Permission
from app.core.config import get_settings
from app.core.events import TOPIC_GOVERNANCE_INCIDENT, Event, EventBus
from app.core.sse import SSEEvent
from app.governance import overview as overview_mod
from app.governance.audience import invalidate_audience_cache
from app.governance.log_summary import level_for, service_for
from app.governance.monitoring import derive_trace_status
from app.governance.overview import (
    DEGRADED,
    HEALTHY,
    INCIDENT,
    OVERVIEW_MAX_QUERIES,
    classify,
    health_status,
)
from app.governance.pagination import Cursor
from app.governance.quota_series import QuotaSeries
from app.governance.stream import GovernanceStreamHub, diff_events, get_governance_hub
from app.governance.timeseries import hour_starts
from app.governance.traces import latency_bucket
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    LatencyRecord,
    TestIpConfig,
)
from main import app
from tests.conftest import test_engine
from tests.governance_helpers import (
    LEVEL_FIXTURES,
    QueryCounter,
    audit_rows,
    chat_session,
    iso,
    parse_frames,
    read_sse,
    trace_rows,
    unique_window,
)

BASE = "/admin/governance"
settings = get_settings()


@pytest.fixture
async def raw_client():
    """No DB override: for validation errors raised before any query."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-API-Key": settings.api_key_name or "ci-test-key"},
    ) as ac:
        yield ac


# ===========================================================================
# Pure units
# ===========================================================================


def test_cursor_round_trip_and_opaque():
    cur = Cursor(datetime(2026, 1, 2, 3, 4, 5, 678), uuid.uuid4())
    token = cur.encode()
    assert "2026" not in token  # opaque base64url
    assert Cursor.decode(token) == cur
    assert Cursor.decode(None) is None
    assert Cursor.decode("") is None


@pytest.mark.parametrize(
    "token",
    [
        "not-base64-$$$",
        "eyJ0IjoxfQ",  # {"t":1} -> missing id
        "x" * 500,
        Cursor(datetime(2026, 1, 1), uuid.uuid4()).encode()[:-4] + "AAAA",
    ],
)
def test_malformed_cursor_is_422(token):
    with pytest.raises(HTTPException) as exc:
        Cursor.decode(token)
    assert exc.value.status_code == 422


def test_cursor_rejects_aware_timestamps():
    import base64

    raw = json.dumps({"t": "2026-01-01T00:00:00+00:00", "id": str(uuid.uuid4())})
    token = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    with pytest.raises(HTTPException):
        Cursor.decode(token)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, HEALTHY),
        (0.0, HEALTHY),
        (1.999, HEALTHY),
        (2.0, DEGRADED),
        (9.999, DEGRADED),
        (10.0, INCIDENT),
        (55.0, INCIDENT),
    ],
)
def test_classify_threshold_boundaries(value, expected):
    assert classify(value, 2.0, 10.0) == expected


def _thresholds():
    return settings.model_copy(
        update={
            "governance_health_error_rate_degraded_pct": 2.0,
            "governance_health_error_rate_incident_pct": 10.0,
            "governance_health_p95_degraded_ms": 8000.0,
            "governance_health_p95_incident_ms": 20000.0,
            "governance_health_quota_degraded_pct": 80.0,
            "governance_health_quota_incident_pct": 95.0,
        }
    )


@pytest.mark.parametrize(
    ("error_rate", "p95", "quota", "expected"),
    [
        (0.0, 100.0, 0.0, HEALTHY),
        (1.99, 7999.0, 79.99, HEALTHY),
        (2.0, 100.0, 0.0, DEGRADED),
        (0.0, 8000.0, 0.0, DEGRADED),
        (0.0, 100.0, 80.0, DEGRADED),
        (10.0, 100.0, 0.0, INCIDENT),
        (0.0, 20000.0, 0.0, INCIDENT),
        (0.0, 100.0, 95.0, INCIDENT),
        (2.5, 20000.0, 81.0, INCIDENT),  # worst signal wins
        (None, None, None, HEALTHY),
    ],
)
def test_health_status_per_signal_boundaries(error_rate, p95, quota, expected):
    health = health_status(error_rate, p95, quota, _thresholds())
    assert health["status"] == expected
    assert set(health["signals"]) == {"error_rate", "p95_latency", "quota_utilization"}
    assert health["signals"]["error_rate"]["degraded_at"] == 2.0
    assert health["signals"]["p95_latency"]["incident_at"] == 20000.0


@pytest.mark.parametrize(
    ("ms", "bucket"),
    [
        (None, None),
        (0, "fast"),
        (999.9, "fast"),
        (1000, "moderate"),
        (2999, "moderate"),
        (3000, "slow"),
        (9999, "slow"),
        (10000, "very_slow"),
    ],
)
def test_latency_buckets_follow_config(ms, bucket):
    assert latency_bucket(ms) == bucket


def test_hour_starts_are_24_contiguous_hours_ending_now():
    now = datetime(2026, 10, 15, 12, 34, 56)
    starts = hour_starts(now)
    assert len(starts) == 24
    assert starts[-1] == datetime(2026, 10, 15, 12)
    assert starts[0] == datetime(2026, 10, 14, 13)
    assert all(
        b - a == timedelta(hours=1) for a, b in zip(starts, starts[1:], strict=False)
    )


@pytest.mark.parametrize("cell", ["=SUM(A1)", "+1", "-2", "@cmd", "\tx", "\rx"])
def test_csv_cells_starting_with_formula_chars_are_prefixed(cell):
    row = next(csv.reader(io.StringIO(_csv_line([cell, "safe"]))))
    assert row == [f"'{cell}", "safe"]


def test_quota_series_is_throttled_and_bounded():
    series = QuotaSeries(points=3, every=60)
    for t in (0, 10, 59, 60, 120, 180, 240):
        series.sample(now=float(t))
    points = series.points()
    assert len(points) == 3  # bounded ring
    assert {"ts", "utilization_pct", "chat_tpm_headroom_pct"} <= set(points[0])


def test_level_and_service_fixtures_match_python_rules():
    for action, detail, level, service in LEVEL_FIXTURES:
        assert level_for(detail, action) == level
        assert service_for(action) == service
    assert level_for({"grounded": False}, "query") == "WARNING"
    assert level_for(None, "unknown") == "INFO"


# --------------------------------------------------------------------------
# Stream hub (no DB: stub snapshots, local event-bus dispatch)
# --------------------------------------------------------------------------


def _snapshot(status: str = HEALTHY, incidents=(), requests: int = 0) -> dict:
    return {
        "health": {"status": status},
        "incidents": [{"id": i, "summary": f"incident {i}"} for i in incidents],
        "kpis": {"requests": {"value": requests}},
    }


def test_diff_events_snapshot_health_incident_and_delta():
    first = diff_events(None, _snapshot())
    assert [e.event for e in first] == ["snapshot"]
    assert diff_events(_snapshot(), _snapshot()) == []
    events = diff_events(
        _snapshot(incidents=["a"]),
        _snapshot(INCIDENT, incidents=["c", "b", "a"], requests=5),
    )
    assert [e.event for e in events] == ["health", "incident", "incident", "monitoring"]
    assert events[0].data["previous"] == HEALTHY
    assert [e.id for e in events[1:3]] == ["b", "c"]  # oldest first
    assert events[3].data == {"kpis": {"requests": 5}}


class _CountingSnapshots:
    def __init__(self) -> None:
        self.calls = 0
        self.incidents: list[str] = []

    async def __call__(self, audience: str) -> dict:
        self.calls += 1
        return _snapshot(incidents=list(reversed(self.incidents)))


async def _wait_for(predicate, attempts: int = 500) -> None:
    """Poll a hub counter (10 ms steps, at most ~5 s)."""
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def _drain(gen, n: int) -> list[SSEEvent]:
    return [await asyncio.wait_for(anext(gen), 5) for _ in range(n)]


async def test_stream_one_producer_for_50_subscribers_and_incident_fan_out():
    bus = EventBus(dsn="postgresql://unused")
    snapshots = _CountingSnapshots()
    hub = GovernanceStreamHub(snapshot_fn=snapshots, interval=0.05, bus=bus)
    try:
        # Baseline: one subscriber, measure snapshot calls per tick.
        solo = hub.subscribe("overview")
        await _drain(solo, 1)
        await _wait_for(lambda: hub.ticks["overview"] >= 5)
        assert snapshots.calls == hub.ticks["overview"]

        gens = [hub.subscribe("overview") for _ in range(49)]
        for gen in gens:
            first = await _drain(gen, 1)
            assert first[0].event == "snapshot"
        assert hub.subscriber_count("overview") == 50
        assert hub.producer_count() == 1

        before_calls, before_ticks = snapshots.calls, hub.ticks["overview"]
        await _wait_for(lambda: hub.ticks["overview"] >= before_ticks + 5)
        # DB load (snapshot computations) == ticks, independent of subscribers.
        assert snapshots.calls - before_calls == hub.ticks["overview"] - before_ticks

        # A writer NOTIFYs a new incident: every subscriber receives it.
        snapshots.incidents.append("inc-1")
        bus.dispatch(Event(TOPIC_GOVERNANCE_INCIDENT, "inc-1", origin="other"))
        for gen in [solo, *gens]:
            async with asyncio.timeout(5):
                while True:
                    event = await anext(gen)
                    if event.event == "incident":
                        break
            assert event.id == "inc-1"
    finally:
        for gen in [solo, *gens]:
            await gen.aclose()
        assert hub.producer_count() == 0  # last subscriber left -> producer stops
        await hub.aclose()


async def test_stream_notify_wakes_producer_before_interval():
    bus = EventBus(dsn="postgresql://unused")
    snapshots = _CountingSnapshots()
    hub = GovernanceStreamHub(snapshot_fn=snapshots, interval=60, bus=bus)
    gen = hub.subscribe("overview")
    try:
        await _drain(gen, 1)
        snapshots.incidents.append("inc-9")
        bus.dispatch(Event(TOPIC_GOVERNANCE_INCIDENT, "inc-9", origin="x"))
        event = await asyncio.wait_for(anext(gen), 5)
        assert (event.event, event.id) == ("incident", "inc-9")
        assert snapshots.calls == 2
    finally:
        await gen.aclose()
        await hub.aclose()


async def test_stream_route_sends_sse_frames():
    bus = EventBus(dsn="postgresql://unused")
    hub = GovernanceStreamHub(snapshot_fn=_CountingSnapshots(), interval=60, bus=bus)
    app.dependency_overrides[get_governance_hub] = lambda: hub
    try:
        start, body = await read_sse(app, f"{BASE}/stream?audience=overview", 2)
    finally:
        app.dependency_overrides.pop(get_governance_hub, None)
        await hub.aclose()
    assert start["status"] == 200
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["cache-control"] == "no-cache"
    frames = parse_frames(body)
    assert frames[0].get("retry")
    assert frames[1]["event"] == "snapshot"
    assert json.loads(frames[1]["data"])["health"]["status"] == HEALTHY


# --------------------------------------------------------------------------
# Validation and OpenAPI (no DB: rejected before any query)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/logs?limit=201",
        f"{BASE}/logs?limit=0",
        f"{BASE}/logs?level=DEBUG",
        f"{BASE}/logs?service=evil",
        f"{BASE}/logs?cursor=bad!token",
        f"{BASE}/logs?q=" + "x" * 201,
        f"{BASE}/logs?from=2026-02-01T00:00:00&to=2026-01-01T00:00:00",
        f"{BASE}/logs?from=yesterday",
        f"{BASE}/logs?audience=everyone",
        f"{BASE}/logs/export.csv?level=nope",
        f"{BASE}/traces/page?limit=500",
        f"{BASE}/traces/page?status=great",
        f"{BASE}/traces/page?latency_bucket=instant",
        f"{BASE}/traces/page?outcome=fallback",
        f"{BASE}/traces/page?cursor=abc",
        f"{BASE}/traces/bad%20id%3B%20drop",
        f"{BASE}/overview?audience=all",
        f"{BASE}/monitoring?audience=x",
        f"{BASE}/stream?audience=x",
    ],
)
async def test_invalid_input_is_422(raw_client, url):
    response = await raw_client.get(url)
    assert response.status_code == 422, response.text


async def test_routes_require_service_key(raw_client):
    for path in ("logs", "logs/export.csv", "traces/page", "overview", "stream"):
        response = await raw_client.get(f"{BASE}/{path}", headers={"X-API-Key": ""})
        assert response.status_code == 401


def test_openapi_documents_every_governance_route():
    paths = app.openapi()["paths"]
    expected = {
        f"{BASE}/logs": "GovernanceLogPage",
        f"{BASE}/traces/page": "GovernanceTracePage",
        f"{BASE}/traces/{{request_id}}": "GovernanceTraceDetail",
        f"{BASE}/overview": "GovernanceOverview",
    }
    for path, model in expected.items():
        op = paths[path]["get"]
        assert op["summary"] and op["description"], path
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith(model), path
        assert {"401", "403", "422"} <= set(op["responses"]), path
    assert "404" in paths[f"{BASE}/traces/{{request_id}}"]["get"]["responses"]
    assert (
        "text/csv"
        in paths[f"{BASE}/logs/export.csv"]["get"]["responses"]["200"]["content"]
    )
    assert (
        "text/event-stream"
        in paths[f"{BASE}/stream"]["get"]["responses"]["200"]["content"]
    )


# Bulk CSV export is a data export: it needs usage:export, not dashboard:read.
EXPORT_ROUTES = frozenset({f"{BASE}/logs/export.csv"})


def test_all_governance_routes_need_dashboard_read():
    from tests.test_route_guards import api_routes, requirement

    routes = [r for r in api_routes() if r.path.startswith(f"{BASE}/")]
    assert len(routes) >= 8
    for route in routes:
        expected = (
            Permission.USAGE_EXPORT
            if route.path in EXPORT_ROUTES
            else Permission.DASHBOARD_READ
        )
        assert requirement(route) == str(expected), route.path


# ===========================================================================
# Endpoint tests (CI Postgres)
# ===========================================================================


async def _logs(client, **params) -> dict:
    response = await client.get(f"{BASE}/logs", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_logs_cursor_walks_1000_rows_without_duplicates_or_gaps(
    client, db_session
):
    start, end = unique_window()
    # Ties on created_at (4 rows per timestamp) exercise the id tie-breaker.
    rows = audit_rows(start, 1000, same_timestamp_every=4)
    db_session.add_all(rows)
    await db_session.flush()

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"from": iso(start), "to": iso(end), "limit": 200}
        if cursor:
            params["cursor"] = cursor
        page = await _logs(client, **params)
        pages += 1
        seen.extend(item["id"] for item in page["items"])
        assert page["total"] == 1000
        cursor = page["next_cursor"]
        assert page["has_more"] is (cursor is not None)
        if not cursor:
            break
    assert pages == 5
    assert len(seen) == len(set(seen)) == 1000
    expected = sorted(rows, key=lambda r: (r.created_at, r.id), reverse=True)
    assert seen == [str(r.id) for r in expected]


async def test_logs_level_service_filters_match_python_rules(client, db_session):
    start, end = unique_window()
    db_session.add_all(audit_rows(start, 50))
    await db_session.flush()
    window = {"from": iso(start), "to": iso(end)}

    everything = await _logs(client, **window)
    assert everything["total"] == 50
    assert everything["level_counts"] == {
        "CRITICAL": 10,
        "ALERT": 10,
        "ERROR": 10,
        "WARNING": 10,
        "INFO": 10,
    }
    for _, _, level, _ in LEVEL_FIXTURES:
        page = await _logs(client, level=level, **window)
        assert page["total"] == 10
        assert {i["level"] for i in page["items"]} == {level}
        # Chip counts ignore the level filter itself.
        assert page["level_counts"] == everything["level_counts"]
    two = await _logs(client, level=["CRITICAL", "ERROR"], **window)
    assert two["total"] == 20
    for service in ("query", "feedback", "system", "ingest"):
        page = await _logs(client, service=service, **window)
        assert {i["service"] for i in page["items"]} == {service}
    system = await _logs(client, service="system", **window)
    assert system["total"] == 20
    assert (await _logs(client, service="auth", **window))["total"] == 0


async def test_logs_q_is_like_escaped_and_injection_is_inert(client, db_session):
    start, end = unique_window()
    db_session.add_all(audit_rows(start, 10, detail_extra={"note": "needle-xyz"}))
    db_session.add_all(
        audit_rows(start + timedelta(hours=1), 5, detail_extra={"note": "50% off"})
    )
    await db_session.flush()
    window = {"from": iso(start), "to": iso(end)}

    assert (await _logs(client, q="needle-xyz", **window))["total"] == 10
    assert (await _logs(client, q="50%", **window))["total"] == 5
    assert (await _logs(client, q="%", **window))["total"] == 5  # literal only
    for payload in ("' OR '1'='1", "x'); DROP TABLE audit_log; --", "\\", "_%_"):
        page = await _logs(client, q=payload, **window)
        assert page["total"] == 0, payload
    # Table still there and untouched.
    assert (await _logs(client, **window))["total"] == 15


async def test_logs_total_switches_to_estimate_above_cap(
    client, db_session, monkeypatch
):
    start, end = unique_window()
    db_session.add_all(audit_rows(start, 30))
    await db_session.flush()
    monkeypatch.setattr(settings, "governance_exact_count_cap", 10)
    page = await _logs(client, **{"from": iso(start), "to": iso(end)})
    assert page["total_is_estimate"] is True
    assert page["level_counts_capped"] is True
    assert page["total_estimate"] == page["total"] >= 11
    assert sum(page["level_counts"].values()) == 11


async def test_logs_export_streams_sanitized_csv_and_is_audited(client, db_session):
    start, end = unique_window()
    db_session.add_all(
        audit_rows(
            start,
            6,
            fixtures=[("=cmd|' /C calc'!A0", {"x": 1}, "INFO", "system")],
        )
    )
    await db_session.flush()
    response = await client.get(
        f"{BASE}/logs/export.csv", params={"from": iso(start), "to": iso(end)}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))
    assert rows[0][:4] == ["Created At", "Level", "Service", "Action"]
    assert len(rows) == 7
    assert all(row[3].startswith("'=") for row in rows[1:])
    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "governance_logs_export")
            )
        )
        .scalars()
        .all()
    )
    assert any(a.detail["filters"]["from"] == iso(start) for a in audit)


async def _page(client, **params) -> dict:
    response = await client.get(f"{BASE}/traces/page", params=params)
    assert response.status_code == 200, response.text
    return response.json()


_STATUS_CASES = [
    ("answered", [], True),
    ("conversational", [], True),
    ("ungrounded", ["groundedness_check_failed"], True),
    ("fallback", [], True),
    ("blocked", ["unsafe_input_detected"], True),
    ("llm_failed", ["llm_call_failed"], True),
    ("empty_llm_output", None, True),
    (None, ["spam_detected"], True),
    (None, ["possible_hallucination"], True),
    (None, [], True),
    (None, None, True),
    (None, None, False),  # no persisted turn
]


async def test_trace_status_filter_matches_python_derivation(client, db_session):
    start, end = unique_window()
    session = await chat_session(db_session)
    expected: dict[str, str] = {}
    for i, (outcome, flags, with_turn) in enumerate(_STATUS_CASES):
        rid, rows = trace_rows(
            session,
            start + timedelta(minutes=i),
            total_ms=500,
            outcome=outcome,
            flags=flags,
            with_turn=with_turn,
        )
        db_session.add_all(rows)
        expected[rid] = derive_trace_status(outcome, flags) if with_turn else "unknown"
    await db_session.flush()
    window = {"from": iso(start), "to": iso(end), "limit": 200}

    page = await _page(client, **window)
    assert page["total"] == len(_STATUS_CASES)
    assert {i["request_id"]: i["status"] for i in page["items"]} == expected
    for status in ("ok", "ungrounded", "blocked", "failed", "unknown"):
        filtered = await _page(client, status=status, **window)
        want = {rid for rid, s in expected.items() if s == status}
        assert {i["request_id"] for i in filtered["items"]} == want, status
        assert filtered["total"] == len(want)


async def test_trace_page_filters_tokens_cost_and_slowest_stage(client, db_session):
    start, end = unique_window()
    session = await chat_session(db_session)
    fast_id, fast = trace_rows(
        session,
        start,
        total_ms=400,
        outcome="answered",
        flags=[],
        query="refund policy",
        stages={"embedding": 20, "retrieval": 50, "llm": 300},
        tokens=(120, 30),
        cost=0.002,
    )
    slow_id, slow = trace_rows(
        session,
        start + timedelta(minutes=1),
        total_ms=12000,
        outcome="llm_failed",
        flags=["llm_call_failed"],
        query="travel rules",
        stages={"retrieval": 9000, "llm": 1000},
    )
    db_session.add_all([*fast, *slow])
    await db_session.flush()
    window = {"from": iso(start), "to": iso(end)}

    item = (await _page(client, latency_bucket="fast", **window))["items"]
    assert [i["request_id"] for i in item] == [fast_id]
    fast_item = item[0]
    assert fast_item["tokens"]["prompt"] == 120
    assert fast_item["tokens"]["total"] == 150
    assert fast_item["cost_usd"] == pytest.approx(0.002)
    assert fast_item["slowest_stage"] == {"stage": "llm", "ms": 300.0, "unit": "ms"}
    assert fast_item["query"] == "refund policy"
    assert fast_item["conversation_id"] == str(session.id)

    vs = await _page(client, latency_bucket="very_slow", **window)
    assert [i["request_id"] for i in vs["items"]] == [slow_id]
    assert vs["items"][0]["slowest_stage"]["stage"] == "retrieval"
    both = await _page(client, latency_bucket=["fast", "very_slow"], **window)
    assert both["total"] == 2
    assert [
        i["request_id"]
        for i in (await _page(client, outcome="llm_failed", **window))["items"]
    ] == [slow_id]
    assert [
        i["request_id"] for i in (await _page(client, q="REFUND", **window))["items"]
    ] == [fast_id]
    assert (await _page(client, q="' OR 1=1 --", **window))["total"] == 0


async def test_legacy_traces_array_is_unchanged_and_enriched(client, db_session):
    session = await chat_session(db_session)
    rid, rows = trace_rows(
        session, datetime.utcnow(), total_ms=900, outcome="answered", flags=[]
    )
    db_session.add_all(rows)
    await db_session.flush()
    response = await client.get(f"{BASE}/traces?limit=500")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert int(response.headers["X-Total-Count"]) >= 1
    item = next(t for t in response.json() if t["request_id"] == rid)
    assert item["status"] == "ok"
    assert item["latency_bucket"] == "fast"
    assert "trace_id" not in item


async def test_trace_detail_matches_fixture(client, db_session):
    session = await chat_session(db_session)
    chunk_id = str(uuid.uuid4())
    rid, rows = trace_rows(
        session,
        datetime.utcnow(),
        total_ms=1000,
        outcome="ungrounded",
        flags=["groundedness_check_failed"],
        stages={"guardrail_pre": 10, "embedding": 40, "retrieval": 150, "llm": 600},
        sources=[
            {"chunk_id": chunk_id, "document_id": "d-1", "score": 0.8123},
        ],
    )
    db_session.add_all(rows)
    await db_session.flush()

    response = await client.get(f"{BASE}/traces/{rid}")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["request_id"] == rid
    assert detail["status"] == "ungrounded"
    assert detail["conversation_id"] == str(session.id)
    assert detail["flags"] == ["groundedness_check_failed"]
    assert detail["sources"] == [
        {
            "chunk_id": chunk_id,
            "document_id": "d-1",
            "source_name": None,
            "score": 0.8123,
        }
    ]
    stages = {s["stage"]: s for s in detail["stages"]["stages"]}
    assert stages["guardrail_pre"] == {
        "stage": "guardrail_pre",
        "start_ms": 0.0,
        "duration_ms": 10.0,
    }
    assert stages["embedding"]["start_ms"] == 10.0
    assert stages["retrieval"]["start_ms"] == 50.0
    assert stages["reranker"]["duration_ms"] is None
    assert stages["llm"]["start_ms"] == 200.0
    assert detail["stages"]["other_ms"] == 200.0


async def test_trace_detail_unknown_is_404(client):
    response = await client.get(f"{BASE}/traces/does-not-exist-{uuid.uuid4().hex}")
    assert response.status_code == 404


async def test_trace_detail_respects_audience_scope(client, db_session):
    test_ip = "203.0.113.71"
    db_session.add(TestIpConfig(ip_address=test_ip, description="pr10"))
    session = await chat_session(db_session, ip=test_ip)
    rid, rows = trace_rows(
        session,
        datetime.utcnow(),
        total_ms=100,
        outcome="answered",
        flags=[],
        ip=test_ip,
    )
    db_session.add_all(rows)
    await db_session.flush()
    invalidate_audience_cache()
    try:
        assert (
            await client.get(f"{BASE}/traces/{rid}?audience=testing")
        ).status_code == 200
        assert (
            await client.get(f"{BASE}/traces/{rid}?audience=users")
        ).status_code == 404
        assert (await client.get(f"{BASE}/traces/{rid}")).status_code == 200
        users = await _page(client, audience="users", q=rid)
        assert users["total"] == 0
        testing = await _page(client, audience="testing", q=rid)
        assert [i["request_id"] for i in testing["items"]] == [rid]
    finally:
        invalidate_audience_cache()


async def _monitoring(client) -> dict:
    response = await client.get(f"{BASE}/monitoring")
    assert response.status_code == 200, response.text
    return response.json()


async def test_monitoring_percentiles_are_exact(client, db_session):
    await db_session.execute(delete(LatencyRecord))
    now = datetime.utcnow()
    db_session.add_all(
        LatencyRecord(
            request_id=f"pct-{v}",
            created_at=now,
            total_ms=float(v),
            llm_ms=float(v) * 10,
        )
        for v in range(1, 102)
    )
    await db_session.flush()
    data = await _monitoring(client)
    total = data["stage_percentiles"]["total"]
    assert (total["p50_ms"], total["p95_ms"], total["p99_ms"]) == (51.0, 96.0, 100.0)
    assert total["sample_count"] == 101
    llm = data["stage_percentiles"]["llm"]
    assert (llm["p50_ms"], llm["p95_ms"]) == (510.0, 960.0)
    assert data["stage_percentiles"]["reranker"] == {
        "p50_ms": None,
        "p95_ms": None,
        "p99_ms": None,
        "sample_count": 0,
        "unit": "ms",
    }
    assert data["p95_latency_ms"] == 96.0


async def test_monitoring_hourly_buckets_are_zero_filled(client, db_session):
    await db_session.execute(delete(LatencyRecord))
    await db_session.execute(delete(ChatMessageRecord))
    now = datetime.utcnow()
    two_h = now - timedelta(hours=2)
    db_session.add_all(
        [
            LatencyRecord(request_id="hb-1", created_at=two_h, total_ms=100),
            LatencyRecord(request_id="hb-2", created_at=two_h, total_ms=300),
        ]
    )
    session = await chat_session(db_session)
    for outcome in ("answered", "llm_failed"):
        db_session.add(
            ChatMessageRecord(
                session_id=session.id,
                turn_index=0,
                role="assistant",
                content="a",
                created_at=now - timedelta(hours=5),
                outcome=outcome,
                guardrail_flags=["low_retrieval_confidence"],
            )
        )
    await db_session.flush()

    data = await _monitoring(client)
    hourly = data["hourly"]
    assert len(hourly) == 24
    hours = [datetime.fromisoformat(h["hour"].rstrip("Z")) for h in hourly]
    assert all(
        b - a == timedelta(hours=1) for a, b in zip(hours, hours[1:], strict=False)
    )
    by_hour = {h["hour"]: h for h in hourly}
    key = two_h.replace(minute=0, second=0, microsecond=0).isoformat() + "Z"
    assert by_hour[key]["latency_samples"] == 2
    assert by_hour[key]["latency_avg_ms"] == 200.0
    turns_key = (now - timedelta(hours=5)).replace(
        minute=0, second=0, microsecond=0
    ).isoformat() + "Z"
    assert by_hour[turns_key]["requests"] >= 2
    assert by_hour[turns_key]["errors"] >= 1
    empty = [h for h in hourly if h["hour"] != key]
    assert all(h["latency_samples"] == 0 for h in empty)
    assert all(h["latency_avg_ms"] is None for h in empty)
    assert all(isinstance(h["requests"], int) for h in hourly)
    assert data["outcome_mix"] == {"answered": 1, "llm_failed": 1}
    assert data["guardrail_flag_breakdown"] == {"low_retrieval_confidence": 2}
    assert "azure_quota_series" in data
    assert len(data["azure_quota_series"]) >= 1
    assert "process_cpu_percent" in data
    assert "online_quality_kpis" in data


async def _overview(client, **params) -> httpx.Response:
    response = await client.get(f"{BASE}/overview", params=params)
    assert response.status_code == 200, response.text
    return response


async def test_overview_shape_and_bounded_query_count(client, db_session):
    await _overview(client)  # warm the audience classification cache
    with QueryCounter(test_engine) as empty:
        await _overview(client)
    session = await chat_session(db_session)
    now = datetime.utcnow()
    for i in range(300):
        _, rows = trace_rows(
            session,
            now - timedelta(minutes=i * 4),
            total_ms=100 + i,
            outcome="answered",
            flags=[],
        )
        db_session.add_all(rows)
    await db_session.flush()
    with QueryCounter(test_engine) as seeded:
        data = (await _overview(client)).json()

    assert seeded.count == empty.count
    assert seeded.count <= OVERVIEW_MAX_QUERIES, seeded.statements
    assert data["health"]["status"] in {HEALTHY, DEGRADED, INCIDENT}
    assert len(data["hours"]) == 24
    for name in (
        "requests",
        "error_rate",
        "latency_p50",
        "latency_p95",
        "active_users",
        "groundedness",
        "blocked_or_flagged",
        "cost_today",
    ):
        kpi = data["kpis"][name]
        assert len(kpi["sparkline"]) == 24, name
        assert kpi["window"] and kpi["source"], name
    # Cost today sums from UTC midnight; it must not claim a rolling 24h.
    assert data["kpis"]["cost_today"]["window"] == "today_utc"
    assert data["kpis"]["requests"]["value"] >= 300


async def test_overview_incident_status_and_recent_incidents(client, db_session):
    await db_session.execute(delete(ChatMessageRecord))
    session = await chat_session(db_session)
    now = datetime.utcnow()
    for _ in range(10):
        _, rows = trace_rows(
            session, now, total_ms=100, outcome="llm_failed", flags=["llm_call_failed"]
        )
        db_session.add_all(rows)
    marker = f"incident-{uuid.uuid4().hex[:8]}"
    db_session.add(
        AuditLog(
            request_id=marker,
            created_at=now + timedelta(seconds=5),
            actor="system",
            action="negative_feedback_spike",
            detail={"negative_rate": 0.9, "total_1h": 10},
        )
    )
    await db_session.flush()
    data = (await _overview(client)).json()
    assert data["health"]["status"] == INCIDENT
    assert data["health"]["signals"]["error_rate"]["status"] == INCIDENT
    assert data["incidents"][0]["request_id"] == marker
    assert data["incidents"][0]["level"] == "ALERT"
    assert all(i["level"] in {"CRITICAL", "ALERT"} for i in data["incidents"])


async def test_overview_etag_round_trip(client, monkeypatch):
    monkeypatch.setattr(
        overview_mod.quality_tracker, "get_kpis", lambda: {"sampled_count": 0}
    )
    first = await _overview(client)
    etag = first.headers["etag"]
    assert first.headers["cache-control"].startswith("private")
    second = await client.get(f"{BASE}/overview", headers={"If-None-Match": etag})
    assert second.status_code == 304


async def test_record_audit_event_notifies_incidents_after_commit(
    db_session, monkeypatch
):
    from app.governance import audit

    published: list[str] = []
    monkeypatch.setattr(
        audit,
        "publish_event",
        lambda topic, item_id: _record(published, topic, item_id),
    )
    await audit.record_audit_event(
        db_session,
        request_id="n-1",
        actor="system",
        action="negative_feedback_spike",
        detail={"negative_rate": 0.9},
    )
    await audit.record_audit_event(
        db_session, request_id="n-2", actor="system", action="ingest", detail={}
    )
    await asyncio.sleep(0.05)
    assert published == [TOPIC_GOVERNANCE_INCIDENT]


async def _record(sink: list[str], topic: str, _item_id) -> None:
    sink.append(topic)


def test_naive_utc_conversion_of_aware_filters():
    from app.api.routes.admin_governance import _naive_utc

    aware = datetime(2026, 1, 1, 10, tzinfo=UTC)
    assert _naive_utc(aware) == datetime(2026, 1, 1, 10)
    assert _naive_utc(None) is None


async def test_planner_estimate_binds_user_values(db_session):
    from app.governance.pagination import planner_estimate

    stmt = select(AuditLog.id).where(
        AuditLog.action.in_(["query", "x'); DROP TABLE audit_log; --"]),
        AuditLog.created_at >= datetime(2000, 1, 1),
    )
    estimate = await planner_estimate(db_session, stmt)
    assert isinstance(estimate, int)
    assert estimate >= 0
    # The session is still usable afterwards.
    assert (await db_session.execute(select(AuditLog.id).limit(1))).all() is not None
