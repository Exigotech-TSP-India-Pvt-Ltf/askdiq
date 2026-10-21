"""Preference-pair preview (GET /admin/feedback/pairs) and batched export."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, insert, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.feedback import repository as repo
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


async def _pair_ids(db_session, window, batch_size: int) -> list[str]:
    async def collect() -> list[str]:
        return [
            p["feedback_id"]
            async for p in service.iter_pairs(
                db_session,
                triage_status=None,
                include_dismissed=False,
                complete_only=False,
                batch_size=batch_size,
                window=window,
            )
        ]

    # A cursor that does not advance would loop forever: fail fast instead.
    return await asyncio.wait_for(collect(), timeout=30)


async def test_export_pages_advance_through_tied_timestamps(db_session):
    tied = datetime(2005, 5, 5, 9, 0, 0, 123456)
    session = await make_session(db_session, "ties@example.com")
    ids = []
    for turn in range(7):
        _u, asst = await make_exchange(
            db_session, session, turn=turn, question=f"{marker()} q{turn}"
        )
        fb = await make_feedback(
            db_session, asst, actor=f"t{turn}@example.com", rating=1, created_at=tied
        )
        ids.append(fb.id)
    window = (tied, tied + timedelta(seconds=1))
    expected = [str(i) for i in sorted(ids)]  # ties break on id
    for batch_size in (1, 2, 3, 7, 8):
        assert await _pair_ids(db_session, window, batch_size) == expected


BULK_DAY = datetime(2006, 6, 6, 9, 0, 0)


async def _bulk_pairs(db_session, n: int) -> set[str]:
    """``n`` rated exchanges in ONE session at one timestamp (worst case),
    never analysed: the planner sees a fresh table, as in a test database.

    The per-row ledger trigger is disabled for the load (inside the test's
    transaction, so the rollback restores it): without statistics it would
    take minutes, and ANALYZE would hide the plan this test is about.
    """
    session = await make_session(db_session, "bulk-pairs@example.com")
    messages, feedback = [], []
    for turn in range(n):
        asst_id = uuid.uuid4()
        for role, msg_id in (("user", uuid.uuid4()), ("assistant", asst_id)):
            messages.append(
                {
                    "id": msg_id,
                    "session_id": session.id,
                    "turn_index": turn,
                    "role": role,
                    "content": f"{role} {turn}",
                    "created_at": BULK_DAY,
                }
            )
        feedback.append(
            {
                "id": uuid.uuid4(),
                "message_id": asst_id,
                "session_id": session.id,
                "actor": "bulk-pairs@example.com",
                "rating": 1,
                "reason_tags": [],
                "corrected_answer": f"Corrected {turn}.",
                "created_at": BULK_DAY,
                "updated_at": BULK_DAY,
            }
        )
    await db_session.execute(
        text("ALTER TABLE chat_messages DISABLE TRIGGER trg_usage_turns_message_insert")
    )
    await db_session.execute(insert(ChatMessageRecord), messages)
    await db_session.execute(insert(MessageFeedback), feedback)
    return {str(row["id"]) for row in feedback}


async def _plan(db_session, stmt) -> str:
    sql = stmt.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    rows = await db_session.execute(text(f"EXPLAIN (COSTS OFF) {sql}"))
    return "\n".join(row[0] for row in rows)


async def test_export_page_probes_the_question_per_row(db_session):
    """A later page of the default (unwindowed) export, planned on fresh
    statistics-less tables, must not multiply feedback rows by messages.

    The old statement joined the question with a plain join and paged with
    an ``OR`` cursor: the planner (believing ~15 user messages) joined
    every remaining feedback row, filtered every user message per row
    (``Join Filter``, ~6M rows per page at 3000 pairs) and sorted; at 10k
    that is the multi-minute "one active query" stall.
    """
    await _bulk_pairs(db_session, 3000)
    stmt = repo.pairs_stmt(
        triage_status=None, include_dismissed=False, window=None, corrected_only=False
    )
    head = (await db_session.execute(repo.pairs_page(stmt, None, 1000))).all()
    page = repo.pairs_page(stmt, (head[-1].created_at, head[-1].id), 100)
    for nested_loops_only in (False, True):
        if nested_loops_only:
            await db_session.execute(text("SET LOCAL enable_hashjoin = off"))
            await db_session.execute(text("SET LOCAL enable_mergejoin = off"))
        plan = await _plan(db_session, page)
        # The question is a per-row index probe, never a filter over all
        # user messages; the cursor is one row comparison (index-rangeable).
        assert "Join Filter" not in plan, plan
        assert "ix_chat_messages_session_turn" in plan, plan
        assert "ROW(created_at, id) > ROW(" in plan, plan
    await db_session.execute(text("RESET enable_hashjoin"))
    await db_session.execute(text("RESET enable_mergejoin"))


async def test_export_of_one_big_session_is_linear(db_session):
    """3000 pairs in one session at one timestamp, 100 per page: every row
    once, in a bounded time (the old statement was quadratic in rows)."""
    seeded = await _bulk_pairs(db_session, 3000)
    started = time.perf_counter()
    ids = await _pair_ids(db_session, None, batch_size=100)
    elapsed = time.perf_counter() - started
    assert len(ids) == len(set(ids))
    assert seeded <= set(ids)
    assert elapsed < 5, elapsed


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
