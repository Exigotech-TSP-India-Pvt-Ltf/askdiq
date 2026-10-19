"""Live-data integration for insights (planchanges4 B6.4.3).

The real pipeline end to end: ``/query`` (LLM and retrieval mocked) persists
the exchanges and enqueues ``insights.classify`` in the same transaction; the
job worker labels them (classifier LLM mocked); the rollup runs; every
dashboard endpoint is queried, the taxonomy is edited and the result
re-checked. All data is committed, so each request is scoped to this test's
own person pseudonym and everything is removed afterwards.

``test_summary_p95_on_100k_turns`` (marker ``perf``) seeds 100k turns inside
a rolled-back transaction and records the summary's p95 latency.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
import warnings
from collections.abc import AsyncIterator
from datetime import timedelta
from functools import partial
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes.insights import get_parallel_runner
from app.core import middleware, sessions
from app.core.concurrency import gather_scoped
from app.core.config import get_settings
from app.core.database import get_db, read_only_session
from app.governance import online_eval
from app.harness import orchestrator
from app.insights import jobs as insights_jobs
from app.insights.analytics_repository import (
    InsightsAnalyticsRepository,
    person_salt,
)
from app.insights.repository import InsightsRepository
from app.insights.service import InsightsService
from app.insights.triggers import CLASSIFY_JOB, classify_dedupe_key
from app.jobs import queue
from app.jobs.worker import JobWorker
from app.schemas.query import SourceChunk
from main import app
from tests.pg_helpers import new_engine, require_postgres
from tests.test_insights_classifier import FakeLLM

settings = get_settings()
BASE = "/admin/insights"
MAX_JOBS = 50
PERF_SESSIONS = 20_000
PERF_TURNS_PER_SESSION = 5  # 100k exchanges
PERF_REQUESTS = 20
PERF_BUDGET_MS = float(os.environ.get("INSIGHTS_PERF_P95_MS", "600"))


def person_id(email: str) -> str:
    salt = person_salt(settings.session_secret.get_secret_value())
    return hashlib.sha256((salt + email.lower()).encode()).hexdigest()[:16]


def _source() -> SourceChunk:
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content="DeployIQ maps controls to the Essential Eight.",
        score=0.9,
        metadata={"source_name": "security.md"},
        embedding=[0.05] * 1536,
    )


@pytest.fixture
async def live(monkeypatch, fake_embed, fake_llm) -> AsyncIterator[dict[str, Any]]:
    engine = new_engine()
    await require_postgres(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    actor = f"insights-live-{uuid.uuid4().hex[:10]}"
    model = f"fake-live-{uuid.uuid4().hex[:8]}"

    async def _db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def _retrieve(*_a: Any, **_k: Any) -> list[SourceChunk]:
        return [_source()]

    async def _post_checks(*_a: Any, **_k: Any) -> tuple[bool, float, list[str]]:
        return True, 0.95, []

    async def _no_schedule(**_kw: Any) -> None:
        return None

    service = InsightsService(engine=engine, llm=FakeLLM(model=model))
    monkeypatch.setattr(orchestrator, "retrieve", _retrieve)
    monkeypatch.setattr(orchestrator, "run_post_checks", _post_checks)
    monkeypatch.setattr(sessions, "AsyncSessionLocal", factory)
    monkeypatch.setattr(middleware, "AsyncSessionLocal", factory)
    monkeypatch.setattr(online_eval, "AsyncSessionLocal", factory)
    monkeypatch.setattr(settings, "rate_limit_exempt_actors", [actor])
    monkeypatch.setattr(settings, "insights_classify_on_write", True)
    monkeypatch.setattr(settings, "insights_daily_token_budget", 10**9)
    monkeypatch.setattr(insights_jobs, "InsightsService", lambda: service)
    monkeypatch.setattr(insights_jobs, "ensure_rollup_scheduled", _no_schedule)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[read_only_session] = _db
    app.dependency_overrides[get_parallel_runner] = lambda: partial(
        gather_scoped, session_factory=factory
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-API-Key": settings.api_key_name, "X-Client-Id": actor},
        timeout=30,
    ) as client:
        try:
            yield {
                "client": client,
                "engine": engine,
                "service": service,
                "actor": actor,
                "person": person_id(actor),
            }
        finally:
            for dep in (get_db, read_only_session, get_parallel_runner):
                app.dependency_overrides.pop(dep, None)
            await sessions.drain_pending_writes(actor)
            async with engine.begin() as conn:
                ids = (
                    await conn.execute(
                        text(
                            "SELECT m.id FROM chat_messages m JOIN chat_sessions s "
                            "ON s.id = m.session_id WHERE s.user_email = :a"
                        ),
                        {"a": actor},
                    )
                ).scalars()
                keys = [classify_dedupe_key(i) for i in ids]
                await conn.execute(
                    text("DELETE FROM jobs WHERE dedupe_key = ANY(:k)"), {"k": keys}
                )
                await conn.execute(
                    text("DELETE FROM chat_sessions WHERE user_email = :a"),
                    {"a": actor},
                )
                await conn.execute(
                    text("DELETE FROM insight_llm_usage WHERE model = :m"),
                    {"m": model},
                )
            await engine.dispose()


async def _run_classify_jobs(engine: Any) -> int:
    worker = JobWorker(types=[CLASSIFY_JOB], engine=engine)
    done = 0
    for _ in range(MAX_JOBS):
        claimed = await queue.claim(
            1, "insights-live-test", [CLASSIFY_JOB], engine=engine
        )
        if not claimed:
            break
        await worker._execute(claimed[0])
        done += 1
    return done


async def _get(client: httpx.AsyncClient, path: str, **params: str) -> dict:
    response = await client.get(f"{BASE}/{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_query_to_worker_to_rollup_to_endpoints(live):
    client, person = live["client"], live["person"]
    session_id = None
    for question in (
        "hello",
        "What is Essential Eight maturity?",
        "Do you integrate with Jira?",
    ):
        body: dict[str, Any] = {"query": question}
        if session_id:
            body["session_id"] = session_id
        response = await client.post("/query", json=body)
        assert response.status_code == 200, response.text
        session_id = response.json()["session_id"]
    await sessions.drain_pending_writes(live["actor"])

    # Before the worker runs, the turns are pending and not on the dashboard.
    detail = await _get(client, f"conversations/{session_id}")
    assert detail["conversation"] is None
    assert [t["pending"] for t in detail["turns"]] == [True, True, True]
    pending = await _get(client, "pending")
    assert pending["pending"] >= 3

    assert await _run_classify_jobs(live["engine"]) >= 1
    detail = await _get(client, f"conversations/{session_id}")
    assert [t["pending"] for t in detail["turns"]] == [False, False, False]
    assert [t["intent"] for t in detail["turns"]] == ["GRE", "SEC", "SVC"]

    await live["service"].rollup()

    scoped = {"person": person}
    summary = await _get(client, "summary", **scoped)
    assert summary["conversations"] == 1
    assert summary["exchanges"] == 3
    assert summary["substantive_questions"] == 2
    assert summary["handled_questions"] == 2
    assert summary["served_pct"] == 100.0
    assert summary["people"] == 1
    assert summary["source"] == "live"  # a person filter cannot use the rollup

    trend = await _get(client, "trend", **scoped)
    assert sum(d["questions"] for d in trend["days"]) == 2
    hourly = await _get(client, "hourly", **scoped)
    assert hourly["total"] == 2
    flow = await _get(client, "flow", **scoped)
    assert flow["links"] == [
        {"user_type": "Customer / prospect", "outcome": "Served", "conversations": 1}
    ]
    topics = await _get(client, "topics", **scoped)
    assert {i["intent"]: i["questions"] for i in topics["items"]} == {
        "SEC": 1,
        "SVC": 1,
    }
    quality = await _get(client, "quality", **scoped)
    assert {i["quality"]: i["questions"] for i in quality["items"]} == {"OK": 2}
    dropoff = await _get(client, "dropoff", **scoped)
    assert dropoff["items"] == []
    people = await _get(client, "people", **scoped)
    # Labels are numbered over the whole audience: stable under every filter.
    assert people["items"][0]["person"].startswith("P")
    person_detail = await _get(client, f"people/{person}")
    assert person_detail["person"] == people["items"][0]["person"]
    assert person_detail["identity"] == "account"
    assert person_detail["conversations"] == 1
    assert people["items"][0]["dominant_outcome"] == "Served"
    conversations = await _get(client, "conversations", **scoped)
    assert [r["conv_id"] for r in conversations["items"]] == [session_id]
    assert conversations["items"][0]["experience_rating"] == "Good"

    # Unscoped summary runs the parallel (gather_scoped) path on real data.
    overall = await client.get(f"{BASE}/summary")
    assert overall.status_code == 200
    assert overall.json()["conversations"] >= 1

    # Edit the taxonomy and re-check: labels follow, counts do not move.
    async with live["engine"].connect() as conn:
        original = await conn.scalar(
            text(
                "SELECT label FROM insight_taxonomy "
                "WHERE kind = 'intent' AND code = 'SEC'"
            )
        )
    try:
        renamed = await client.patch(
            f"{BASE}/taxonomy/intent/SEC", json={"label": "Security (renamed)"}
        )
        assert renamed.status_code == 200, renamed.text
        topics = await _get(client, "topics", **scoped)
        sec = next(i for i in topics["items"] if i["intent"] == "SEC")
        assert sec["label"] == "Security (renamed)"
        assert sec["questions"] == 1
    finally:
        async with live["engine"].begin() as conn:
            await conn.execute(
                text(
                    "UPDATE insight_taxonomy SET label = :l "
                    "WHERE kind = 'intent' AND code = 'SEC'"
                ),
                {"l": original},
            )


# ---------------------------------------------------------------------------
# Performance (100k turns; rolled back)
# ---------------------------------------------------------------------------

_PERF_SEED = (
    """
    CREATE TEMP TABLE perf_sessions ON COMMIT DROP AS
    SELECT gen_random_uuid() AS id, g,
           (now() AT TIME ZONE 'UTC')
               - make_interval(days => g % 90, mins => g % 1440) AS ts
    FROM generate_series(1, :sessions) AS g
    """,
    """
    INSERT INTO chat_sessions
        (id, user_email, title, created_at, last_activity_at, expires_at, created_ip)
    SELECT id, 'perf-' || (g % 5000) || '@perf.invalid', 'perf', ts, ts, ts,
           '10.77.' || (g % 250) || '.1'
    FROM perf_sessions
    """,
    """
    INSERT INTO chat_messages (id, session_id, turn_index, role, content, created_at)
    SELECT gen_random_uuid(), s.id, t, r.role, 'question ' || t,
           s.ts + make_interval(mins => t)
    FROM perf_sessions s
    CROSS JOIN generate_series(0, :turns - 1) AS t
    CROSS JOIN (VALUES ('user'), ('assistant')) AS r(role)
    """,
    """
    INSERT INTO turn_insights
        (id, message_id, session_id, intent, bot_quality, issue, substantive,
         handled, confidence, classified_by, classified_at)
    SELECT gen_random_uuid(), m.id, m.session_id,
           (ARRAY['GRE', 'SVC', 'SEC', 'CO', 'CON'])[m.turn_index + 1],
           (ARRAY['ACK', 'OK', 'PART', 'OK', 'FB'])[m.turn_index + 1],
           CASE WHEN m.turn_index IN (2, 4) THEN 'KB_GAP' END,
           m.turn_index > 0,
           m.turn_index IN (0, 1, 3),
           1.0, 'rule', now() - interval '1 day'
    FROM chat_messages m
    JOIN perf_sessions s ON s.id = m.session_id
    WHERE m.role = 'assistant'
    """,
    "ANALYZE chat_messages",
    "ANALYZE turn_insights",
    "ANALYZE chat_sessions",
)


@pytest.mark.perf
async def test_summary_p95_on_100k_turns(
    insights_client, insights_reference_db, record_property
):
    db = insights_reference_db
    for statement in _PERF_SEED:
        await db.execute(
            text(statement),
            {"sessions": PERF_SESSIONS, "turns": PERF_TURNS_PER_SESSION},
        )
    turns = await db.scalar(text("SELECT count(*) FROM turn_insights"))
    assert turns >= PERF_SESSIONS * PERF_TURNS_PER_SESSION
    # Closed days come from the rollup (the default "all" view's fast path).
    await db.execute(
        text("UPDATE turn_insights SET classified_at = now() - interval '1 day'")
    )
    today = await InsightsAnalyticsRepository(db).today(settings.insights_timezone)
    days = [today - timedelta(days=i) for i in range(1, 400)]
    await InsightsRepository(db).recompute_days(days, settings.insights_timezone, [])

    timings = []
    for _ in range(PERF_REQUESTS):
        started = time.perf_counter()
        response = await insights_client.get(f"{BASE}/summary")
        timings.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200
    body = response.json()
    assert body["source"] == "rollup"
    assert body["exchanges"] >= PERF_SESSIONS * PERF_TURNS_PER_SESSION
    timings.sort()
    p95 = timings[int(0.95 * len(timings)) - 1]
    record_property("insights_summary_p95_ms", round(p95, 1))
    # Surfaces in the CI log's warnings summary, so every run records the value.
    warnings.warn(
        f"insights summary p95 over {turns} turns: {p95:.1f} ms",
        UserWarning,
        stacklevel=1,
    )
    assert p95 < PERF_BUDGET_MS, f"summary p95 {p95:.1f} ms"


async def test_session_turn_statistics_exist(db_session):
    """Migration bd75f2033c21: the planner has multi-column ndistinct
    statistics for the user-turn join used by every insights query."""
    kinds = await db_session.scalar(
        text(
            "SELECT CAST(stxkind AS text[]) FROM pg_statistic_ext "
            "WHERE stxname = 'stx_chat_messages_session_turn' "
            "AND stxrelid = 'chat_messages'::regclass"
        )
    )
    assert kinds is not None
    assert "d" in kinds  # ndistinct
