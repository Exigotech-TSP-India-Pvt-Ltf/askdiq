"""Usage hourly rollup (app.dashboard.usage_rollup): any range equals the raw rows.

The summary's aggregates read final hours from ``usage_hourly_rollup`` and the
rest live; every number must match the raw-row aggregation it replaced, before
any roll, after a roll, for ranges with partial edge hours, and after a late
change to an already rolled hour (database trigger). Needs Postgres (CI).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import admin_usage
from app.dashboard import queries, usage_rollup, usage_series
from app.dashboard.window import utcnow
from app.models.db_models import ChatMessageRecord, UsageRollupState
from tests.dashboard_helpers import audit, exchange, session_at, unique_day, unique_ip

AUDIENCES = ("overview", "users", "testing")


def _close(a: float, b: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)


async def _seed(db: AsyncSession, day: datetime) -> str:
    """Exchanges over several hours and two days, latency, ingestion rows."""
    ip = unique_ip()
    chat = await session_at(db, day + timedelta(hours=1), ip)
    anon = await session_at(db, day + timedelta(hours=2), None)
    details = {"cached_tokens": 40, "is_actual": "false"}
    tokens = {
        "prompt": 120,
        "completion": 60,
        "total": 180,
        "reasoning": 20,
        "embedding": 8,
        "guardrail": 4,
        "details": details,
    }
    plan = [
        (chat, 0, timedelta(hours=1, minutes=5), "answered", 900.0),
        (chat, 1, timedelta(hours=1, minutes=50), "ungrounded", 1500.0),
        (chat, 2, timedelta(hours=3, minutes=20), "blocked", None),
        (anon, 0, timedelta(hours=2, minutes=10), "clarify", 2400.0),
        (chat, 3, timedelta(hours=23, minutes=59, seconds=50), "llm_failed", 700.0),
        (anon, 1, timedelta(days=1, hours=4), "conversational", 300.0),
        (anon, 2, timedelta(days=1, hours=4, minutes=30), None, 1100.0),
    ]
    for i, (sess, turn, offset, outcome, ms) in enumerate(plan):
        await exchange(
            db,
            sess,
            turn,
            day + offset,
            outcome=outcome,
            cost=0.001 * (i + 1),
            latency_ms=ms,
            tokens={**tokens, "prompt": 100 + i, "details": details if i % 2 else None},
        )
    await audit(
        db,
        "ingest",
        day + timedelta(hours=1, minutes=30),
        {"embedding_tokens": 500, "cost_usd": 0.01},
        ip=ip,
    )
    await audit(db, "ingest", day + timedelta(hours=26), {"embedding_tokens": 70})
    return ip


async def _assert_parity(
    db: AsyncSession, lo: datetime | None, hi: datetime | None, day: datetime
) -> None:
    for audience in AUDIENCES:
        raw = await admin_usage._summary_totals(db, audience, lo, hi)
        ingest = await admin_usage._ingest_overhead(db, audience, lo, hi)
        outcomes = await admin_usage._outcome_counts(db, audience, lo, hi)
        lat = await admin_usage._latency_stats(db, audience, lo, hi)
        got = await usage_rollup.totals(db, audience, lo, hi)
        got_lat = await usage_rollup.latency(db, audience, lo, hi)
        assert got.total_turns == raw.total_turns
        assert got.sum_prompt == raw.sum_prompt
        assert got.sum_completion == raw.sum_completion
        assert got.sum_total_tokens == raw.sum_total_tokens
        assert got.sum_reasoning == raw.sum_reasoning
        assert got.sum_query_embed == raw.sum_query_embed
        assert got.sum_guardrail_embed == raw.sum_guardrail_embed
        assert got.estimated_turns == raw.estimated_turns
        assert _close(got.sum_cost, raw.sum_cost)
        assert _close(got.sum_cached, raw.sum_cached)
        assert got.ingest_tokens == ingest[0]
        assert _close(got.ingest_cost, ingest[1])
        assert got.outcomes == {k: outcomes.get(k, 0) for k in usage_rollup.OUTCOMES}
        for mine, theirs in zip(
            got_lat, (lat.avg_lat, lat.p50_lat, lat.p95_lat), strict=True
        ):
            assert _close(mine, theirs)
        for bucket in ("hour", "day"):
            tokens = await usage_series.stage_token_rows(db, audience, bucket, lo, hi)
            ingestion = await usage_series.ingestion_cost_rows(
                db, audience, bucket, lo, hi
            )
            rows = await usage_rollup.stage_buckets(db, audience, bucket, lo, hi)
            assert set(rows.tokens) == set(tokens)
            assert set(rows.ingestion) == set(ingestion)
            for key, values in tokens.items():
                assert all(_close(rows.tokens[key][k], v) for k, v in values.items())
            assert all(_close(rows.ingestion[k], v) for k, v in ingestion.items())
        month = day.replace(day=1)
        month_end = (month + timedelta(days=32)).replace(day=1)
        assert await usage_rollup.daily_cost(
            db, audience, month, month_end
        ) == pytest.approx(await queries.daily_cost(db, audience, month, month_end))


def _ranges(day: datetime) -> list[tuple[datetime | None, datetime | None]]:
    end = day + timedelta(days=2, microseconds=-1)
    return [
        (day, end),
        (day, day + timedelta(days=1, microseconds=-1)),
        (day + timedelta(hours=1, minutes=40), day + timedelta(hours=3, minutes=25)),
        (day + timedelta(hours=1, minutes=10), day + timedelta(hours=1, minutes=55)),
        (day + timedelta(hours=23), end),
    ]


async def _set_state(db: AsyncSession, synced_until: datetime) -> None:
    await db.execute(delete(UsageRollupState))
    db.add(
        UsageRollupState(id=usage_rollup.STATE_ID, synced_until=synced_until, version=0)
    )
    await db.flush()


async def test_rollup_parity_live_rolled_and_partial(db_session: AsyncSession) -> None:
    day = unique_day()
    await _seed(db_session, day)
    await db_session.execute(delete(UsageRollupState))
    # No roll yet: everything is aggregated live.
    for lo, hi in _ranges(day):
        await _assert_parity(db_session, lo, hi, day)

    # Hours up to day + 30 h are final; later ones stay live.
    synced = day + timedelta(hours=30)
    await usage_rollup.roll(db_session, day - timedelta(days=1), synced)
    await _set_state(db_session, synced)
    for lo, hi in _ranges(day):
        await _assert_parity(db_session, lo, hi, day)

    # Partially rolled: the rolled side ends inside the range.
    await _set_state(db_session, day + timedelta(hours=2))
    for lo, hi in _ranges(day):
        await _assert_parity(db_session, lo, hi, day)


async def test_late_change_reopens_rolled_hours(db_session: AsyncSession) -> None:
    day = unique_day()
    await _seed(db_session, day)
    synced = day + timedelta(days=3)
    await usage_rollup.roll(db_session, day - timedelta(days=1), synced)
    await _set_state(db_session, synced)

    # Deleting an old exchange (a user deleting a chat) moves the sync point
    # back, so the rolled hour reads live again and the numbers stay exact.
    await db_session.execute(
        delete(ChatMessageRecord).where(
            ChatMessageRecord.created_at == day + timedelta(hours=1, minutes=5)
        )
    )
    state = (
        await db_session.execute(
            select(UsageRollupState).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert state.synced_until == day
    assert state.version == 1
    for lo, hi in _ranges(day):
        await _assert_parity(db_session, lo, hi, day)

    # A late update to an old row reopens too (after rolling the hours again).
    await usage_rollup.roll(db_session, day - timedelta(days=1), synced)
    await db_session.execute(
        update(UsageRollupState).values(synced_until=synced, version=5)
    )
    await db_session.execute(
        update(ChatMessageRecord)
        .where(ChatMessageRecord.created_at == day + timedelta(days=1, hours=4))
        .values(cost_usd=1.5)
    )
    state = (
        await db_session.execute(
            select(UsageRollupState).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert state.synced_until == day + timedelta(days=1, hours=3)
    assert state.version == 6
    await _assert_parity(db_session, day, day + timedelta(days=2), day)


async def test_sync_rolls_until_overlap_and_is_idempotent(
    db_session: AsyncSession,
) -> None:
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=6)
    chat = await session_at(db_session, start, unique_ip())
    await exchange(db_session, chat, 0, start + timedelta(minutes=10), latency_ms=800.0)
    await _set_state(db_session, start)
    rolled = await usage_rollup.sync_usage_rollup(db_session)
    assert rolled == 1
    state = (
        await db_session.execute(
            select(UsageRollupState).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert state.synced_until <= now
    assert state.synced_until > start
    assert await usage_rollup.sync_usage_rollup(db_session) == 0
    await _assert_parity(db_session, start, None, start)


def test_hour_helpers() -> None:
    t = datetime(2026, 10, 20, 13, 0)
    assert usage_rollup.floor_hour(t + timedelta(minutes=59)) == t
    assert usage_rollup.ceil_hour(t) == t
    assert usage_rollup.ceil_hour(t + timedelta(microseconds=1)) == t + timedelta(
        hours=1
    )


async def test_summary_endpoint_uses_rollup_numbers(client, db_session) -> None:
    day = unique_day()
    await _seed(db_session, day)
    params = {
        "audience": "overview",
        "from_date": day.date().isoformat(),
        "to_date": (day + timedelta(days=1)).date().isoformat(),
    }
    data = (await client.get("/admin/usage/summary", params=params)).json()
    assert data["accounting"]["total_turns"] == 7, (data["quality"], data["tokens"])
    assert data["quality"]["outcomes"]["clarify"] == 1
    assert data["tokens"]["embeddings_tokens"]["ingestion_embed_tokens"] == 570
    assert data["quality"]["latency"]["p50_ms"] == 1000.0
    assert "as_of" in data
