"""Global date range on the admin console: every time-based page's endpoints
take an optional, validated range and apply it; without one they keep their
previous behaviour.

- Usage & Cost: ``/admin/usage/users`` and ``/sessions`` gained
  ``from_date`` / ``to_date`` (same parser and cap as ``/queries``).
- Feedback: ``/admin/feedback/analytics``, ``/golden`` and ``/export`` take
  ``from`` / ``to`` days.
- Evaluation: ``/metrics/evaluation/live`` takes ``from`` / ``to`` days.
- Visitors > Conversations: ``/admin/conversations`` takes ``from`` / ``to``
  days (conversations started then, as ``/conversations/stats``).

Fixtures sit on a fixed day long before any seeded data (2020-01-15) so the
range assertions are exact.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.admin_usage import usage_date_range
from app.core.config import get_settings
from app.dashboard.eval_series import Run, runs_in_window
from app.dashboard.range_params import optional_day_range
from tests.feedback_helpers import make_exchange, make_feedback, make_session, marker

DAY = "2020-01-15"
NEXT_DAY = "2020-01-16"
AT = datetime(2020, 1, 15, 10, 30)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_optional_day_range_is_none_without_bounds() -> None:
    assert optional_day_range(None, None) is None


def test_optional_day_range_resolves_inclusive_days() -> None:
    days = optional_day_range(date(2020, 1, 15), date(2020, 1, 16))
    assert days is not None
    assert days.start == datetime(2020, 1, 15)
    assert days.end == datetime(2020, 1, 17)


def test_optional_day_range_rejects_reversed_and_too_long() -> None:
    with pytest.raises(HTTPException) as reversed_:
        optional_day_range(date(2020, 1, 16), date(2020, 1, 15))
    assert reversed_.value.status_code == 422
    too_long = get_settings().usage_max_range_days + 1
    with pytest.raises(HTTPException) as long_:
        optional_day_range(
            date(2020, 1, 1), date(2020, 1, 1) + timedelta(days=too_long)
        )
    assert long_.value.status_code == 422


def test_usage_date_range_caps_the_span() -> None:
    max_days = get_settings().usage_max_range_days
    lo = date(2020, 1, 1)
    usage_date_range(lo.isoformat(), (lo + timedelta(days=max_days - 1)).isoformat())
    with pytest.raises(HTTPException) as exc:
        usage_date_range(
            lo.isoformat(), (lo + timedelta(days=max_days + 1)).isoformat()
        )
    assert exc.value.status_code == 422


def test_runs_in_window_keeps_runs_started_inside() -> None:
    runs = [
        Run(
            run_id=f"r{i}",
            started_at=datetime(2020, 1, 14 + i, 12),
            n_cases=1,
            pass_rate=1.0,
        )
        for i in range(3)
    ]
    window = (datetime(2020, 1, 15), datetime(2020, 1, 16))
    assert [r.run_id for r in runs_in_window(runs, window)] == ["r1"]
    assert runs_in_window(runs, None) == runs


# ---------------------------------------------------------------------------
# Usage & Cost lists
# ---------------------------------------------------------------------------


async def _past_turn(db: AsyncSession, ip: str) -> None:
    tag = marker()
    session = await make_session(db, f"{tag}@example.com", ip=ip)
    session.created_at = AT
    user_msg, _ = await make_exchange(db, session, turn=1, question=f"{tag} q")
    user_msg.created_at = AT
    await db.commit()


@pytest.mark.asyncio
async def test_usage_users_apply_the_day_range(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    ip = "198.51.100.61"
    await _past_turn(db_session, ip)

    inside = await client.get(
        f"/admin/usage/users?q={ip}&audience=overview&from_date={DAY}&to_date={DAY}"
    )
    assert inside.status_code == 200
    assert [r["ip_address"] for r in inside.json()["items"]] == [ip]

    outside = await client.get(
        f"/admin/usage/users?q={ip}&audience=overview&from_date={NEXT_DAY}&to_date={NEXT_DAY}"
    )
    assert outside.json()["items"] == []

    unbounded = await client.get(f"/admin/usage/users?q={ip}&audience=overview")
    assert len(unbounded.json()["items"]) == 1

    bad = await client.get("/admin/usage/users?from_date=not-a-day")
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_usage_sessions_apply_the_day_range(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    ip = "198.51.100.62"
    await _past_turn(db_session, ip)

    inside = await client.get(
        f"/admin/usage/sessions?ip={ip}&audience=overview&from_date={DAY}&to_date={DAY}"
    )
    assert inside.status_code == 200
    assert inside.json()["total"] == 1

    outside = await client.get(
        f"/admin/usage/sessions?ip={ip}&audience=overview&from_date={NEXT_DAY}"
    )
    assert outside.json()["total"] == 0

    inverted = await client.get(
        f"/admin/usage/sessions?from_date={NEXT_DAY}&to_date={DAY}"
    )
    assert inverted.status_code == 422


@pytest.mark.asyncio
async def test_usage_summary_date_only_to_covers_the_whole_day(
    client: AsyncClient,
) -> None:
    res = await client.get(f"/admin/usage/summary?from_date={DAY}&to_date={DAY}")
    assert res.status_code == 200
    too_long = await client.get(
        "/admin/usage/summary?from_date=2000-01-01&to_date=2026-01-01"
    )
    assert too_long.status_code == 422


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_analytics_count_only_ratings_in_range(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tag = marker()
    session = await make_session(db_session, f"{tag}@example.com", ip="198.51.100.63")
    _, asst = await make_exchange(db_session, session, turn=1, question=f"{tag} q")
    await make_feedback(
        db_session, asst, actor=f"{tag}@example.com", rating=1, created_at=AT
    )

    inside = await client.get(f"/admin/feedback/analytics?from={DAY}&to={DAY}")
    assert inside.status_code == 200
    body = inside.json()
    assert body["total"] == 1
    assert body["negative_count"] == 1
    assert [p["day"] for p in body["daily_trend"]] == [DAY]

    outside = await client.get(
        f"/admin/feedback/analytics?from={NEXT_DAY}&to={NEXT_DAY}"
    )
    assert outside.json()["total"] == 0

    unbounded = await client.get("/admin/feedback/analytics")
    assert unbounded.json()["total"] >= 1

    bad = await client.get(f"/admin/feedback/analytics?from={NEXT_DAY}&to={DAY}")
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_feedback_golden_and_export_take_the_range(client: AsyncClient) -> None:
    golden = await client.get(f"/admin/feedback/golden?from={DAY}&to={DAY}")
    assert golden.status_code == 200
    assert golden.json()["total"] == 0

    export = await client.get(
        f"/admin/feedback/export?format=jsonl&from={DAY}&to={DAY}"
    )
    assert export.status_code == 200
    assert export.text.strip() == ""

    bad = await client.get("/admin/feedback/golden?from=not-a-day")
    assert bad.status_code == 422


# ---------------------------------------------------------------------------
# Evaluation and conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_evaluation_filters_records_and_echoes_the_window(
    client: AsyncClient,
) -> None:
    res = await client.get(
        f"/metrics/evaluation/live?audience=users&from={DAY}&to={DAY}"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["window"] == {"from": DAY, "to": DAY}
    assert body["n_cases"] == 0

    legacy = await client.get("/metrics/evaluation/live?audience=users")
    assert legacy.status_code == 200
    assert "window" not in legacy.json()

    bad = await client.get(f"/metrics/evaluation/live?from={NEXT_DAY}&to={DAY}")
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_conversation_list_keeps_conversations_started_in_range(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    ip = "198.51.100.64"
    await _past_turn(db_session, ip)

    inside = await client.get(f"/admin/conversations?ip={ip}&from={DAY}&to={DAY}")
    assert inside.status_code == 200
    assert inside.json()["total"] == 1

    outside = await client.get(
        f"/admin/conversations?ip={ip}&from={NEXT_DAY}&to={NEXT_DAY}"
    )
    assert outside.json()["total"] == 0

    unbounded = await client.get(f"/admin/conversations?ip={ip}")
    assert unbounded.json()["total"] == 1
