"""Active visitors: one definition behind every "active users" number.

Pure shaping runs without a database; the counting tests seed rows inside the
rolled-back ``db_session`` around a random far-past ``now`` (real Postgres,
run in CI) and call the shared helpers directly with that ``now``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.audit import ANONYMOUS_ACTOR
from app.dashboard import active_visitors
from app.dashboard.active_visitors import (
    ACTIVE_NOW_SPAN,
    DEFINITION,
    active_counts,
    count_active,
    hourly_active,
    summary,
    visitor_audience,
)
from app.governance.audience import invalidate_audience_cache
from app.governance.auth import DEFAULT_ACTOR
from app.models.db_models import ChatMessageRecord
from app.schemas.dashboard import ActiveVisitors
from tests.dashboard_helpers import exchange, session_at, unique_day, unique_ip

DAY = timedelta(days=1)
SECOND = timedelta(seconds=1)
TEST_IP = "127.0.0.1"  # built-in loopback: always a test IP


COUNT_KEYS = (
    "active_now",
    "dau",
    "dau_previous",
    "wau",
    "wau_previous",
    "mau",
    "mau_previous",
)


def _counts(**overrides: int) -> dict[str, int]:
    return {**dict.fromkeys(COUNT_KEYS, 0), **overrides}


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


def test_test_ips_are_excluded_on_every_audience_but_testing() -> None:
    assert visitor_audience("overview") == "users"
    assert visitor_audience("users") == "users"
    assert visitor_audience("testing") == "testing"


def test_summary_shape_trend_and_definition() -> None:
    out = summary(_counts(active_now=2, dau=6, dau_previous=4, wau=9, mau=12))
    assert out["definition"] == DEFINITION
    assert out["active_now"] == 2
    assert out["active_now_minutes"] == 5
    assert out["dau"] == {"value": 6, "previous": 4, "delta": 0.5, "days": 1}
    assert out["wau"] == {"value": 9, "previous": 0, "delta": None, "days": 7}
    assert out["mau"]["days"] == 30
    ActiveVisitors.model_validate(out)


def test_summary_carries_counts_only() -> None:
    out = summary(_counts(dau=1))
    assert set(out) == {
        "definition",
        "active_now",
        "active_now_minutes",
        "dau",
        "wau",
        "mau",
    }
    for key in ("dau", "wau", "mau"):
        assert set(out[key]) == {"value", "previous", "delta", "days"}


def test_definition_text_names_every_rule() -> None:
    text = DEFINITION.lower()
    for phrase in (
        "distinct",
        "at least one query",
        "client id",
        "ip address",
        "test ips",
    ):
        assert phrase in text


def test_negative_trend() -> None:
    assert summary(_counts(mau=3, mau_previous=4))["mau"]["delta"] == -0.25


# ---------------------------------------------------------------------------
# Counting (real Postgres)
# ---------------------------------------------------------------------------


@pytest.fixture
async def fresh_classification() -> AsyncIterator[None]:
    """The test-IP classification only knows IPs already in the database."""
    invalidate_audience_cache()
    yield
    invalidate_audience_cache()


async def _query(
    db: AsyncSession, at: datetime, ip: str | None, actor: str = DEFAULT_ACTOR
) -> None:
    """One user query (plus its reply) at ``at`` from ``ip`` as ``actor``."""
    session = await session_at(db, at, ip, email=actor)
    await exchange(db, session, 0, at)


def _now() -> datetime:
    return unique_day() + timedelta(days=200, hours=12)


async def test_identity_prefers_client_id_over_ip(db_session) -> None:
    now = _now()
    at = now - timedelta(hours=1)
    # One client id from two IPs: one visitor.
    await _query(db_session, at, unique_ip(), actor="Alice@Example.com")
    await _query(db_session, at, unique_ip(), actor="alice@example.com ")
    assert await count_active(db_session, "overview", now - DAY, now) == 1


async def test_default_or_anonymous_actor_falls_back_to_ip(db_session) -> None:
    now = _now()
    at = now - timedelta(hours=1)
    shared, other = unique_ip(), unique_ip()
    await _query(db_session, at, shared, actor=DEFAULT_ACTOR)
    await _query(db_session, at, shared, actor=ANONYMOUS_ACTOR)
    await _query(db_session, at, shared, actor="")
    await _query(db_session, at, other, actor=DEFAULT_ACTOR)
    assert await count_active(db_session, "overview", now - DAY, now) == 2


async def test_message_ip_wins_over_session_ip(db_session) -> None:
    now = _now()
    session = await session_at(db_session, now - timedelta(hours=2), unique_ip())
    user, _ = await exchange(db_session, session, 0, now - timedelta(hours=2))
    second, _ = await exchange(db_session, session, 1, now - timedelta(hours=1))
    second.ip_address = unique_ip()  # roamed to a new IP mid-session
    await db_session.flush()
    assert user.ip_address == session.created_ip
    assert await count_active(db_session, "overview", now - DAY, now) == 2


async def test_test_ips_are_excluded(db_session, fresh_classification) -> None:
    now = _now()
    at = now - timedelta(hours=1)
    await _query(db_session, at, unique_ip())
    await _query(db_session, at, TEST_IP, actor="tester@example.com")
    await _query(db_session, at, None, actor="no-ip@example.com")
    invalidate_audience_cache()
    for audience in ("overview", "users"):
        assert await count_active(db_session, audience, now - DAY, now) == 1
    # The testing audience counts test (and unknown-IP) traffic instead.
    assert await count_active(db_session, "testing", now - DAY, now) == 2


async def test_only_user_queries_count(db_session) -> None:
    now = _now()
    session = await session_at(db_session, now - timedelta(hours=1), unique_ip())
    db_session.add(
        ChatMessageRecord(
            session_id=session.id,
            turn_index=0,
            role="assistant",
            content="Hello!",
            created_at=now - timedelta(hours=1),
            ip_address=session.created_ip,
        )
    )
    await db_session.flush()
    assert await count_active(db_session, "overview", now - DAY, now) == 0


async def test_window_boundaries_and_periods(db_session) -> None:
    now = _now()
    seeds = {
        "now_edge": now - ACTIVE_NOW_SPAN,  # active now (inclusive start)
        "just_before_now": now - ACTIVE_NOW_SPAN - SECOND,  # DAU only
        "day_edge": now - DAY,  # DAU (inclusive start)
        "prev_day": now - DAY - SECOND,  # previous DAU, WAU
        "prev_day_edge": now - 2 * DAY,  # previous DAU (inclusive start)
        "week": now - timedelta(days=6),  # WAU
        "prev_week": now - timedelta(days=8),  # previous WAU, MAU
        "month": now - timedelta(days=29),  # MAU
        "prev_month": now - timedelta(days=45),  # previous MAU
        "too_old": now - timedelta(days=60) - SECOND,  # outside every window
        "future": now,  # end is exclusive
    }
    for at in seeds.values():
        await _query(db_session, at, unique_ip())

    counts = await active_counts(db_session, "overview", now)
    assert set(counts) == set(COUNT_KEYS)
    assert counts == {
        "active_now": 1,
        "dau": 3,
        "dau_previous": 2,
        "wau": 6,
        "wau_previous": 1,
        "mau": 8,
        "mau_previous": 1,
    }
    assert summary(counts)["dau"]["delta"] == 0.5


async def test_returning_visitor_counts_once_per_window(db_session) -> None:
    now = _now()
    ip = unique_ip()
    for hours in (1, 3, 30, 100):
        await _query(db_session, now - timedelta(hours=hours), ip)
    counts = await active_counts(db_session, "overview", now)
    assert counts["dau"] == 1
    assert counts["dau_previous"] == 1
    assert counts["wau"] == 1
    assert counts["mau"] == 1


async def test_hourly_active_and_total(db_session) -> None:
    now = _now()
    start = now - timedelta(hours=24)
    ip = unique_ip()
    hour_a = now.replace(minute=0) - timedelta(hours=3)
    await _query(db_session, hour_a + timedelta(minutes=5), ip)
    await _query(db_session, hour_a + timedelta(minutes=40), ip)
    await _query(db_session, hour_a + timedelta(minutes=50), unique_ip())
    await _query(db_session, hour_a + timedelta(hours=1), ip)
    rollup = await hourly_active(db_session, "overview", start, now)
    key = active_visitors.VISITORS
    assert rollup.hours[hour_a] == {key: 2}
    assert rollup.hours[hour_a + timedelta(hours=1)] == {key: 1}
    assert rollup.total == {key: 2}


async def test_dashboard_and_visitors_report_active_visitors(
    client, db_session
) -> None:
    now = datetime.utcnow()
    await _query(db_session, now - timedelta(minutes=1), unique_ip())
    legacy = (await client.get("/admin/dashboard")).json()
    block = ActiveVisitors.model_validate(legacy["active_visitors"])
    assert block.active_now >= 1
    assert legacy["active_users"] == block.dau.value
    day = unique_day().date().isoformat()
    windowed = (
        await client.get(
            "/admin/dashboard",
            params={"from": f"{day}T00:00:00Z", "to": f"{day}T12:00:00Z"},
        )
    ).json()
    assert windowed["active_visitors"]["definition"] == DEFINITION
    stats = (await client.get("/admin/visitors/stats")).json()
    assert stats["kpis"]["active_now"] == stats["active_visitors"]["active_now"]
    raw = str(legacy["active_visitors"]) + str(stats["active_visitors"])
    assert "198.51." not in raw


async def test_governance_cards_use_the_shared_definition(client, db_session) -> None:
    overview = (await client.get("/admin/governance/overview")).json()
    assert overview["kpis"]["active_users"]["source"] == active_visitors.SOURCE
    monitoring = (await client.get("/admin/governance/monitoring")).json()
    assert monitoring["counts"]["active_users"]["source"] == active_visitors.SOURCE
