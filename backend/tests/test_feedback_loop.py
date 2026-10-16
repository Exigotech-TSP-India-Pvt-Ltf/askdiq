"""Feedback / RLHF loop: unit and API tests (planchanges4 B5.1, B5.2)."""

from __future__ import annotations

import asyncio
import functools
import gc
import importlib.util
import json
import tracemalloc
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import psutil
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, event, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import authenticate_session
from app.auth.permissions import Role, UserStatus
from app.auth.principal import AuthUser
from app.core.config import get_settings
from app.core.rate_limit import _feedback_ip_log, _feedback_user_log
from app.feedback import repository as repo
from app.feedback import service
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    EvalPromotedCase,
    EvaluationRecord,
    MessageFeedback,
)
from app.schemas.feedback import (
    MAX_BULK_IDS,
    FeedbackBulkRequest,
    FeedbackSort,
    FeedbackTriageRequest,
    TriageStatus,
)
from app.schemas.session import UserFeedbackRequest
from main import app
from tests.conftest import shared_session_factory
from tests.feedback_helpers import make_exchange, make_feedback, make_session, marker
from tests.pg_helpers import new_engine, require_postgres

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "5712ebd9d3c9_add_feedback_triage_and_promoted_cases.py"
)


@pytest.fixture(autouse=True)
def _reset_feedback_rate_limit():
    _feedback_user_log.clear()
    _feedback_ip_log.clear()


# ---------------------------------------------------------------------------
# Pure unit tests (no database)
# ---------------------------------------------------------------------------


def test_reason_tags_are_allow_listed_and_deduped():
    req = UserFeedbackRequest(rating=1, reason_tags=["wrong", "wrong", "unclear"])
    assert [t.value for t in req.reason_tags] == ["wrong", "unclear"]
    with pytest.raises(ValidationError):
        UserFeedbackRequest(rating=1, reason_tags=["made_up"])


def test_corrected_answer_is_bounded_and_cleaned():
    req = UserFeedbackRequest(rating=1, corrected_answer="  better\x00 answer  ")
    assert req.corrected_answer == "better answer"
    UserFeedbackRequest(rating=1, corrected_answer="x" * 4000)
    with pytest.raises(ValidationError):
        UserFeedbackRequest(rating=1, corrected_answer="x" * 4001)


def test_bulk_request_limits():
    ids = [uuid.uuid4() for _ in range(MAX_BULK_IDS)]
    assert len(FeedbackBulkRequest(ids=ids, action="reviewed").ids) == MAX_BULK_IDS
    with pytest.raises(ValidationError):
        FeedbackBulkRequest(ids=[*ids, uuid.uuid4()], action="reviewed")
    with pytest.raises(ValidationError):
        FeedbackBulkRequest(ids=[], action="reviewed")
    with pytest.raises(ValidationError):
        FeedbackBulkRequest(ids=ids[:1], action="new")
    dup = FeedbackBulkRequest(ids=[ids[0], ids[0]], action="dismissed")
    assert dup.ids == [ids[0]]


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        ("new", TriageStatus.REVIEWED, True),
        ("new", TriageStatus.PROMOTED, True),
        ("new", TriageStatus.DISMISSED, True),
        ("new", TriageStatus.NEW, False),
        ("reviewed", TriageStatus.PROMOTED, True),
        ("reviewed", TriageStatus.DISMISSED, True),
        ("reviewed", TriageStatus.NEW, False),
        ("reviewed", TriageStatus.REVIEWED, False),
        ("dismissed", TriageStatus.NEW, True),
        ("dismissed", TriageStatus.PROMOTED, False),
        ("dismissed", TriageStatus.REVIEWED, False),
        ("promoted", TriageStatus.NEW, False),
        ("promoted", TriageStatus.DISMISSED, False),
    ],
)
def test_triage_state_machine(current, target, allowed):
    assert service.can_transition(current, target) is allowed


def test_cursor_round_trip_and_rejection():
    row = SimpleNamespace(rating=2, created_at=datetime(2026, 1, 2, 3), id=uuid.uuid4())
    for sort in FeedbackSort:
        cursor = repo.encode_cursor(sort, row)
        values = repo.decode_cursor(sort, cursor)
        assert values[-1] == row.id
    cursor = repo.encode_cursor(FeedbackSort.RATING_ASC, row)
    with pytest.raises(repo.InvalidCursorError):
        repo.decode_cursor(FeedbackSort.CREATED_AT_DESC, cursor)
    for bad in ("not-base64!!", "e30", "' OR 1=1 --"):
        with pytest.raises(repo.InvalidCursorError):
            repo.decode_cursor(FeedbackSort.CREATED_AT_DESC, bad)


def _pair_row(**kw):
    base = {
        "id": uuid.uuid4(),
        "message_id": uuid.uuid4(),
        "rating": 1,
        "reason_tags": ["wrong"],
        "corrected_answer": None,
        "triage_status": "new",
        "prompt": "What is X?",
        "answer": "X is Y.",
        "retrieved_chunks": [{"chunk_id": "c1"}, {"chunk_id": ""}, "junk"],
        "created_at": datetime(2026, 10, 14, 9, 30),
    }
    return SimpleNamespace(**{**base, **kw})


