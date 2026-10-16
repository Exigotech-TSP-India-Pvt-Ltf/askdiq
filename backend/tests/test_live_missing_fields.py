"""Dashboard fields the live write path used to leave empty (seed-data PR).

- query audit rows carry ``message_id``;
- online-eval records receive the turn's ``latency_ms``;
- ingest audit rows carry ``cost_usd`` / ``document_id`` / ``total_chunks``;
- governance ``active_users`` and feedback ``feedback_rate`` count the
  session IP (assistant rows never store one).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.core import sessions
from app.dashboard import active_visitors
from app.governance.audience import invalidate_audience_cache
from app.ingestion.service import ingest_text
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    MessageFeedback,
)
from app.schemas.query import QueryResponse


class _FakeDb:
    async def __aenter__(self) -> _FakeDb:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, *_a: object) -> object:
        return object()

    async def commit(self) -> None:
        return None


async def test_query_audit_detail_carries_assistant_message_id(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_record_exchange(*_a: object, **_k: object) -> tuple[str, str]:
        return "u", "a"

    async def fake_audit(_db: object, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(sessions, "AsyncSessionLocal", _FakeDb)
    monkeypatch.setattr(sessions, "record_exchange", fake_record_exchange)
    monkeypatch.setattr(sessions, "record_audit_event", fake_audit)
    monkeypatch.setattr(sessions, "enqueue_classification_in_transaction", noop)

    message_id = uuid.uuid4()
    await sessions._write_exchange(
        user_email="live-fields@example.com",
        session_id=uuid.uuid4(),
        new_session=None,
        user_message_id=uuid.uuid4(),
        assistant_message_id=message_id,
        request_id="req-1",
        client_ip="203.0.113.9",
        audit_detail={"query": "What is Zero Trust?"},
    )
    assert captured["action"] == "query"
    assert captured["detail"] == {
        "query": "What is Zero Trust?",
        "message_id": str(message_id),
    }


@pytest.mark.db
async def test_query_passes_latency_to_online_eval(client, monkeypatch) -> None:
    from app.api.routes import query as query_route

    eval_kwargs: dict[str, Any] = {}

    async def fake_run_query(**kwargs: Any) -> QueryResponse:
        return QueryResponse(
            answer="Zero Trust verifies every request explicitly.",
            sources=[],
            grounded=True,
            confidence=0.9,
            request_id=kwargs["request_id"],
            latency_ms=1234.5,
            guardrail_flags=[],
            outcome="answered",
        )

    def fake_schedule_write(**_k: Any) -> tuple[str, str, str]:
        return str(uuid.uuid4()), str(uuid.uuid4()), "t"

    monkeypatch.setattr(query_route, "run_query", fake_run_query)
    monkeypatch.setattr(query_route, "schedule_exchange_write", fake_schedule_write)
    monkeypatch.setattr(query_route, "exchange_write_task", lambda _mid: None)
    monkeypatch.setattr(
        query_route, "schedule_online_evaluation", lambda **k: eval_kwargs.update(k)
    )
    resp = await client.post("/query", json={"query": "What is Zero Trust?"})
    assert resp.status_code == 200, resp.text
    assert eval_kwargs["latency_ms"] == pytest.approx(1234.5)


@pytest.mark.db
async def test_ingest_audit_has_reader_keys(db_session, fake_embed) -> None:
    result = await ingest_text(
        db_session,
        source_name=f"live-fields-{uuid.uuid4().hex[:8]}.md",
        content="# Policy\n\nAll administrators must use phishing-resistant MFA.",
        actor="live-fields@example.com",
    )
    row = await db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "ingest")
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    )
    assert row is not None
    assert row.detail["cost_usd"] == row.detail["embedding_cost_usd"]
    assert row.detail["document_id"] == result["document_id"]
    assert row.detail["total_chunks"] >= 1


async def _user_turn(db_session, ip: str, *, rating: int | None = None) -> None:
    now = datetime.utcnow()
    session = ChatSession(
        id=uuid.uuid4(),
        user_email="live-fields@example.com",
        title="t",
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(hours=1),
        created_ip=ip,
    )
    db_session.add(session)
    await db_session.flush()
    request_id = f"live-{uuid.uuid4().hex[:12]}"
    db_session.add(
        ChatMessageRecord(
            session_id=session.id,
            turn_index=0,
            role="user",
            content="q",
            created_at=now,
            ip_address=ip,
        )
    )
    answer = ChatMessageRecord(
        session_id=session.id,
        turn_index=0,
        role="assistant",
        content="a",
        created_at=now,
        request_id=request_id,
        grounded=True,
        guardrail_flags=[],
        outcome="answered",
    )
    db_session.add(answer)
    await db_session.flush()
    if rating is not None:
        db_session.add(
            MessageFeedback(
                message_id=answer.id,
                session_id=session.id,
                actor="live-fields@example.com",
                rating=rating,
                created_at=now,
                updated_at=now,
            )
        )
        await db_session.flush()


def _unique_ip() -> str:
    n = uuid.uuid4().int
    return f"203.0.{113 if n % 2 else 112}.{10 + n % 240}"


@pytest.mark.db
async def test_governance_active_users_counts_session_ips(client, db_session) -> None:
    async def active() -> int:
        resp = await client.get("/admin/governance/overview?audience=overview")
        assert resp.status_code == 200, resp.text
        kpi = resp.json()["kpis"]["active_users"]
        assert kpi["source"] == active_visitors.SOURCE
        return int(kpi["value"])

    before = await active()
    tag = uuid.uuid4().hex
    await _user_turn(db_session, f"2001:db8::{tag[:4]}:{tag[4:8]}:{tag[8:12]}")
    assert await active() == before + 1


@pytest.mark.db
async def test_feedback_rate_for_users_audience_is_not_zero(client, db_session) -> None:
    await _user_turn(db_session, _unique_ip(), rating=5)
    invalidate_audience_cache()
    try:
        resp = await client.get("/admin/feedback/analytics?audience=users")
        assert resp.status_code == 200, resp.text
        assert resp.json()["feedback_rate"] > 0
    finally:
        invalidate_audience_cache()
