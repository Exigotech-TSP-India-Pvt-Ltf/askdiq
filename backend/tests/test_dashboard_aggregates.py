"""Dashboard aggregations: window, sparklines, deltas,
outcome / latency series, month cost, satisfaction, top intents, activity feed.

Pure logic runs without a database; the endpoint tests seed a random far-past
window (real Postgres, run in CI) and assert exact numbers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.core.config import get_settings
from app.dashboard import activity, service
from app.dashboard.queries import Split
from app.dashboard.window import (
    Window,
    bucket_label,
    delta,
    iso_z,
    month_forecast,
    resolve_window,
)
from app.models.db_models import InsightTaxonomy, LatencyRecord, TurnInsight
from tests.dashboard_helpers import (
    audit,
    exchange,
    feedback,
    iso,
    session_at,
    unique_day,
    unique_ip,
)

NOW = datetime(2026, 10, 16, 13, 0)

# ---------------------------------------------------------------------------
# Window resolution (pure)
# ---------------------------------------------------------------------------


def test_no_bounds_keeps_legacy_mode() -> None:
    assert resolve_window(None, None, now=NOW, max_days=400) is None


def test_missing_bound_defaults_to_seven_days_ending_now() -> None:
    window = resolve_window(NOW - timedelta(days=2), None, now=NOW, max_days=400)
    assert window is not None
    assert window.end == NOW
    only_to = resolve_window(None, NOW, now=NOW, max_days=400)
    assert only_to is not None
    assert only_to.start == NOW - timedelta(days=7)


def test_aware_bounds_are_converted_to_naive_utc() -> None:
    start = datetime(2026, 10, 9, 23, 0, tzinfo=UTC)
    window = resolve_window(start, start + timedelta(days=7), now=NOW, max_days=400)
    assert window is not None
    assert window.start == datetime(2026, 10, 9, 23, 0)
    assert window.start.tzinfo is None


@pytest.mark.parametrize("span", [timedelta(0), timedelta(hours=-1)])
def test_to_not_after_from_is_422(span: timedelta) -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_window(NOW, NOW + span, now=NOW, max_days=400)
    assert exc.value.status_code == 422


def test_span_above_max_range_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_window(NOW - timedelta(days=11), NOW, now=NOW, max_days=10)
    assert exc.value.status_code == 422


def test_bucket_is_hour_up_to_48h_then_day() -> None:
    assert Window(NOW - timedelta(hours=48), NOW).bucket == "hour"
    assert Window(NOW - timedelta(hours=49), NOW).bucket == "day"


def test_buckets_cover_partial_first_and_last_day() -> None:
    window = Window(datetime(2026, 10, 9, 13), datetime(2026, 10, 16, 13))
    labels = [window.label(s) for s in window.starts()]
    assert labels[0] == "2026-10-09"
    assert labels[-1] == "2026-10-16"
    assert len(labels) == 8


def test_hour_labels_are_iso_utc() -> None:
    assert bucket_label(datetime(2026, 10, 16, 13), "hour") == "2026-10-16T13:00:00Z"


def test_window_echo_includes_previous_window() -> None:
    window = Window(datetime(2026, 10, 9, 13), datetime(2026, 10, 16, 13))
    assert window.echo() == {
        "from": "2026-10-09T13:00:00Z",
        "to": "2026-10-16T13:00:00Z",
        "bucket": "day",
        "previous_from": "2026-10-02T13:00:00Z",
        "previous_to": "2026-10-09T13:00:00Z",
    }


# ---------------------------------------------------------------------------
# Arithmetic (pure)
# ---------------------------------------------------------------------------


def test_delta_semantics() -> None:
    assert delta(130, 100) == 0.3
    assert delta(90, 100) == -0.1
    assert delta(5, 0) is None
    assert delta(None, 3) is None
    assert delta(3, None) is None


def test_month_forecast_matches_contract_example() -> None:
    today = date(2026, 10, 16)
    daily = {date(2026, 10, 1): 40.0, date(2026, 10, 16): 56.4}
    forecast = month_forecast(daily, today, 250.0)
    assert forecast.summary() == {
        "currency": "USD",
        "month": "2026-10",
        "mtd_usd": 96.4,
        "forecast_month_end_usd": 186.775,
        "budget_usd": 250.0,
        "days_elapsed": 16,
        "days_in_month": 31,
    }
    points = {p["t"]: p for p in forecast.daily}
    assert len(points) == 31
    assert points["2026-10-01"] == {
        "t": "2026-10-01",
        "actual_usd": 40.0,
        "forecast_usd": None,
    }
    # Today carries both values so the lines join.
    assert points["2026-10-16"]["actual_usd"] == 96.4
    assert points["2026-10-16"]["forecast_usd"] == 96.4
    assert points["2026-10-17"]["actual_usd"] is None
    assert points["2026-10-31"]["forecast_usd"] == 186.775


def test_month_forecast_is_null_on_day_one() -> None:
    forecast = month_forecast({date(2026, 11, 1): 3.0}, date(2026, 11, 1), None)
    assert forecast.mtd_usd == 3.0
    assert forecast.forecast_month_end_usd is None
    assert forecast.budget_usd is None
    assert all(p["forecast_usd"] is None for p in forecast.daily)


def test_month_forecast_ignores_other_months_and_future_days() -> None:
    daily = {date(2026, 9, 30): 99.0, date(2026, 10, 20): 5.0, date(2026, 10, 2): 2.0}
    forecast = month_forecast(daily, date(2026, 10, 4), None)
    assert forecast.mtd_usd == 2.0


def test_error_rate_and_cost_null_semantics() -> None:
    assert service.error_rate({}, {}) is None
    assert service.error_rate({"turns": 3, "failed": 1}, {"errors": 1}) == 50.0
    assert service.cost_value({"cost": None, "cost_rows": 0}) is None
    assert service.cost_value({"cost": 0.0, "cost_rows": 2}) == 0.0


def test_users_sparkline_is_cumulative() -> None:
    starts = [datetime(2026, 1, d) for d in (1, 2, 3)]
    assert service.cumulative(starts, {starts[0]: 2, starts[2]: 1}) == [2, 2, 3]


def test_sparklines_and_period_kpis_from_splits() -> None:
    window = Window(datetime(2026, 1, 1, 12), datetime(2026, 1, 3, 13))
    d1, d2, _d3 = window.starts()
    src = service.Sources(
        messages=Split(
            {d1: {"queries": 3, "visitors": 2}},
            {"queries": 3, "visitors": 2, "active": 1},
            {"queries": 1, "visitors": 1, "active": 0},
        ),
        first_seen={d1: 2},
        sessions=Split({d2: {"sessions": 1}}, {"sessions": 1}, {"sessions": 2}),
        feedback=Split({}, {"feedback": 0, "up": 0, "down": 0}, {}),
        latency=Split({d1: {"avg_ms": 100.0}}, {"avg_ms": 100.0}, {"avg_ms": 50.0}),
        turns=Split({d1: {"turns": 4, "failed": 1}}, {"turns": 4, "failed": 1}, {}),
        server=Split({}, {"errors": 0}, {}),
        exchanges=Split({d1: {"cost": 0.5, "cost_rows": 1}}, {"cost": 0.5}, {}),
    )
    lines = service.sparklines(window, src)
    assert set(lines) == set(service.KPI_KEYS)
    assert lines["users"] == [
        {"t": "2026-01-01", "value": 2},
        {"t": "2026-01-02", "value": 2},
        {"t": "2026-01-03", "value": 2},
    ]
    assert [p["value"] for p in lines["sessions"]] == [0, 1, 0]
    assert lines["avg_latency_ms"][1] == {"t": "2026-01-02", "value": None}
    assert lines["error_rate"][0]["value"] == 25.0
    assert lines["error_rate"][1]["value"] is None
    assert lines["cost_usd"][1]["value"] is None
    current = service.period_kpis(src, current=True)
    previous = service.period_kpis(src, current=False)
    assert current["active_users"] == 1
    assert previous["sessions"] == 2
    assert previous["error_rate"] is None
    assert delta(current["avg_latency_ms"], previous["avg_latency_ms"]) == 1.0


# ---------------------------------------------------------------------------
# Activity feed shaping (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "detail", "expected"),
    [
        ("query", {"outcome": "answered"}, "query"),
        ("query", {"outcome": "blocked"}, "guardrail"),
        ("query", {"guardrail_flags": ["spam_detected"]}, "guardrail"),
        ("query", {"outcome": "fallback"}, "query"),
        ("query", {"guardrail_flags": ["llm_call_failed"]}, "error"),
        ("user_feedback", {"rating": 4}, "feedback"),
        ("server_error", {"status_code": 500}, "error"),
        ("ingest", {}, "document"),
        ("online_eval", {"guardrail_flags": ["pii_leak_detected"]}, "guardrail"),
        ("test_ip_added", {"ip_address": "10.0.0.1"}, "admin"),
    ],
)
def test_activity_classification(action: str, detail: dict, expected: str) -> None:
    assert activity.classify(action, detail) == expected


def test_activity_summaries_carry_no_pii() -> None:
    detail = {
        "query": "my email is alice@example.com",
        "outcome": "answered",
        "ip_address": "203.0.113.5",
    }
    assert activity.summarize("query", detail, 2100.0) == "Query answered in 2.1 s"
    fb = {"rating": 2, "comment": "call me on 0400 000 000"}
    assert activity.summarize("user_feedback", fb, None) == (
        "Feedback received: 2/5 with a comment"
    )
    assert activity.summarize("test_ip_added", {"ip_address": "10.1.1.1"}, None) == (
        "Admin action: test ip added"
    )
    assert activity.summarize("server_error", {"status_code": 503}, None) == (
        "Server error 503"
    )


def test_auth_events_are_not_in_the_feed() -> None:
    assert "login_success" not in activity.FEED_ACTIONS
    assert all(
        not a.startswith(("auth", "login", "logout")) for a in activity.FEED_ACTIONS
    )


# ---------------------------------------------------------------------------
# GET /admin/dashboard with a window (real Postgres)
# ---------------------------------------------------------------------------


async def _seed_window(db) -> dict:
    """Current window D0..D3 (day buckets) and the previous one D-3..D0."""
    d0 = unique_day()
    ip_a, ip_b, ip_c = unique_ip(), unique_ip(), unique_ip()
    code_a, code_b = (
        f"Z{uuid.uuid4().hex[:6].upper()}",
        f"Y{uuid.uuid4().hex[:6].upper()}",
    )
    db.add(InsightTaxonomy(kind="intent", code=code_a, label="Alpha test"))

    a = await session_at(db, d0 + timedelta(hours=10), ip_a)
    _, a0 = await exchange(
        db, a, 0, d0 + timedelta(hours=10), cost=0.01, latency_ms=1000
    )
    _, a1 = await exchange(
        db,
        a,
        1,
        d0 + timedelta(hours=11),
        outcome="fallback",
        cost=0.02,
        latency_ms=3000,
    )
    b = await session_at(db, d0 + timedelta(days=2, hours=9), ip_b)
    _, b0 = await exchange(
        db,
        b,
        0,
        d0 + timedelta(days=2, hours=9),
        outcome="blocked",
        cost=None,
        latency_ms=500,
    )
    await exchange(
        db,
        b,
        1,
        d0 + timedelta(days=2, hours=23, minutes=30),
        outcome="llm_failed",
        cost=0.0,
    )
    await audit(
        db,
        "server_error",
        d0 + timedelta(days=1, hours=12),
        {"status_code": 500},
        ip=ip_b,
    )
    await feedback(db, a0, 5, d0 + timedelta(hours=12))
    await feedback(db, b0, 1, d0 + timedelta(days=2, hours=10))
    await feedback(db, a1, 3, d0 + timedelta(days=1))
    for msg, code in ((a0, code_a), (a1, code_a), (b0, code_b)):
        db.add(
            TurnInsight(
                message_id=msg.id,
                session_id=msg.session_id,
                intent=code,
                bot_quality="OK",
                substantive=True,
                handled=True,
                classified_by="rule",
            )
        )
    # Previous window: one visitor, one answered turn.
    c = await session_at(db, d0 - timedelta(days=2) + timedelta(hours=10), ip_c)
    await exchange(
        db,
        c,
        0,
        d0 - timedelta(days=2) + timedelta(hours=10),
        cost=0.05,
        latency_ms=2000,
    )
    await db.flush()
    return {"d0": d0, "code_a": code_a, "code_b": code_b}


async def test_windowed_dashboard_exact_numbers(client, db_session) -> None:
    seed = await _seed_window(db_session)
    d0 = seed["d0"]
    resp = await client.get(
        "/admin/dashboard",
        params={"from": iso(d0), "to": iso(d0 + timedelta(days=3))},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    days = [(d0 + timedelta(days=i)).date().isoformat() for i in range(3)]

    assert data["window"] == {
        "from": iso_z(d0),
        "to": iso_z(d0 + timedelta(days=3)),
        "bucket": "day",
        "previous_from": iso_z(d0 - timedelta(days=3)),
        "previous_to": iso_z(d0),
    }
    assert data["users"] == 2
    assert data["active_users"] == 1
    assert data["sessions"] == data["conversations"] == 2
    assert data["queries"] == 4
    assert data["feedback"] == 3
    assert data["avg_latency_ms"] == pytest.approx(1500.0)
    assert data["error_rate"] == pytest.approx(40.0)
    assert data["cost_usd"] == pytest.approx(0.03)

    spark = data["sparklines"]
    assert [p["t"] for p in spark["queries"]] == days
    assert [p["value"] for p in spark["queries"]] == [2, 0, 2]
    assert [p["value"] for p in spark["users"]] == [1, 1, 2]
    assert [p["value"] for p in spark["active_users"]] == [1, 0, 1]
    assert [p["value"] for p in spark["sessions"]] == [1, 0, 1]
    assert [p["value"] for p in spark["feedback"]] == [1, 1, 1]
    assert [p["value"] for p in spark["avg_latency_ms"]] == [2000.0, None, 500.0]
    assert [p["value"] for p in spark["error_rate"]] == [0.0, 100.0, 50.0]
    assert [p["value"] for p in spark["cost_usd"]] == [
        pytest.approx(0.03),
        None,
        0.0,
    ]

    assert data["deltas"] == {
        "users": 1.0,
        "active_users": None,
        "sessions": 1.0,
        "queries": 3.0,
        "feedback": None,
        "avg_latency_ms": -0.25,
        "error_rate": None,
        "cost_usd": pytest.approx(-0.4),
    }
    assert data["queries_by_outcome"] == [
        {
            "t": days[0],
            "answered": 1,
            "ungrounded": 1,
            "blocked": 0,
            "conversational": 0,
            "llm_failed": 0,
            "clarify": 0,
        },
        {
            "t": days[1],
            "answered": 0,
            "ungrounded": 0,
            "blocked": 0,
            "conversational": 0,
            "llm_failed": 0,
            "clarify": 0,
        },
        {
            "t": days[2],
            "answered": 0,
            "ungrounded": 0,
            "blocked": 1,
            "conversational": 0,
            "llm_failed": 1,
            "clarify": 0,
        },
    ]
    lat = data["latency_series"]
    assert lat[0] == {
        "t": days[0],
        "p50_ms": 2000.0,
        "p95_ms": pytest.approx(2900.0),
        "count": 2,
    }
    assert lat[1] == {"t": days[1], "p50_ms": None, "p95_ms": None, "count": 0}
    assert lat[2]["p50_ms"] == 500.0
    assert data["satisfaction"] == {"thumbs_up": 1, "thumbs_down": 1, "ratio": 0.5}
    assert data["top_intents"] == [
        {
            "intent": seed["code_a"],
            "label": "Alpha test",
            "count": 2,
            "share": pytest.approx(0.666667),
        },
        {
            "intent": seed["code_b"],
            "label": seed["code_b"],
            "count": 1,
            "share": pytest.approx(0.333333),
        },
    ]
    # Legacy panels are still present.
    assert {"pipeline", "governance", "query_trend", "latency_breakdown"} <= set(data)


async def test_month_cost_block_shape_and_budget(
    client, db_session, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "monthly_cost_budget_usd", 250.0)
    d0 = unique_day()
    resp = await client.get(
        "/admin/dashboard",
        params={"from": iso(d0), "to": iso(d0 + timedelta(hours=6))},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["window"]["bucket"] == "hour"
    assert len(data["sparklines"]["queries"]) == 6
    assert data["sparklines"]["queries"][0]["t"] == iso_z(d0)
    cost = data["cost"]
    today = datetime.now(UTC).date()
    assert cost["currency"] == "USD"
    assert cost["month"] == f"{today.year:04d}-{today.month:02d}"
    assert cost["budget_usd"] == 250.0
    assert cost["days_elapsed"] == today.day
    assert cost["mtd_usd"] >= 0
    assert data["satisfaction"] == {"thumbs_up": 0, "thumbs_down": 0, "ratio": None}
    assert data["top_intents"] == []


async def test_audience_scopes_windowed_kpis(client, db_session) -> None:
    seed = await _seed_window(db_session)
    params = {"from": iso(seed["d0"]), "to": iso(seed["d0"] + timedelta(days=3))}
    users = (
        await client.get("/admin/dashboard", params={**params, "audience": "users"})
    ).json()
    testing = (
        await client.get("/admin/dashboard", params={**params, "audience": "testing"})
    ).json()
    assert users["queries"] == 4
    assert users["sessions"] == 2
    assert testing["queries"] == 0
    assert testing["sessions"] == 0
    assert testing["top_intents"] == []


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-10-10T00:00:00Z", "to": "2026-10-10T00:00:00Z"},
        {"from": "2026-10-11T00:00:00Z", "to": "2026-10-10T00:00:00Z"},
        {"from": "2020-01-01T00:00:00Z", "to": "2026-10-10T00:00:00Z"},
        {"from": "not-a-date"},
        {"audience": "everyone"},
    ],
)
async def test_invalid_window_or_audience_is_422(client, params) -> None:
    resp = await client.get("/admin/dashboard", params=params)
    assert resp.status_code == 422


async def test_dashboard_without_window_keeps_legacy_shape(client) -> None:
    data = (await client.get("/admin/dashboard")).json()
    assert "window" not in data
    assert "sparklines" not in data
    assert {"users", "queries", "error_rate", "query_trend"} <= set(data)


async def test_windowed_dashboard_sends_etag_and_304(client) -> None:
    d0 = unique_day()
    params = {"from": iso(d0), "to": iso(d0 + timedelta(days=1))}
    first = await client.get("/admin/dashboard", params=params)
    assert first.status_code == 200
    second = await client.get(
        "/admin/dashboard",
        params=params,
        headers={"If-None-Match": first.headers["etag"]},
    )
    assert second.status_code == 304


# ---------------------------------------------------------------------------
# GET /admin/dashboard/activity (real Postgres)
# ---------------------------------------------------------------------------


async def test_activity_feed_newest_first_and_pii_free(client, db_session) -> None:
    base = datetime(2099, 1, 1) + timedelta(minutes=uuid.uuid4().int % 100_000)
    session_id, message_id = uuid.uuid4(), uuid.uuid4()
    query = await audit(
        db_session,
        "query",
        base + timedelta(seconds=1),
        {
            "query": "alice@example.com asks about leave",
            "outcome": "answered",
            "session_id": str(session_id),
        },
        ip="203.0.113.77",
    )
    db_session.add(
        LatencyRecord(
            id=uuid.uuid4(),
            request_id=query.request_id,
            created_at=base,
            total_ms=2100.0,
        )
    )
    await audit(
        db_session,
        "user_feedback",
        base + timedelta(seconds=2),
        {"rating": 2, "comment": "bob@example.com", "message_id": str(message_id)},
        ip="203.0.113.77",
    )
    await audit(
        db_session, "server_error", base + timedelta(seconds=3), {"status_code": 500}
    )
    await audit(
        db_session, "login_success", base + timedelta(seconds=4), {"email": "x@y.z"}
    )
    await audit(
        db_session,
        "test_ip_added",
        base + timedelta(seconds=5),
        {"ip_address": "10.9.9.9"},
    )
    await db_session.flush()

    resp = await client.get("/admin/dashboard/activity", params={"limit": 4})
    assert resp.status_code == 200, resp.text
    feed = resp.json()
    assert feed["generated_at"].endswith("Z")
    items = feed["items"]
    assert [i["type"] for i in items] == ["admin", "error", "feedback", "query"]
    assert items[3]["summary"] == "Query answered in 2.1 s"
    assert items[3]["request_id"] == query.request_id
    assert items[3]["conversation_id"] == str(session_id)
    assert items[2]["message_id"] == str(message_id)
    assert items[2]["request_id"] is None
    text = repr(items)
    for secret in ("alice@", "bob@", "203.0.113.77", "10.9.9.9", "x@y.z"):
        assert secret not in text


@pytest.mark.parametrize("limit", [0, 51, "x"])
async def test_activity_limit_is_validated(client, limit) -> None:
    resp = await client.get("/admin/dashboard/activity", params={"limit": limit})
    assert resp.status_code == 422