def test_build_pair_shapes():
    full = service.build_pair(_pair_row(corrected_answer="X is Z."))
    assert full["chosen"] == "X is Z."
    assert full["rejected"] == "X is Y."
    assert full["complete"] is True
    assert full["source_ids"] == ["c1"]

    neg = service.build_pair(_pair_row())
    assert neg["chosen"] is None
    assert neg["complete"] is False
    assert neg["missing"] == ["chosen"]

    pos = service.build_pair(_pair_row(rating=5))
    assert pos["chosen"] == "X is Y."
    assert pos["missing"] == ["rejected"]

    masked = service.build_pair(_pair_row(prompt="mail me at jane.doe@example.com"))
    assert "jane.doe@example.com" not in masked["prompt"]


def test_build_pair_carries_feedback_timestamp():
    assert service.build_pair(_pair_row())["created_at"] == "2026-10-14T09:30:00"
    assert service.build_pair(_pair_row(created_at=None))["created_at"] is None


# ---------------------------------------------------------------------------
# RBAC (guards run before any database access)
# ---------------------------------------------------------------------------


def _as(role: Role) -> None:
    async def _user() -> AuthUser:
        return AuthUser(
            id=uuid.uuid4(),
            email=f"{role.value}@example.com",
            name=role.value,
            role=role,
            status=UserStatus.ACTIVE,
            version=1,
        )

    app.dependency_overrides[authenticate_session] = _user


async def _raw(method: str, path: str, **kw) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        return await c.request(
            method, path, headers={"X-API-Key": get_settings().api_key_name}, **kw
        )


async def test_operator_gets_403_on_triage_bulk_promote_run_and_export():
    _as(Role.OPERATOR)
    fid = uuid.uuid4()
    calls = [
        ("PATCH", f"/admin/feedback/{fid}", {"json": {"version": 1}}),
        (
            "POST",
            "/admin/feedback/bulk",
            {"json": {"ids": [str(fid)], "action": "reviewed"}},
        ),
        ("POST", f"/admin/feedback/{fid}/promote-to-test", {}),
        ("POST", "/admin/feedback/golden/promoted_x/run", {}),
        ("GET", "/admin/feedback/export?format=jsonl", {}),
    ]
    for method, path, kw in calls:
        response = await _raw(method, path, **kw)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"] == "Insufficient permissions."


async def test_feedback_routes_reject_bad_input_with_422():
    _as(Role.ADMIN)
    bad = [
        ("GET", "/admin/feedback?sort=rating; DROP TABLE x"),
        ("GET", "/admin/feedback?limit=0"),
        ("GET", "/admin/feedback?limit=201"),
        ("GET", "/admin/feedback?offset=-1"),
        ("GET", "/admin/feedback?triage_status=bogus"),
        ("GET", "/admin/feedback?outcome=bogus"),
        ("GET", "/admin/feedback?rating=9"),
        ("GET", "/admin/feedback?message_id=not-a-uuid"),
        ("GET", "/admin/feedback/export?format=csv"),
        ("GET", "/admin/feedback/analytics?audience=everyone"),
    ]
    for method, path in bad:
        response = await _raw(method, path)
        assert response.status_code == 422, (path, response.text)


# ---------------------------------------------------------------------------
# B5.1 migration data step and dataset_extra
# ---------------------------------------------------------------------------


def _load_migration():
    spec = importlib.util.spec_from_file_location("feedback_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_import_handles_missing_and_bad_files(tmp_path):
    module = _load_migration()
    assert module.import_promoted_cases(None, tmp_path / "missing.json") == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert module.import_promoted_cases(None, bad) == 0


async def test_migration_import_is_idempotent(db_session, tmp_path):
    module = _load_migration()
    ids = [f"promoted_test_{uuid.uuid4().hex[:12]}" for _ in range(2)]
    path = tmp_path / "promoted.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": ids[0],
                    "query": "Legacy question one?",
                    "case_type": "negative",
                    "expect_grounded": False,
                    "source_message_id": str(uuid.uuid4()),
                    "promoted_at": "2026-10-15T04:24:37.161833Z",
                    "promoted_by": "legacy@example.com",
                },
                {"id": ids[1], "query": "Legacy question two?"},
                {"query": "no id, skipped"},
            ]
        ),
        encoding="utf-8",
    )

    def _run(sync_session):
        return module.import_promoted_cases(sync_session.connection(), path)

    assert await db_session.run_sync(_run) == 2
    assert await db_session.run_sync(_run) == 2
    count = await db_session.scalar(
        select(func.count())
        .select_from(EvalPromotedCase)
        .where(EvalPromotedCase.id.in_(ids))
    )
    assert count == 2
    first = await db_session.get(EvalPromotedCase, ids[0])
    assert first.expect_grounded is False
    assert first.case_type == "negative"


