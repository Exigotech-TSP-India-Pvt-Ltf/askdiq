"""50+ concurrency CI gate (plan B1.7.3, newplan section 13.5).

60 simultaneous requests hit the ASGI app through ``asyncio.gather``: 30
``/query`` (fake LLM with a 1 s delay, fake embeddings, fake retrieval), 20
admin dashboard GETs and 10 feedback POSTs, against real Postgres with a
real connection pool (one session per request). All must succeed, and the
wall time must stay under 3 s, proving they ran concurrently rather than one
after another (~60 s). Needs Postgres, so it runs in CI.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncGenerator
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import middleware, rate_limit, sessions, tasks
from app.core.config import get_settings
from app.core.database import get_db
from app.governance import online_eval
from app.models.db_models import ChatMessageRecord, ChatSession
from app.schemas.query import SourceChunk
from main import app
from tests.loop_guard import LoopBlockingDetector
from tests.pg_helpers import new_engine, require_postgres

settings = get_settings()

# Timing gates: CI runs them without coverage tracing (see ci.yml).
pytestmark = pytest.mark.perf

QUERY_COUNT = 30
DASHBOARD_COUNT = 20
FEEDBACK_COUNT = 10
LLM_DELAY_SECONDS = 1.0
WALL_TIME_BUDGET_SECONDS = 3.0
QUESTION = "How does DeployIQ handle zero trust onboarding for new tenants?"


def _sources() -> list[SourceChunk]:
    return [
        SourceChunk(
            chunk_id=f"chunk-{i}",
            document_id="zero-trust-guide",
            content=(
                "DeployIQ onboards new tenants with zero trust controls: "
                "identity verification, least privilege and continuous monitoring."
            ),
            score=0.9,
            metadata={"source_name": "Zero Trust Guide"},
        )
        for i in range(3)
    ]


async def _fake_retrieve(*_args, **_kwargs) -> list[SourceChunk]:
    return _sources()


async def _slow_llm(*_args, **_kwargs):
    await asyncio.sleep(LLM_DELAY_SECONDS)
    return (
        "DeployIQ onboards new tenants with zero trust controls such as identity "
        "verification and least privilege.",
        {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
    )


async def _drain_background_writes() -> None:
    pending = [
        *(t for bucket in sessions._pending_writes.values() for t in bucket),
        *tasks._tasks,
        *online_eval._background_tasks,
    ]
    if pending:
        await asyncio.wait(pending, timeout=15)


async def _cleanup(factory, actors: list[str]) -> None:
    async with factory() as db:
        await db.execute(delete(ChatSession).where(ChatSession.user_email.in_(actors)))
        await db.commit()


@pytest.fixture
async def conc_env(fake_embed, monkeypatch) -> AsyncGenerator[dict, None]:
    """Real pooled engine, fakes for the slow externals, seeded feedback rows."""
    engine = new_engine(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=10,
    )
    await require_postgres(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    run_id = uuid.uuid4().hex[:10]
    query_actors = [f"conc50-{run_id}-q{i}" for i in range(QUERY_COUNT)]
    feedback_actors = [f"conc50-{run_id}-f{i}" for i in range(FEEDBACK_COUNT)]

    # Settings injection: test users are exempt from the per-user limit and
    # the shared test-client IP gets headroom for the burst.
    monkeypatch.setattr(settings, "rate_limit_exempt_actors", query_actors)
    monkeypatch.setattr(settings, "rate_limit_per_minute_per_ip", 10_000)
    # Fresh in-process windows so this burst does not count against later tests.
    for log_name in (
        "_user_request_log",
        "_ip_request_log",
        "_feedback_user_log",
        "_feedback_ip_log",
    ):
        monkeypatch.setattr(rate_limit, log_name, defaultdict(deque))
    # Background writers use the same pool as the requests.
    monkeypatch.setattr(sessions, "AsyncSessionLocal", factory)
    monkeypatch.setattr(middleware, "AsyncSessionLocal", factory)
    monkeypatch.setattr(online_eval, "AsyncSessionLocal", factory)

    targets = []
    async with factory() as db:
        for actor in feedback_actors:
            chat = ChatSession(
                user_email=actor,
                title="conc50",
                created_ip="10.9.9.9",
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
            db.add(chat)
            await db.flush()
            message = ChatMessageRecord(
                session_id=chat.id,
                turn_index=1,
                role="assistant",
                content="Seeded answer for the concurrency gate.",
                ip_address="10.9.9.9",
            )
            db.add(message)
            await db.flush()
            targets.append((actor, str(chat.id), str(message.id)))
        await db.commit()

    app.dependency_overrides[get_db] = _get_db
    with ExitStack() as stack:
        stack.enter_context(
            patch("app.harness.orchestrator.retrieve", side_effect=_fake_retrieve)
        )
        stack.enter_context(
            patch("app.harness.orchestrator.call_llm", side_effect=_slow_llm)
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers={"X-API-Key": settings.api_key_name or "ci-test-key"},
                timeout=30,
            ) as client:
                yield {
                    "client": client,
                    "query_actors": query_actors,
                    "feedback_targets": targets,
                }
        finally:
            app.dependency_overrides.pop(get_db, None)
            await _drain_background_writes()
            await _cleanup(factory, query_actors + feedback_actors)
            await engine.dispose()


def _query(env: dict, index: int):
    return env["client"].post(
        "/query",
        json={"query": QUESTION},
        headers={"X-Client-Id": env["query_actors"][index]},
    )


def _dashboard(env: dict):
    return env["client"].get("/admin/dashboard?audience=overview")


def _feedback(env: dict, index: int):
    actor, session_id, message_id = env["feedback_targets"][index]
    return env["client"].post(
        f"/sessions/{session_id}/messages/{message_id}/feedback",
        json={"rating": 5, "comment": "concurrency gate"},
        headers={"X-Client-Id": actor},
    )


async def test_60_concurrent_requests_all_succeed_within_3s(conc_env):
    # Warm-up: first-use imports, statement compilation and pool connections.
    assert (await _dashboard(conc_env)).status_code == 200

    calls = [
        *(_query(conc_env, i) for i in range(QUERY_COUNT)),
        *(_dashboard(conc_env) for _ in range(DASHBOARD_COUNT)),
        *(_feedback(conc_env, i) for i in range(FEEDBACK_COUNT)),
    ]
    assert len(calls) == 60

    started = time.perf_counter()
    responses = await asyncio.gather(*calls)
    wall = time.perf_counter() - started

    failures = [
        (r.request.method, r.request.url.path, r.status_code, r.text[:200])
        for r in responses
        if r.status_code not in (200, 201)
    ]
    assert not failures, failures
    answers = [r.json() for r in responses[:QUERY_COUNT]]
    assert all(a["outcome"] != "llm_failed" for a in answers)
    assert len({a["session_id"] for a in answers}) == QUERY_COUNT
    assert wall < WALL_TIME_BUDGET_SECONDS, f"60 requests took {wall:.2f}s"


async def test_concurrent_mix_never_blocks_the_event_loop(conc_env):
    # Warm up outside the detector, then run a concurrent mix with asyncio
    # debug mode watching for callbacks that block the loop for > 50 ms.
    assert (await _dashboard(conc_env)).status_code == 200
    assert (await _query(conc_env, 0)).status_code == 200

    async with LoopBlockingDetector():
        responses = await asyncio.gather(
            *(_query(conc_env, i) for i in range(1, 11)),
            *(_dashboard(conc_env) for _ in range(5)),
            *(_feedback(conc_env, i) for i in range(5)),
        )
    assert all(r.status_code == 200 for r in responses)
