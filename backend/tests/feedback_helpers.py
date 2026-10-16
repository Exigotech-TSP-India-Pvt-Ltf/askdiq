"""Fixture builders for the feedback / RLHF loop tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    ChatMessageRecord,
    ChatSession,
    EvaluationRecord,
    LatencyRecord,
    MessageFeedback,
)


def marker() -> str:
    """A unique letters-only token (never PII-like) for ``q=`` isolation."""
    return "mk" + "".join(chr(97 + int(c, 16)) for c in uuid.uuid4().hex[:12])


# Fixtures commit (in tests that is the SAVEPOINT of the per-test
# transaction), so a service-level rollback cannot discard them.


async def make_session(
    db: AsyncSession, email: str, ip: str = "198.51.100.7"
) -> ChatSession:
    now = datetime.utcnow()
    session = ChatSession(
        user_email=email,
        title="Feedback fixture",
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(days=1),
        created_ip=ip,
    )
    db.add(session)
    await db.commit()
    return session


async def make_exchange(
    db: AsyncSession,
    session: ChatSession,
    *,
    turn: int,
    question: str,
    answer: str = "An answer from the knowledge base.",
    grounded: bool | None = True,
    outcome: str | None = "answered",
    cost_usd: float | None = 0.0012,
    flags: list[str] | None = None,
    sources: list[dict] | None = None,
    confidence: float | None = None,
    latency_ms: float | None = None,
) -> tuple[ChatMessageRecord, ChatMessageRecord]:
    request_id = f"req-{uuid.uuid4().hex[:16]}"
    user_msg = ChatMessageRecord(
        session_id=session.id,
        turn_index=turn,
        role="user",
        content=question,
        ip_address=session.created_ip,
    )
    asst = ChatMessageRecord(
        session_id=session.id,
        turn_index=turn,
        role="assistant",
        content=answer,
        request_id=request_id,
        grounded=grounded,
        outcome=outcome,
        cost_usd=cost_usd,
        guardrail_flags=flags or [],
    )
    db.add_all([user_msg, asst])
    await db.flush()
    if sources is not None or confidence is not None:
        db.add(
            EvaluationRecord(
                request_id=request_id,
                message_id=asst.id,
                session_id=session.id,
                query=question,
                answer=answer,
                grounded=grounded,
                confidence=confidence,
                retrieved_chunks=sources or [],
                latency_ms=latency_ms,
            )
        )
    if latency_ms is not None:
        db.add(LatencyRecord(request_id=request_id, total_ms=latency_ms))
    await db.commit()
    return user_msg, asst


async def make_feedback(
    db: AsyncSession,
    asst: ChatMessageRecord,
    *,
    actor: str,
    rating: int,
    comment: str | None = None,
    reason_tags: list[str] | None = None,
    corrected_answer: str | None = None,
    triage_status: str = "new",
    created_at: datetime | None = None,
) -> MessageFeedback:
    now = created_at or datetime.utcnow()
    row = MessageFeedback(
        message_id=asst.id,
        session_id=asst.session_id,
        actor=actor,
        rating=rating,
        comment=comment,
        reason_tags=reason_tags or [],
        corrected_answer=corrected_answer,
        triage_status=triage_status,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.commit()
    return row