async def test_dataset_extra_reads_db_with_json_fallback(db_session, tmp_path):
    from app.evaluation.dataset import EvalCase
    from app.evaluation.dataset_extra import load_promoted_cases, merge_cases

    case_id = f"promoted_db_{uuid.uuid4().hex[:12]}"
    db_session.add(
        EvalPromotedCase(
            id=case_id,
            query="From the database?",
            case_type="negative",
            expect_grounded=False,
            promoted_by="admin@example.com",
        )
    )
    await db_session.flush()
    json_path = tmp_path / "promoted.json"
    json_path.write_text(
        json.dumps([{"id": "promoted_json_only", "query": "From JSON?"}]),
        encoding="utf-8",
    )

    from_db = await load_promoted_cases(EvalCase, db_session, json_path)
    assert case_id in {c.id for c in from_db}
    assert "promoted_json_only" not in {c.id for c in from_db}
    picked = next(c for c in from_db if c.id == case_id)
    assert picked.expect_grounded is False

    class _BrokenDb:
        async def execute(self, *_a, **_k):
            raise RuntimeError("database down")

    fallback = await load_promoted_cases(EvalCase, _BrokenDb(), json_path)
    assert [c.id for c in fallback] == ["promoted_json_only"]

    merged = merge_cases(from_db, fallback + from_db)
    assert len(merged) == len(from_db) + 1


# ---------------------------------------------------------------------------
# B5.2.2 chat-side upsert
# ---------------------------------------------------------------------------


