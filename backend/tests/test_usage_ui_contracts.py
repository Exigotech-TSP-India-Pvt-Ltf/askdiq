"""Regression tests for the Usage console contracts the UI relies on.

Each case is a parameter the frontend sends that the API used to reject or
ignore: day links (``from_date`` / ``to_date``), column sorts, header stats,
the IP filter on session-only IPs and the Users / Sessions CSV exports.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.admin_usage import parse_usage_bound, usage_date_range
from tests.feedback_helpers import make_exchange, make_session, marker

# ---------------------------------------------------------------------------
# Pure helpers (no database)
# ---------------------------------------------------------------------------


def test_date_only_bounds_cover_the_whole_day() -> None:
    lo, hi = usage_date_range("2026-10-14", "2026-10-14")
    assert lo == datetime(2026, 10, 14)
    assert hi == datetime(2026, 10, 15) - timedelta(microseconds=1)


def test_iso_bounds_are_converted_to_naive_utc() -> None:
    assert parse_usage_bound("2026-10-14T10:00:00+10:00", "from_date") == datetime(
        2026, 10, 14, 0, 0
    )
    assert parse_usage_bound("2026-10-14T00:00:00.000Z", "from_date") == datetime(
        2026, 10, 14
    )
    assert parse_usage_bound(None, "from_date") is None


@pytest.mark.parametrize(
    ("lo", "hi"),
    [("yesterday", None), (None, "2026-13-01"), ("2026-10-15", "2026-10-14")],
)
def test_invalid_or_inverted_bounds_are_422(lo: str | None, hi: str | None) -> None:
    with pytest.raises(HTTPException) as exc:
        usage_date_range(lo, hi)
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("sort", ["tokens_desc", "tokens_asc"])
async def test_sessions_token_sort_is_accepted(client: AsyncClient, sort: str) -> None:
    res = await client.get(f"/admin/usage/sessions?sort={sort}&limit=10")
    assert res.status_code == 200
    stats = res.json()["stats"]
    assert {"top_sessions", "avg_cost_per_session", "max_depth"} <= stats.keys()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sort", ["latency_desc", "latency_asc", "cost_asc", "tokens_asc", "created_at_asc"]
)
async def test_queries_sorts_are_accepted(client: AsyncClient, sort: str) -> None:
    res = await client.get(f"/admin/usage/queries?sort={sort}&limit=10")
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_queries_day_filter_stats_and_ascending_cost(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tag = marker()
    session = await make_session(db_session, f"{tag}@example.com", ip="198.51.100.41")
    await make_exchange(
        db_session,
        session,
        turn=1,
        question=f"{tag} cheap",
        cost_usd=0.001,
        latency_ms=900,
    )
    await make_exchange(
        db_session,
        session,
        turn=2,
        question=f"{tag} pricey",
        cost_usd=0.009,
        outcome="ungrounded",
        grounded=False,
        latency_ms=2100,
    )
    today = datetime.utcnow().date().isoformat()

    res = await client.get(
        f"/admin/usage/queries?q={tag}&from_date={today}&to_date={today}&sort=cost_asc"
    )
    assert res.status_code == 200
    body = res.json()
    assert [r["cost_usd"] for r in body["items"]] == [0.001, 0.009]
    stats = body["stats"]
    assert stats["total_queries"] == 2
    assert stats["outcomes"] == {"answered": 1, "ungrounded": 1}
    assert stats["groundedness_evaluated_turns"] == 2
    assert stats["groundedness_passed_turns"] == 1
    assert stats["groundedness_pass_rate"] == 50.0
    assert stats["avg_latency_ms"] == 1500.0

    latency = await client.get(f"/admin/usage/queries?q={tag}&sort=latency_desc")
    assert [r["latency_ms"] for r in latency.json()["items"]] == [2100.0, 900.0]

    yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    none = await client.get(
        f"/admin/usage/queries?q={tag}&from_date={yesterday}&to_date={yesterday}"
    )
    assert none.json()["total"] == 0

    bad = await client.get("/admin/usage/queries?from_date=not-a-date")
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_ip_filter_matches_session_ip_when_message_ip_is_missing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tag = marker()
    session = await make_session(db_session, f"{tag}@example.com", ip="198.51.100.42")
    user_msg, _ = await make_exchange(db_session, session, turn=1, question=f"{tag} q")
    user_msg.ip_address = None
    await db_session.commit()

    res = await client.get(f"/admin/usage/queries?q={tag}&ip=198.51.100.42")
    assert res.status_code == 200
    assert res.json()["total"] == 1


@pytest.mark.asyncio
async def test_users_sessions_sort(client: AsyncClient) -> None:
    res = await client.get("/admin/usage/users?sort=sessions_desc&limit=50")
    assert res.status_code == 200
    sessions = [r["sessions"] for r in res.json()["items"]]
    assert sessions == sorted(sessions, reverse=True)
    assert {"queries_driven", "top_users"} <= res.json()["stats"].keys()


@pytest.mark.asyncio
@pytest.mark.parametrize("tab", ["users", "sessions"])
async def test_users_and_sessions_exports_stream_rows(
    client: AsyncClient, db_session: AsyncSession, tab: str
) -> None:
    tag = marker()
    session = await make_session(db_session, f"{tag}@example.com", ip="198.51.100.43")
    await make_exchange(db_session, session, turn=1, question=f"{tag} q")

    res = await client.get(f"/admin/usage/export?tab={tab}&audience=overview")
    assert res.status_code == 200
    lines = [line for line in res.text.lstrip("﻿").splitlines() if line]
    assert len(lines) >= 2  # header plus at least the seeded row
    assert "198.51.100.43" in res.text


@pytest.mark.asyncio
async def test_users_carry_last_active_and_sort_by_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tag = marker()
    session = await make_session(db_session, f"{tag}@example.com", ip="198.51.100.44")
    user_msg, _ = await make_exchange(db_session, session, turn=1, question=f"{tag} q")
    user_msg.created_at = datetime(2026, 10, 14, 9, 30)
    await db_session.commit()

    mine = await client.get("/admin/usage/users?q=198.51.100.44&audience=overview")
    assert mine.status_code == 200
    assert mine.json()["items"][0]["last_active"] == "2026-10-14T09:30:00Z"

    for order in ("desc", "asc"):
        res = await client.get(
            f"/admin/usage/users?sort=last_active_{order}&limit=50&audience=overview"
        )
        assert res.status_code == 200
        stamps = [r["last_active"] for r in res.json()["items"] if r["last_active"]]
        assert stamps == sorted(stamps, reverse=order == "desc")


@pytest.fixture
async def raw_client():
    """No DB override: for validation errors raised before any query."""
    from httpx import ASGITransport

    from app.core.config import get_settings
    from main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-API-Key": get_settings().api_key_name or "ci-test-key"},
    ) as ac:
        yield ac


def test_query_inspector_route_is_declared_once() -> None:
    from main import app
    from tests.route_helpers import flat_routes, is_api_route

    matches = [
        r
        for r in flat_routes(app)
        if is_api_route(r)
        and r.path == "/admin/usage/queries/{request_id}"
        and "GET" in r.methods
    ]
    assert len(matches) == 1
    assert matches[0].endpoint.__module__ == "app.api.routes.admin_usage"


@pytest.mark.asyncio
async def test_sessions_page_limit_is_still_capped(raw_client: AsyncClient) -> None:
    res = await raw_client.get("/admin/usage/sessions?limit=10000")
    assert res.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/admin/users?limit=100000",
        "/admin/users?limit=0",
        "/admin/users?offset=-1",
        "/admin/conversations?limit=100000",
        "/admin/conversations?offset=99999999",
        "/admin/usage/users?limit=100000",
    ],
)
async def test_admin_list_limits_are_bounded(raw_client: AsyncClient, url: str) -> None:
    res = await raw_client.get(url)
    assert res.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["/admin/users?status=bogus", "/admin/users?weekday=1", "/admin/users?hour=3"],
)
async def test_visitor_list_bad_filters_are_422_not_500(
    raw_client: AsyncClient, url: str
) -> None:
    res = await raw_client.get(url)
    assert res.status_code == 422


def test_visitor_status_follows_last_activity() -> None:
    from app.api.routes.admin import VISITOR_ACTIVE_WINDOW, visitor_status_label

    now = datetime(2026, 10, 16, 12, 0)
    assert visitor_status_label(now - timedelta(minutes=5), now) == "Active"
    assert (
        visitor_status_label(now - VISITOR_ACTIVE_WINDOW - timedelta(seconds=1), now)
        == "Inactive"
    )
    assert visitor_status_label(None, now) == "Inactive"


@pytest.mark.asyncio
async def test_visitor_status_filter_and_message_counts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tag = marker()
    session = await make_session(db_session, f"{tag}@example.com", ip="198.51.100.77")
    await make_exchange(db_session, session, turn=1, question=f"{tag} q")

    active = await client.get("/admin/users?status=active&q=198.51.100.77")
    inactive = await client.get("/admin/users?status=inactive&q=198.51.100.77")
    assert active.status_code == inactive.status_code == 200
    # The session was just used, so the visitor is active, not inactive.
    assert [r["status"] for r in active.json()["items"]] == ["Active"]
    assert inactive.json()["items"] == []

    detail = await client.get("/admin/users/client-198.51.100.77")
    assert detail.status_code == 200
    counts = [c["message_count"] for c in detail.json()["conversations_data"]]
    assert counts == [2]
