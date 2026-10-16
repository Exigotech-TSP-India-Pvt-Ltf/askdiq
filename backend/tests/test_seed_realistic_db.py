"""seed_realistic_activity.py against real Postgres (runs in CI).

Seeds a small, tagged dataset in a far-past window (1995, outside every other
test's ``unique_day`` range), asserts the main dashboard endpoints return
non-empty data, then purges it. Seeded ``audit_log`` rows stay (append-only).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.db_models import (
    ChatMessageRecord,
    EvaluationRecord,
    InsightDailyRollup,
    LatencyRecord,
)
from scripts import seed_realistic_activity as seed
from tests.pg_helpers import new_engine, require_postgres

pytestmark = pytest.mark.db

NOW = datetime(1995, 6, 20, 0)
CFG = seed.SeedConfig(seed=19950620, scale=0.01, days=10, eval_rate=0.5, now=NOW)
FROM_DAY = (NOW - timedelta(days=11)).date().isoformat()
TO_DAY = NOW.date().isoformat()


async def _seeded_count(engine, model, column) -> int:
    async with engine.connect() as conn:
        stmt = select(func.count()).select_from(model).where(column.startswith("seed-"))
        return int(await conn.scalar(stmt) or 0)


async def test_seed_fills_dashboards_then_purges(client) -> None:
    engine = new_engine()
    await require_postgres(engine)
    try:
        await seed.purge(engine, CFG, apply=True)
        counts = await seed.seed(engine, CFG)
        assert counts["chat_sessions"] >= 50
        assert counts["chat_messages"] >= 100
        # Idempotent: the same seed is detected and nothing is re-inserted.
        assert await seed.seed(engine, CFG) == {}

        assert await _seeded_count(
            engine, ChatMessageRecord, ChatMessageRecord.request_id
        ) == (counts["chat_messages"] // 2)
        assert await _seeded_count(engine, LatencyRecord, LatencyRecord.request_id) > 0
        assert (
            await _seeded_count(engine, EvaluationRecord, EvaluationRecord.request_id)
            > 0
        )
        async with engine.connect() as conn:
            rollup = await conn.scalar(
                select(func.coalesce(func.sum(InsightDailyRollup.count), 0)).where(
                    InsightDailyRollup.day.between(
                        NOW.date() - timedelta(days=12), NOW.date()
                    )
                )
            )
        assert rollup > 0

        window = {
            "from": f"{FROM_DAY}T00:00:00Z",
            "to": f"{TO_DAY}T00:00:00Z",
        }
        dash = await client.get("/admin/dashboard", params=window)
        assert dash.status_code == 200, dash.text
        data = dash.json()
        assert data["queries"] > 0 and data["sessions"] > 0 and data["users"] > 0
        assert data["cost_usd"] > 0

        visitors = await client.get(
            "/admin/visitors/stats", params={"from": FROM_DAY, "to": TO_DAY}
        )
        assert visitors.status_code == 200, visitors.text
        assert visitors.json()["kpis"]["visitors"] > 0

        usage = await client.get(
            "/admin/usage/summary",
            params={"from_date": window["from"], "to_date": window["to"]},
        )
        assert usage.status_code == 200, usage.text
        assert usage.json()["cache"]["prompt_tokens"] > 0

        insights = await client.get(
            "/admin/insights/summary",
            params={"period": "custom", "from": FROM_DAY, "to": TO_DAY},
        )
        assert insights.status_code == 200, insights.text
        assert insights.json()["conversations"] > 0

        conversations = await client.get(
            "/admin/conversations", params={"from": FROM_DAY, "to": TO_DAY}
        )
        assert conversations.status_code == 200, conversations.text
        assert conversations.json()["items"]
    finally:
        purged = await seed.purge(engine, CFG, apply=True)
        await engine.dispose()
    assert purged["chat_sessions"] >= 50
    engine = new_engine()
    try:
        assert (
            await _seeded_count(engine, ChatMessageRecord, ChatMessageRecord.request_id)
            == 0
        )
        assert await _seeded_count(engine, LatencyRecord, LatencyRecord.request_id) == 0
    finally:
        await engine.dispose()