async def test_chat_feedback_upserts_masks_pii_and_rejects_unknown_tags(
    client, db_session
):
    actor = f"chat-{uuid.uuid4().hex[:8]}@example.com"
    client.headers["X-Client-Id"] = actor
    session = await make_session(db_session, actor)
    _u, asst = await make_exchange(db_session, session, turn=1, question="Q?")
    url = f"/sessions/{session.id}/messages/{asst.id}/feedback"

    first = await client.post(
        url,
        json={
            "rating": 1,
            "comment": "Wrong",
            "reason_tags": ["wrong", "incomplete"],
            "corrected_answer": "Call 0412 345 678 or email jane.doe@example.com.",
        },
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["reason_tags"] == ["wrong", "incomplete"]
    assert body["triage_status"] == "new"
    assert "jane.doe@example.com" not in body["corrected_answer"]
    assert "0412 345 678" not in body["corrected_answer"]
    assert "[REDACTED_EMAIL]" in body["corrected_answer"]

    second = await client.post(url, json={"rating": 5, "reason_tags": []})
    assert second.status_code == 200
    rows = (
        await db_session.execute(
            select(
                MessageFeedback.rating,
                MessageFeedback.reason_tags,
                MessageFeedback.corrected_answer,
                MessageFeedback.version,
            ).where(MessageFeedback.message_id == asst.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].rating == 5
    assert rows[0].reason_tags == []
    assert rows[0].corrected_answer is None
    assert rows[0].version == 2

    unknown = await client.post(url, json={"rating": 1, "reason_tags": ["evil"]})
    assert unknown.status_code == 422
    too_long = await client.post(
        url, json={"rating": 1, "corrected_answer": "x" * 4001}
    )
    assert too_long.status_code == 422


# ---------------------------------------------------------------------------
# B5.2.3 enriched list
# ---------------------------------------------------------------------------


async def test_list_is_enriched_and_uses_at_most_two_queries_for_50_rows(
    client, db_session
):
    mk = marker()
    session = await make_session(db_session, "owner@example.com")
    for turn in range(50):
        _u, asst = await make_exchange(
            db_session,
            session,
            turn=turn,
            question=f"{mk} question {turn}",
            sources=[{"source_name": "doc.md", "chunk_id": "c-1", "score": 0.8}],
            confidence=0.7,
            latency_ms=1234.0,
        )
        await make_feedback(
            db_session, asst, actor=f"rater{turn}@example.com", rating=1 + turn % 5
        )

    statements: list[str] = []
    sync_conn = db_session.bind.sync_connection

    def _count(conn, cursor, statement, *args):
        # SAVEPOINTs come from the per-test transaction, not the endpoint.
        if not statement.lstrip().upper().startswith(("SAVEPOINT", "RELEASE")):
            statements.append(statement)

    event.listen(sync_conn, "before_cursor_execute", _count)
    try:
        response = await client.get(f"/admin/feedback?q={mk}&limit=50")
    finally:
        event.remove(sync_conn, "before_cursor_execute", _count)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 50
    assert len(data["items"]) == 50
    assert len(statements) <= 2, statements
    item = data["items"][0]
    for key in (
        "id",
        "rating",
        "comment",
        "ip_address",
        "conversation_id",
        "message_id",
        "created_at",
    ):
        assert key in item  # legacy fields kept for the current UI
    assert item["user_query"].startswith(mk)
    assert item["answer"] == "An answer from the knowledge base."
    assert item["outcome"] == "answered"
    assert item["grounded"] is True
    assert item["sources"][0]["chunk_id"] == "c-1"
    assert item["latency_ms"] == 1234.0
    assert item["cost_usd"] == pytest.approx(0.0012)
    assert item["actor"].startswith("rater")
    assert item["triage_status"] == "new"
    assert item["promoted_case_id"] is None
    assert item["version"] == 1


async def test_each_list_filter(client, db_session):
    mk = marker()
    session = await make_session(db_session, "owner@example.com")
    old = datetime.utcnow() - timedelta(days=40)
    specs = [
        # rating, status, outcome, grounded, comment, created_at
        (1, "new", "ungrounded", False, "bad", None),
        (5, "reviewed", "answered", True, None, None),
        (2, "dismissed", "answered", True, "meh", old),
        (4, "promoted", "blocked", None, None, None),
    ]
    ids = []
    for turn, (rating, st, outcome, grounded, comment, created) in enumerate(specs):
        _u, asst = await make_exchange(
            db_session,
            session,
            turn=turn,
            question=f"{mk} q{turn}",
            outcome=outcome,
            grounded=grounded,
        )
        row = await make_feedback(
            db_session,
            asst,
            actor=f"f{turn}@example.com",
            rating=rating,
            comment=comment,
            triage_status=st,
            created_at=created,
        )
        ids.append((row.id, asst.id))

    async def totals(query: str) -> int:
        r = await client.get(f"/admin/feedback?q={mk}&{query}")
        assert r.status_code == 200, r.text
        return r.json()["total"]

    assert await totals("") == 4
    assert await totals("rating=1") == 1
    assert await totals("triage_status=reviewed") == 1
    assert await totals("triage_status=promoted") == 1
    assert await totals("outcome=answered") == 2
    assert await totals("outcome=ungrounded") == 1
    assert await totals("grounded=true") == 2
    assert await totals("grounded=false") == 1
    assert await totals("has_comment=true") == 2
    assert await totals("has_comment=false") == 2
    since = (datetime.utcnow() - timedelta(days=1)).isoformat()
    assert await totals(f"date_from={since}") == 3
    assert await totals(f"date_to={since}") == 1
    assert await totals(f"message_id={ids[1][1]}") == 1
    bad_range = await client.get(
        f"/admin/feedback?date_from={since}&date_to=2000-01-01T00:00:00"
    )
    assert bad_range.status_code == 422
    # LIKE metacharacters are escaped, not treated as wildcards.
    r = await client.get("/admin/feedback?q=%25%25%25_")
    assert r.status_code == 200
    assert all("%%%_" in (i["user_query"] or "") for i in r.json()["items"])


async def test_keyset_pagination_walks_every_row_once(client, db_session):
    mk = marker()
    session = await make_session(db_session, "owner@example.com")
    base = datetime.utcnow()
    for turn in range(7):
        _u, asst = await make_exchange(
            db_session, session, turn=turn, question=f"{mk} {turn}"
        )
        await make_feedback(
            db_session,
            asst,
            actor=f"k{turn}@example.com",
            rating=1 + turn % 3,
            created_at=base - timedelta(minutes=turn % 2),  # ties on purpose
        )
    for sort in ("created_at_desc", "created_at_asc", "rating_desc", "rating_asc"):
        seen: list[str] = []
        cursor = None
        for _ in range(10):
            qs = f"q={mk}&sort={sort}&limit=3" + (f"&cursor={cursor}" if cursor else "")
            r = await client.get(f"/admin/feedback?{qs}")
            assert r.status_code == 200, r.text
            page = r.json()
            assert page["total"] == 7
            seen.extend(i["id"] for i in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == 7, sort
        assert len(set(seen)) == 7, sort
    bad = await client.get("/admin/feedback?cursor=garbage")
    assert bad.status_code == 422


# ---------------------------------------------------------------------------
# B5.2.1 triage state machine
# ---------------------------------------------------------------------------


async def test_triage_transitions_versions_and_audit(client, db_session):
    session = await make_session(db_session, "owner@example.com")
    _u, asst = await make_exchange(db_session, session, turn=1, question="T?")
    row = await make_feedback(db_session, asst, actor="t@example.com", rating=2)
    url = f"/admin/feedback/{row.id}"

    ok = await client.patch(
        url, json={"version": 1, "triage_status": "reviewed", "review_note": "seen"}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["triage_status"] == "reviewed"
    assert ok.json()["version"] == 2
    assert ok.json()["review_note"] == "seen"
    assert ok.json()["reviewed_by"]

    stale = await client.patch(url, json={"version": 1, "triage_status": "dismissed"})
    assert stale.status_code == 409

    invalid = await client.patch(url, json={"version": 2, "triage_status": "new"})
    assert invalid.status_code == 409
    assert "Invalid transition" in invalid.json()["detail"]

    dismissed = await client.patch(
        url, json={"version": 2, "triage_status": "dismissed"}
    )
    assert dismissed.status_code == 200
    reopened = await client.patch(url, json={"version": 3, "triage_status": "new"})
    assert reopened.status_code == 200
    assert reopened.json()["reviewed_by"] is None

    empty = await client.patch(url, json={"version": 4})
    assert empty.status_code == 422
    missing = await client.patch(
        f"/admin/feedback/{uuid.uuid4()}",
        json={"version": 1, "triage_status": "reviewed"},
    )
    assert missing.status_code == 404

    audits = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == "feedback_triaged",
            AuditLog.detail["feedback_id"].as_string() == str(row.id),
        )
    )
    assert audits == 3


async def test_promote_via_patch_creates_golden_case(client, db_session):
    session = await make_session(db_session, "owner@example.com")
    _u, asst = await make_exchange(
        db_session,
        session,
        turn=1,
        question="Promote me?",
        grounded=False,
        outcome="ungrounded",
    )
    row = await make_feedback(
        db_session,
        asst,
        actor="p@example.com",
        rating=1,
        reason_tags=["wrong"],
        corrected_answer="The right answer.",
    )
    r = await client.patch(
        f"/admin/feedback/{row.id}", json={"version": 1, "triage_status": "promoted"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["triage_status"] == "promoted"
    case = await db_session.get(EvalPromotedCase, body["promoted_case_id"])
    assert case.query == "Promote me?"
    assert case.reference_answer == "The right answer."
    assert case.expect_grounded is False
    assert case.reason_tags == ["wrong"]

    again = await client.patch(
        f"/admin/feedback/{row.id}",
        json={"version": body["version"], "triage_status": "dismissed"},
    )
    assert again.status_code == 409  # promoted is terminal


async def test_concurrent_triage_one_wins_one_conflicts():
    engine = new_engine()
    await require_postgres(engine)
    email = f"race-{uuid.uuid4().hex[:8]}@example.com"
    session_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            session = await make_session(db, email)
            _u, asst = await make_exchange(db, session, turn=1, question="Race?")
            row = await make_feedback(db, asst, actor=email, rating=1)
            await db.commit()
            feedback_id, session_id = row.id, session.id

        async def attempt(target: str):
            async with AsyncSession(engine, expire_on_commit=False) as db:
                try:
                    await service.triage(
                        db,
                        feedback_id,
                        FeedbackTriageRequest(version=1, triage_status=target),
                        "racer@example.com",
                    )
                except HTTPException as exc:
                    return exc.status_code
                return 200

        results = await asyncio.gather(attempt("reviewed"), attempt("dismissed"))
        assert sorted(results) == [200, 409]
        async with engine.connect() as conn:
            version = await conn.scalar(
                select(MessageFeedback.version).where(MessageFeedback.id == feedback_id)
            )
        assert version == 2
    finally:
        if session_id is not None:
            async with engine.begin() as conn:
                await conn.execute(
                    delete(ChatSession).where(ChatSession.id == session_id)
                )
        await engine.dispose()


# ---------------------------------------------------------------------------
# B5.2.5 bulk, promotion, export, golden run
# ---------------------------------------------------------------------------


async def test_bulk_is_all_or_nothing(client, db_session):
    session = await make_session(db_session, "owner@example.com")
    rows = []
    for turn, st in enumerate(["new", "new", "promoted"]):
        _u, asst = await make_exchange(
            db_session, session, turn=turn, question=f"B{turn}"
        )
        rows.append(
            await make_feedback(
                db_session,
                asst,
                actor=f"b{turn}@example.com",
                rating=2,
                triage_status=st,
            )
        )
    # Plain ids: a service rollback expires the ORM objects.
    row_ids = [r.id for r in rows]
    ids = [str(i) for i in row_ids]

    async def statuses() -> list[str]:
        res = await db_session.execute(
            select(MessageFeedback.id, MessageFeedback.triage_status).where(
                MessageFeedback.id.in_(row_ids)
            )
        )
        by_id = dict(res.all())
        return [by_id[i] for i in row_ids]

    conflict = await client.post(
        "/admin/feedback/bulk", json={"ids": ids, "action": "dismissed"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["ids"] == [ids[2]]
    assert await statuses() == ["new", "new", "promoted"]

    missing = await client.post(
        "/admin/feedback/bulk",
        json={"ids": [*ids[:2], str(uuid.uuid4())], "action": "reviewed"},
    )
    assert missing.status_code == 404
    assert await statuses() == ["new", "new", "promoted"]

    too_many = await client.post(
        "/admin/feedback/bulk",
        json={"ids": [str(uuid.uuid4()) for _ in range(201)], "action": "reviewed"},
    )
    assert too_many.status_code == 422

    ok = await client.post(
        "/admin/feedback/bulk",
        json={"ids": ids[:2], "action": "reviewed", "review_note": "batch"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["updated"] == 2
    assert await statuses() == ["reviewed", "reviewed", "promoted"]

    promoted = await client.post(
        "/admin/feedback/bulk", json={"ids": ids[:2], "action": "promoted"}
    )
    assert promoted.status_code == 200, promoted.text
    assert len(promoted.json()["promoted_case_ids"]) == 2
    assert await statuses() == ["promoted", "promoted", "promoted"]


async def test_legacy_promote_route_writes_db_and_is_idempotent(client, db_session):
    session = await make_session(db_session, "owner@example.com")
    _u, asst = await make_exchange(db_session, session, turn=1, question="Legacy?")
    await make_feedback(db_session, asst, actor="l1@example.com", rating=1)
    await make_feedback(db_session, asst, actor="l2@example.com", rating=2)

    first = await client.post(f"/admin/feedback/{asst.id}/promote-to-test")
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["status"] == "promoted"
    assert data["query"] == "Legacy?"
    assert data["created"] is True
    assert len(data["feedback_ids"]) == 2

    second = await client.post(f"/admin/feedback/{asst.id}/promote-to-test")
    assert second.json()["case_id"] == data["case_id"]
    assert second.json()["created"] is False

    linked = await db_session.scalar(
        select(func.count())
        .select_from(MessageFeedback)
        .where(
            MessageFeedback.message_id == asst.id,
            MessageFeedback.promoted_case_id == data["case_id"],
            MessageFeedback.triage_status == "promoted",
        )
    )
    assert linked == 2

    golden = await client.get("/admin/feedback/golden?limit=200")
    assert golden.status_code == 200
    assert data["case_id"] in {c["id"] for c in golden.json()["items"]}

    assert (
        await client.post("/admin/feedback/nope/promote-to-test")
    ).status_code == 400
    assert (
        await client.post(f"/admin/feedback/{uuid.uuid4()}/promote-to-test")
    ).status_code == 404


async def test_export_jsonl_api(client, db_session):
    session = await make_session(db_session, "owner@example.com")
    _u, asst = await make_exchange(
        db_session,
        session,
        turn=1,
        question="Export me?",
        answer="Wrong answer.",
        sources=[{"chunk_id": "chunk-9"}],
    )
    row = await make_feedback(
        db_session,
        asst,
        actor="e@example.com",
        rating=1,
        reason_tags=["wrong"],
        corrected_answer="Right answer.",
    )
    r = await client.get("/admin/feedback/export?format=jsonl&complete_only=true")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in r.headers["content-disposition"]
    pairs = [json.loads(line) for line in r.text.splitlines() if line]
    assert all(p["complete"] for p in pairs)
    mine = next(p for p in pairs if p["feedback_id"] == str(row.id))
    assert datetime.fromisoformat(mine.pop("created_at"))
    assert mine == {
        "feedback_id": str(row.id),
        "message_id": str(asst.id),
        "prompt": "Export me?",
        "chosen": "Right answer.",
        "rejected": "Wrong answer.",
        "rating": 1,
        "reason_tags": ["wrong"],
        "source_ids": ["chunk-9"],
        "triage_status": "new",
        "complete": True,
        "missing": [],
    }
    audited = await db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "feedback_exported")
    )
    assert audited >= 1


async def test_export_streams_10k_pairs_with_flat_memory(db_session):
    session = await make_session(db_session, "bulk-owner@example.com")
    n = 10_000
    user_rows, asst_rows, fb_rows = [], [], []
    now = datetime.utcnow()
    for i in range(n):
        asst_id = uuid.uuid4()
        user_rows.append(
            {
                "id": uuid.uuid4(),
                "session_id": session.id,
                "turn_index": i,
                "role": "user",
                "content": f"Question number {i} about the platform?",
                "created_at": now,
            }
        )
        asst_rows.append(
            {
                "id": asst_id,
                "session_id": session.id,
                "turn_index": i,
                "role": "assistant",
                "content": f"Answer number {i}. " * 10,
                "created_at": now,
            }
        )
        fb_rows.append(
            {
                "id": uuid.uuid4(),
                "message_id": asst_id,
                "session_id": session.id,
                "actor": "bulk@example.com",
                "rating": 1,
                "comment": None,
                "reason_tags": ["wrong"],
                "corrected_answer": f"Corrected {i}.",
                "created_at": now,
                "updated_at": now,
            }
        )
    await db_session.execute(insert(ChatMessageRecord), user_rows + asst_rows)
    await db_session.execute(insert(MessageFeedback), fb_rows)
    del user_rows, asst_rows, fb_rows
    gc.collect()

    process = psutil.Process()
    rss_before = process.memory_info().rss
    tracemalloc.start()
    lines = 0
    try:
        async for line in service.export_pairs_jsonl(
            db_session,
            triage_status=None,
            include_dismissed=False,
            complete_only=False,
            batch_size=500,
        ):
            assert line.endswith("\n")
            lines += 1
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    rss_growth = process.memory_info().rss - rss_before

    assert lines >= n
    assert peak < 20 * 1024 * 1024, peak
    assert rss_growth < 20 * 1024 * 1024, rss_growth


async def test_golden_run_is_queued_once(client, db_session, monkeypatch):
    from app.feedback import jobs
    from app.jobs import queue

    engine = new_engine()
    await require_postgres(engine)
    monkeypatch.setattr(
        jobs, "enqueue", functools.partial(queue.enqueue, engine=engine)
    )
    case_id = f"promoted_run_{uuid.uuid4().hex[:12]}"
    db_session.add(
        EvalPromotedCase(id=case_id, query="Run me?", promoted_by="a@example.com")
    )
    await db_session.flush()
    try:
        first = await client.post(f"/admin/feedback/golden/{case_id}/run")
        assert first.status_code == 202, first.text
        assert first.json()["status"] == "queued"
        assert first.json()["job_id"]
        second = await client.post(f"/admin/feedback/golden/{case_id}/run")
        assert second.status_code == 202
        assert second.json()["status"] == "already_queued"
        async with engine.connect() as conn:
            queued = await conn.scalar(
                text("SELECT count(*) FROM jobs WHERE dedupe_key = :k"),
                {"k": jobs.golden_run_dedupe_key(case_id)},
            )
        assert queued == 1
        missing = await client.post("/admin/feedback/golden/promoted_missing/run")
        assert missing.status_code == 404
        bad = await client.post("/admin/feedback/golden/bad%20id;/run")
        assert bad.status_code == 422
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM jobs WHERE dedupe_key = :k"),
                {"k": jobs.golden_run_dedupe_key(case_id)},
            )
        await engine.dispose()


async def test_golden_run_handler_stores_result(db_session, monkeypatch):
    from app.core import database
    from app.evaluation import evaluator
    from app.feedback import jobs

    case_id = f"promoted_handler_{uuid.uuid4().hex[:12]}"
    db_session.add(
        EvalPromotedCase(id=case_id, query="Handled?", promoted_by="a@example.com")
    )
    await db_session.flush()

    async def fake_evaluate(case, _store, **_kw):
        assert case.id == case_id
        return SimpleNamespace(
            passed=True,
            grounded=True,
            confidence=0.9,
            guardrail_flags=[],
            latency_ms=12.0,
            error=None,
        )

    monkeypatch.setattr(evaluator, "evaluate_case", fake_evaluate)
    monkeypatch.setattr(
        database, "AsyncSessionLocal", shared_session_factory(db_session)
    )
    await jobs.golden_run_task({"case_id": case_id})
    await jobs.golden_run_task({"case_id": "promoted_missing"})  # logged, no error
    case = await db_session.get(EvalPromotedCase, case_id, populate_existing=True)
    assert case.last_run_passed is True
    assert case.last_run_detail["confidence"] == 0.9


# ---------------------------------------------------------------------------
# B5.2.4 analytics
# ---------------------------------------------------------------------------


async def _analytics(client) -> dict:
    r = await client.get("/admin/feedback/analytics")
    assert r.status_code == 200, r.text
    assert r.headers.get("etag")
    return r.json()


def _delta(after: dict, before: dict) -> dict:
    return {k: after.get(k, 0) - before.get(k, 0) for k in after}


async def test_analytics_exact_counts_and_unfiltered_total(client, db_session):
    before = await _analytics(client)
    mk = marker()
    session = await make_session(db_session, "owner@example.com")
    fixture = [
        # rating, grounded, outcome, tags, status, confidence
        (5, True, "answered", [], "new", 0.9),
        (4, False, "ungrounded", ["unclear"], "reviewed", 0.2),
        (1, True, "answered", ["wrong", "incomplete"], "new", 0.8),
        (1, False, "ungrounded", ["wrong"], "dismissed", 0.1),
        (2, False, "ungrounded", ["wrong"], "promoted", 0.3),
        (3, True, "answered", [], "new", None),
    ]
    gap_question = f"{mk} missing topic"
    for turn, (rating, grounded, outcome, tags, st, conf) in enumerate(fixture):
        question = gap_question if not grounded and rating <= 2 else f"{mk} {turn}"
        _u, asst = await make_exchange(
            db_session,
            session,
            turn=turn,
            question=question,
            grounded=grounded,
            outcome=outcome,
            confidence=conf,
            sources=[] if conf is not None else None,
        )
        await make_feedback(
            db_session,
            asst,
            actor=f"a{turn}@example.com",
            rating=rating,
            reason_tags=tags,
            triage_status=st,
        )
    after = await _analytics(client)

    assert after["total"] - before["total"] == 6
    assert after["total_feedback"] == after["total"]
    assert after["positive_count"] - before["positive_count"] == 2
    assert after["negative_count"] - before["negative_count"] == 3
    assert after["neutral_count"] - before["neutral_count"] == 1
    assert _delta(after["rating_distribution"], before["rating_distribution"]) == {
        "1": 2,
        "2": 1,
        "3": 1,
        "4": 1,
        "5": 1,
    }
    assert _delta(after["triage_funnel"], before["triage_funnel"]) == {
        "new": 3,
        "reviewed": 1,
        "promoted": 1,
        "dismissed": 1,
    }
    assert _delta(after["disagreement"], before["disagreement"]) == {
        "positive_grounded": 1,
        "positive_ungrounded": 1,
        "negative_grounded": 1,
        "negative_ungrounded": 2,
    }
    reasons_before = {r["key"]: r["count"] for r in before["top_reasons"]}
    reasons_after = {r["key"]: r["count"] for r in after["top_reasons"]}
    assert reasons_after["wrong"] - reasons_before.get("wrong", 0) == 3
    assert reasons_after["unclear"] - reasons_before.get("unclear", 0) == 1
    out_b = before["feedback_by_outcome"].get("ungrounded", {"total": 0, "negative": 0})
    out_a = after["feedback_by_outcome"]["ungrounded"]
    assert out_a["total"] - out_b["total"] == 3
    assert out_a["negative"] - out_b["negative"] == 2
    gap = next(g for g in after["knowledge_gaps"] if g["query"] == gap_question)
    assert gap["count"] == 2
    assert gap["avg_rating"] == 1.5
    today = datetime.utcnow().date().isoformat()
    day_a = next(d for d in after["daily_trend"] if d["day"] == today)
    day_b = next((d for d in before["daily_trend"] if d["day"] == today), {"total": 0})
    assert day_a["total"] - day_b["total"] == 6
    # The insights migration (turn_insights) is below this one in the stack,
    # so by_intent is reported; the seeded feedback has no turn labels, so
    # the per-intent counts do not move.
    intents_before = {r["key"]: r["count"] for r in before["by_intent"]}
    intents_after = {r["key"]: r["count"] for r in after["by_intent"]}
    assert intents_after == intents_before

    # F3: the analytics total ignores list filters entirely.
    filtered = await client.get(f"/admin/feedback?q={mk}&rating=1")
    assert filtered.json()["total"] == 2
    assert (await _analytics(client))["total"] == after["total"]


# ---------------------------------------------------------------------------
# PR-04 race: online-eval write must not hit an FK violation
# ---------------------------------------------------------------------------


async def test_online_eval_waits_for_exchange_write_and_tolerates_missing_rows(
    db_session, monkeypatch
):
    from app.governance import online_eval

    monkeypatch.setattr(
        online_eval, "AsyncSessionLocal", shared_session_factory(db_session)
    )
    session = await make_session(db_session, "eval-owner@example.com")
    pending_message_id = uuid.uuid4()
    write_done: asyncio.Future = asyncio.get_running_loop().create_future()
    request_id = f"race-{uuid.uuid4().hex[:12]}"

    task = asyncio.create_task(
        online_eval._run_evaluation_task(
            request_id=request_id,
            query="Q?",
            answer="A.",
            context_chunks=[],
            session_id=str(session.id),
            message_id=str(pending_message_id),
            grounded=True,
            after=write_done,
        )
    )
    await asyncio.sleep(0.05)
    assert not task.done()  # waiting for the exchange write
    db_session.add_all(
        [
            ChatMessageRecord(
                session_id=session.id, turn_index=1, role="user", content="Q?"
            ),
            ChatMessageRecord(
                id=pending_message_id,
                session_id=session.id,
                turn_index=1,
                role="assistant",
                content="A.",
            ),
        ]
    )
    await db_session.flush()
    write_done.set_result(None)
    await asyncio.wait_for(task, timeout=10)

    stored = await db_session.scalar(
        select(EvaluationRecord.message_id).where(
            EvaluationRecord.request_id == request_id
        )
    )
    assert stored == pending_message_id

    # The exchange write failed: the record is kept, without dangling FKs.
    lost_request = f"race-lost-{uuid.uuid4().hex[:12]}"
    await online_eval._run_evaluation_task(
        request_id=lost_request,
        query="Q?",
        answer="A.",
        context_chunks=[],
        session_id=str(uuid.uuid4()),
        message_id=str(uuid.uuid4()),
        grounded=True,
    )
    row = (
        await db_session.execute(
            select(EvaluationRecord.message_id, EvaluationRecord.session_id).where(
                EvaluationRecord.request_id == lost_request
            )
        )
    ).one()
    assert row.message_id is None
    assert row.session_id is None


def test_exchange_write_task_is_tracked_until_done():
    from app.core import sessions

    assert sessions.exchange_write_task(str(uuid.uuid4())) is None


def test_daily_coverage_divides_ratings_by_that_days_answers() -> None:
    from app.feedback.analytics import with_daily_coverage

    trend = [
        {"day": "2026-10-13", "total": 2},
        {"day": "2026-10-14", "total": 3},
    ]
    out = with_daily_coverage(trend, {"2026-10-14": 12})
    assert out[0]["answers"] == 0
    assert out[0]["coverage"] is None
    assert out[1]["coverage"] == 0.25
