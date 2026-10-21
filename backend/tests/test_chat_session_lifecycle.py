"""Chat session lifecycle (plan §9 OR-6, decision D1).

- "New chat" is lazy: ``new_session`` builds a transient session that only
  becomes a row with its first exchange.
- An explicit, owned session id always resumes that session, however old,
  with only its own history (``session_resumed`` audited when idle).
- Unknown or foreign ids rotate (``not_found``) without leaking existence.
- ``chat.prune_empty_sessions`` deletes only empty, old sessions, in batches.

Unit tests use a mocked database; the DB-backed ones run in CI.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect

from app.core import sessions
from app.core.config import get_settings
from app.governance import audit
from app.harness import prompts
from app.jobs import handlers
from app.jobs.registry import CHAT_PRUNE_EMPTY_SESSIONS_JOB, get_handler
from app.models.db_models import ChatMessageRecord, ChatSession
from app.schemas.query import ChatTurn, QueryRequest
from main import app
from tests.conftest import shared_session_factory

USER = "lifecycle@example.com"
OTHER = "someone-else@example.com"
IP = "10.0.0.9"


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    rows: list[dict] = []

    async def _sink(batch: list[dict]) -> None:
        rows.extend(batch)

    monkeypatch.setattr(audit, "_batch_writer", _sink)
    audit._queue.clear()
    return rows


def _session(email: str, *, idle: timedelta, ip: str = IP) -> ChatSession:
    now = datetime.utcnow()
    last = now - idle
    return ChatSession(
        id=uuid.uuid4(),
        user_email=email,
        title="t",
        created_at=last - timedelta(minutes=5),
        last_activity_at=last,
        expires_at=last + timedelta(minutes=get_settings().session_timeout_minutes),
        created_ip=ip,
    )


def _fake_db(get_result: ChatSession | None, latest: ChatSession | None = None):
    db = MagicMock()
    db.get = AsyncMock(return_value=get_result)
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=latest)
    db.execute = AsyncMock(return_value=result)
    return db


# --------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------


def test_new_session_with_session_id_is_rejected():
    with pytest.raises(ValidationError, match="new_session"):
        QueryRequest(query="q", new_session=True, session_id=str(uuid.uuid4()))


def test_new_session_alone_and_empty_session_id_are_valid():
    assert QueryRequest(query="q", new_session=True).new_session is True
    assert QueryRequest(query="q", new_session=True, session_id="").new_session
    assert QueryRequest(query="q").new_session is False


def test_openapi_documents_new_session():
    schema = app.openapi()["components"]["schemas"]["QueryRequest"]
    assert schema["properties"]["new_session"]["default"] is False
    assert "422" in schema["properties"]["new_session"]["description"]
    summary = app.openapi()["components"]["schemas"]["ChatSessionSummary"]
    assert "does not block" in summary["properties"]["is_expired"]["description"]
    create = app.openapi()["paths"]["/sessions"]["post"]
    assert create.get("deprecated") is True


# --------------------------------------------------------------------------
# resolve_session_for_query (mocked DB)
# --------------------------------------------------------------------------


async def test_new_session_never_resumes_the_recent_same_ip_session(captured):
    recent = _session(USER, idle=timedelta(minutes=1))
    db = _fake_db(None, latest=recent)
    got = await sessions.resolve_session_for_query(db, USER, None, IP, new_session=True)
    assert got is not recent
    assert sa_inspect(got).transient  # no row until the first exchange
    assert got.user_email == USER
    assert got.title == "New chat"
    db.execute.assert_not_called()
    await audit.flush_audit_events()
    assert captured == []


async def test_no_id_still_resumes_recent_same_ip_session():
    recent = _session(USER, idle=timedelta(minutes=1))
    got = await sessions.resolve_session_for_query(
        _fake_db(None, latest=recent), USER, None, IP
    )
    assert got is recent


async def test_explicit_old_session_resumes_with_session_resumed(captured):
    old = _session(USER, idle=timedelta(days=3))
    got = await sessions.resolve_session_for_query(
        _fake_db(old), USER, str(old.id), "10.9.9.9"
    )
    assert got is old
    await audit.flush_audit_events()
    assert [r["action"] for r in captured] == ["session_resumed"]
    assert captured[0]["detail"]["session_id"] == str(old.id)
    assert captured[0]["detail"]["idle_minutes"] >= 3 * 24 * 60


async def test_explicit_live_session_reuses_without_audit(captured):
    live = _session(USER, idle=timedelta(minutes=2))
    got = await sessions.resolve_session_for_query(_fake_db(live), USER, str(live.id))
    assert got is live
    await audit.flush_audit_events()
    assert captured == []


async def test_foreign_session_id_rotates_as_not_found(captured):
    foreign = _session(OTHER, idle=timedelta(minutes=1))
    got = await sessions.resolve_session_for_query(
        _fake_db(foreign), USER, str(foreign.id), IP
    )
    assert got is not foreign
    assert got.user_email == USER
    await audit.flush_audit_events()
    assert [r["action"] for r in captured] == ["session_rotated"]
    assert captured[0]["detail"]["reason"] == "not_found"


# --------------------------------------------------------------------------
# History scope and budget (no DB)
# --------------------------------------------------------------------------


def test_history_block_respects_token_budget(monkeypatch):
    monkeypatch.setattr(prompts.settings, "llm_history_max_tokens", 60)
    turns = [
        ChatTurn(role="user" if i % 2 == 0 else "assistant", content=f"turn {i} " * 30)
        for i in range(20)
    ]
    block = prompts._format_history("and what about that?", turns)
    assert len(prompts._TOKEN_ENCODING.encode(block)) <= 60


# --------------------------------------------------------------------------
# Prune job registration
# --------------------------------------------------------------------------


def test_prune_job_is_registered():
    assert get_handler(CHAT_PRUNE_EMPTY_SESSIONS_JOB) is (
        handlers.prune_empty_sessions_task
    )


# --------------------------------------------------------------------------
# DB-backed (run in CI)
# --------------------------------------------------------------------------


async def _count_sessions(db, email: str) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(ChatSession)
            .where(ChatSession.user_email == email)
        )
    ).scalar_one()


def _message(session: ChatSession, idx: int, at: datetime, role: str):
    return ChatMessageRecord(
        session_id=session.id,
        turn_index=idx // 2,
        role=role,
        content=f"{session.title} message {idx}",
        created_at=at,
    )


async def test_db_lazy_new_chat_creates_one_row_on_first_message(db_session):
    email = f"lazy-{uuid.uuid4().hex[:8]}@example.com"
    for _ in range(3):  # three "New chat" clicks resolved, never written
        await sessions.resolve_session_for_query(
            db_session, email, None, IP, new_session=True
        )
    assert await _count_sessions(db_session, email) == 0

    fresh = await sessions.resolve_session_for_query(
        db_session, email, None, IP, new_session=True
    )
    db_session.add(fresh)
    await sessions.record_exchange(
        db_session,
        fresh,
        user_text="How do I reset my password?",
        answer_text="Use the reset link.",
        request_id="r-lazy",
        grounded=True,
        guardrail_flags=[],
        commit=False,
    )
    assert await _count_sessions(db_session, email) == 1
    assert fresh.title == "How do I reset my password?"


async def test_db_new_session_does_not_append_to_previous_chat(db_session):
    email = f"fresh-{uuid.uuid4().hex[:8]}@example.com"
    previous = _session(email, idle=timedelta(minutes=1))
    db_session.add(previous)
    await db_session.flush()
    implicit = await sessions.resolve_session_for_query(db_session, email, None, IP)
    assert implicit.id == previous.id
    fresh = await sessions.resolve_session_for_query(
        db_session, email, None, IP, new_session=True
    )
    assert fresh.id != previous.id


async def test_db_resume_old_session_keeps_id_and_extends_expiry(db_session):
    email = f"resume-{uuid.uuid4().hex[:8]}@example.com"
    old = _session(email, idle=timedelta(days=2))
    db_session.add(old)
    await db_session.flush()
    before = old.expires_at

    got = await sessions.resolve_session_for_query(
        db_session, email, str(old.id), "10.1.1.1"
    )
    assert got.id == old.id
    await sessions.record_exchange(
        db_session,
        got,
        user_text="follow up",
        answer_text="answer",
        request_id="r-resume",
        grounded=True,
        guardrail_flags=[],
        commit=False,
    )
    assert got.expires_at > before
    assert got.expires_at > datetime.utcnow()
    assert await _count_sessions(db_session, email) == 1


async def test_db_two_sessions_of_one_user_never_mix_history(db_session):
    email = f"mix-{uuid.uuid4().hex[:8]}@example.com"
    a = _session(email, idle=timedelta(days=1))
    a.title = "A"
    b = _session(email, idle=timedelta(minutes=5))
    b.title = "B"
    db_session.add_all([a, b])
    await db_session.flush()
    start = datetime.utcnow() - timedelta(hours=1)
    cap = get_settings().session_history_max_messages
    total = cap + 6
    # Interleaved timestamps: A and B alternate, second by second.
    for i in range(total):
        role = "user" if i % 2 == 0 else "assistant"
        db_session.add(_message(a, i, start + timedelta(seconds=2 * i), role))
        db_session.add(_message(b, i, start + timedelta(seconds=2 * i + 1), role))
    await db_session.flush()

    history_a = await sessions.load_recent_history(db_session, a)
    history_b = await sessions.load_recent_history(db_session, b)
    assert len(history_a) == cap
    assert len(history_b) == cap
    assert all(t.content.startswith("A message") for t in history_a)
    assert all(t.content.startswith("B message") for t in history_b)
    # The newest turns of that session, oldest first.
    assert history_a[-1].content == f"A message {total - 1}"
    assert history_a[0].content == f"A message {total - cap}"


async def test_db_prune_deletes_only_old_empty_sessions_in_batches(db_session):
    email = f"prune-{uuid.uuid4().hex[:8]}@example.com"
    now = datetime.utcnow()

    def make(age: timedelta, owner: str = email) -> ChatSession:
        created = now - age
        return ChatSession(
            id=uuid.uuid4(),
            user_email=owner,
            title="New chat",
            created_at=created,
            last_activity_at=created,
            expires_at=created + timedelta(hours=1),
        )

    old_empty = [make(timedelta(days=d)) for d in (1, 2, 3)]
    other_old_empty = make(timedelta(days=4), owner=OTHER)
    recent_empty = make(timedelta(minutes=10))
    old_with_message = make(timedelta(days=5))
    db_session.add_all([*old_empty, other_old_empty, recent_empty, old_with_message])
    await db_session.flush()
    db_session.add(_message(old_with_message, 0, now - timedelta(days=5), "user"))
    await db_session.flush()
    keep_ids = {recent_empty.id, old_with_message.id}
    gone_ids = {s.id for s in old_empty} | {other_old_empty.id}

    factory = shared_session_factory(db_session)
    deleted = await sessions.prune_empty_sessions(
        batch_size=1, now=now, session_factory=factory
    )
    assert deleted >= len(gone_ids)  # other tests' leftovers may be pruned too
    remaining = set(
        (
            await db_session.execute(
                select(ChatSession.id).where(ChatSession.id.in_(keep_ids | gone_ids))
            )
        )
        .scalars()
        .all()
    )
    assert remaining == keep_ids

    again = await sessions.prune_empty_sessions(
        batch_size=1, now=now, session_factory=factory
    )
    assert again == 0  # idempotent
