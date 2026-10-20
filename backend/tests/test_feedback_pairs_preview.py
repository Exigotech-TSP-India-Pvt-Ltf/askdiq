"""Preference-pair preview (GET /admin/feedback/pairs) and batched export."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.feedback import service
from app.models.db_models import ChatMessageRecord, ChatSession, MessageFeedback
from tests.feedback_helpers import make_exchange, make_feedback, make_session, marker
from tests.pg_helpers import new_engine, require_postgres

# A day no other fixture rates on, so the window isolates this test's rows.
DAY = datetime(2003, 3, 3, 9, 0, 0)
WINDOW = "from=2003-03-03&to=2003-03-03"


async def _rate(db_session, n: int, *, corrected_every: int = 2) -> list[str]:
    mk = marker()
    session = await make_session(db_session, "pairs@example.com")
    ids = []
    for turn in range(n):
        _u, asst = await make_exchange(
            db_session, session, turn=turn, question=f"{mk} question {turn}"
        )
        fb = await make_feedback(
            db_session,
            asst,
            actor=f"r{turn}@example.com",
            rating=1 + turn % 5,
            corrected_answer="Better." if turn % corrected_every == 0 else None,
            created_at=DAY + timedelta(minutes=turn),
        )
        ids.append(str(fb.id))
    return ids


async def test_preview_is_the_head_of_the_export(client, db_session):
    ids = await _rate(db_session, 5)

    export = await client.get(f"/admin/feedback/export?format=jsonl&{WINDOW}")
    assert export.status_code == 200
    lines = [json.loads(line) for line in export.text.splitlines() if line]
    assert [p["feedback_id"] for p in lines] == ids

    res = await client.get(f"/admin/feedback/pairs?limit=3&{WINDOW}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["truncated"] is True
    assert body["pairs"] == lines[:3]

    whole = (await client.get(f"/admin/feedback/pairs?limit=5&{WINDOW}")).json()
    assert whole["truncated"] is False
    assert whole["pairs"] == lines


async def test_preview_complete_only_matches_the_export(client, db_session):
    await _rate(db_session, 6)
    export = await client.get(
        f"/admin/feedback/export?format=jsonl&complete_only=true&{WINDOW}"
    )
    lines = [json.loads(line) for line in export.text.splitlines() if line]
    assert lines and all(p["complete"] for p in lines)

    body = (
        await client.get(f"/admin/feedback/pairs?complete_only=true&limit=50&{WINDOW}")
    ).json()
    assert body == {"pairs": lines, "truncated": False}


async def test_preview_validates_limit(client):
    assert (await client.get("/admin/feedback/pairs?limit=0")).status_code == 422
    assert (await client.get("/admin/feedback/pairs?limit=101")).status_code == 422


async def test_export_batches_cover_every_row_once(db_session):
    ids = await _rate(db_session, 7)
    window = (DAY, DAY + timedelta(days=1))
    pairs = [
        json.loads(line)
        async for line in service.export_pairs_jsonl(
            db_session,
            triage_status=None,
            include_dismissed=False,
            complete_only=False,
            batch_size=10,
            window=window,
        )
    ]
    small = [
        p
        async for p in service.iter_pairs(
            db_session,
            triage_status=None,
            include_dismissed=False,
            complete_only=False,
            batch_size=2,
            window=window,
        )
    ]
    assert [p["feedback_id"] for p in pairs] == ids
    assert small == pairs


# ---------------------------------------------------------------------------
# A stalled or vanished download client must not pin a pooled connection
# (or leave a session idle in transaction holding locks).
# ---------------------------------------------------------------------------

LEAK_DAY = datetime(2004, 4, 4, 9, 0, 0)


async def _committed_rows(engine) -> ChatSession:
    async with AsyncSession(engine, expire_on_commit=False) as db:
        session = await make_session(db, "leak@example.com")
        for turn in range(5):
            _u, asst = await make_exchange(
                db, session, turn=turn, question=f"{marker()} q{turn}"
            )
            await make_feedback(
                db,
                asst,
                actor=f"leak{turn}@example.com",
                rating=1,
                created_at=LEAK_DAY + timedelta(minutes=turn),
            )
    return session


async def _cleanup(engine, session_id) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            delete(MessageFeedback).where(MessageFeedback.session_id == session_id)
        )
        await conn.execute(
            delete(ChatMessageRecord).where(ChatMessageRecord.session_id == session_id)
        )
        await conn.execute(delete(ChatSession).where(ChatSession.id == session_id))


async def _state(engine, pid: int) -> str | None:
    async with engine.connect() as conn:
        return await conn.scalar(
            text("SELECT state FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
        )


async def test_cancelled_export_holds_no_connection():
    engine = new_engine(pool_size=1, max_overflow=0)
    probe = new_engine()
    await require_postgres(engine)
    session = await _committed_rows(engine)
    window = (LEAK_DAY, LEAK_DAY + timedelta(days=1))
    try:
        async with AsyncSession(engine) as db:
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            await db.commit()
            stream = service.export_pairs_jsonl(
                db,
                triage_status=None,
                include_dismissed=False,
                complete_only=False,
                batch_size=2,
                window=window,
            )
            first = await anext(stream)
            assert json.loads(first)["rating"] == 1
            # The client stops reading here: nothing may stay checked out.
            assert engine.pool.checkedout() == 0
            assert await _state(probe, pid) == "idle"
            await stream.aclose()  # the response is cancelled

            pairs, truncated = await service.preview_pairs(
                db, complete_only=False, limit=2, window=window
            )
            assert len(pairs) == 2
            assert truncated is True
            assert engine.pool.checkedout() == 0
            assert await _state(probe, pid) == "idle"
        assert engine.pool.checkedout() == 0
    finally:
        await _cleanup(engine, session.id)
        await engine.dispose()
        await probe.dispose()
