"""AI Usage card (/admin/usage/llm): one window, one source, real failures only.

Regression: ``failed`` counted every ungrounded / blocked / conversational turn
(``grounded is False``, all time) against ``requests`` from latency records
(30 days), so the card showed ~42 % "failed" turns that were not errors.
"""

from __future__ import annotations

from datetime import datetime

from app.api.routes import admin as admin_routes
from app.api.routes import admin_usage
from tests.dashboard_helpers import exchange, session_at, unique_ip

_SEEDED = {
    "answered": 3,
    "ungrounded": 2,
    "blocked": 2,
    "conversational": 1,
    "llm_failed": 1,
    # Legacy spellings normalise like the Monitoring outcome mix.
    "fallback": 1,  # -> ungrounded
    "empty_llm_output": 1,  # -> llm_failed
}


async def _seed(db_session) -> None:
    at = datetime(2026, 3, 2, 9)
    session = await session_at(db_session, at, unique_ip())
    turn = 0
    for outcome, count in _SEEDED.items():
        for _ in range(count):
            await exchange(
                db_session,
                session,
                turn,
                at,
                outcome=outcome,
                grounded=outcome == "answered",
            )
            turn += 1
    # Legacy turn without an outcome.
    await exchange(db_session, session, turn, at, outcome=None, grounded=None)
    await db_session.flush()


async def test_outcome_counts_normalise_every_outcome(db_session) -> None:
    before = await admin_routes._assistant_outcome_counts(db_session)
    await _seed(db_session)
    after = await admin_routes._assistant_outcome_counts(db_session)

    assert set(after) >= {
        "answered",
        "ungrounded",
        "blocked",
        "conversational",
        "llm_failed",
        "unknown",
    }
    delta = {k: after[k] - before.get(k, 0) for k in after}
    assert delta == {
        "answered": 3,
        "ungrounded": 3,
        "blocked": 2,
        "conversational": 1,
        "llm_failed": 2,
        "unknown": 1,
    }


async def test_usage_llm_counts_only_real_errors_as_failed(client, db_session) -> None:
    await _seed(db_session)
    admin_usage.clear_usage_caches()
    try:
        res = await client.get("/admin/usage/llm")
    finally:
        admin_usage.clear_usage_caches()
    assert res.status_code == 200
    body = res.json()
    outcomes = body["outcomes"]

    assert body["window"] == "all_time"
    assert body["latency_window_hours"] == admin_routes.USAGE_LATENCY_WINDOW_HOURS
    assert body["requests"] == sum(outcomes.values())
    assert body["failed"] == outcomes["llm_failed"]
    assert body["successful"] == body["requests"] - body["failed"]
    # Blocked / conversational / ungrounded turns are not failures.
    assert body["failed"] < (
        outcomes["llm_failed"]
        + outcomes["blocked"]
        + outcomes["conversational"]
        + outcomes["ungrounded"]
    )
