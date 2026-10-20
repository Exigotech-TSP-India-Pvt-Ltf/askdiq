"""Seeders shared by the chart-aggregation tests (dashboard, entities, usage).

Rows are written inside the caller's rolled-back ``db_session`` and timed in a
random far-past window (``unique_day``) so window filters isolate them from
rows other tests committed. Every IP is a random TEST-NET-2 address and
sessions default to the anonymous actor, so each IP is one active visitor
and per-visitor numbers are exact.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.auth import DEFAULT_ACTOR
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    LatencyRecord,
    MessageFeedback,
)


def unique_day() -> datetime:
    """Midnight of a random day in 2001-2010 (unique per test run)."""
    offset = uuid.uuid4().int % (365 * 9)
    return datetime(2001, 1, 10) + timedelta(days=offset)


def unique_ip() -> str:
    """Fixed-width octets, so one IP is never a substring of another."""
    n = uuid.uuid4().int
    return f"198.51.{100 + n % 150}.{100 + (n >> 8) % 150}"


def marker() -> str:
    return "mk" + "".join(chr(97 + int(c, 16)) for c in uuid.uuid4().hex[:12])


def iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


async def session_at(
    db: AsyncSession,
    created_at: datetime,
    ip: str | None,
    *,
    title: str = "Fixture chat",
    email: str = DEFAULT_ACTOR,
    last_activity_at: datetime | None = None,
) -> ChatSession:
    session = ChatSession(
        id=uuid.uuid4(),
        user_email=email,
        title=title,
        created_at=created_at,
        last_activity_at=last_activity_at or created_at,
        expires_at=created_at + timedelta(hours=1),
        created_ip=ip,
    )
    db.add(session)
    await db.flush()
    return session


async def exchange(
    db: AsyncSession,
    session: ChatSession,
    turn: int,
    at: datetime,
    *,
    outcome: str | None = "answered",
    cost: float | None = 0.001,
    latency_ms: float | None = None,
    question: str = "how many leave days",
    grounded: bool | None = True,
    tokens: dict[str, Any] | None = None,
    stages: dict[str, float] | None = None,
) -> tuple[ChatMessageRecord, ChatMessageRecord]:
    """A user + assistant pair (plus a latency row when ``latency_ms`` is set)."""
    request_id = f"b7-{uuid.uuid4().hex[:20]}"
    tok = tokens or {}
    user = ChatMessageRecord(
        id=uuid.uuid4(),
        session_id=session.id,
        turn_index=turn,
        role="user",
        content=question,
        created_at=at,
        ip_address=session.created_ip,
    )
    asst = ChatMessageRecord(
        id=uuid.uuid4(),
        session_id=session.id,
        turn_index=turn,
        role="assistant",
        content="An answer.",
        created_at=at,
        request_id=request_id,
        grounded=grounded,
        outcome=outcome,
        cost_usd=cost,
        prompt_tokens=tok.get("prompt"),
        completion_tokens=tok.get("completion"),
        total_tokens=tok.get("total"),
        reasoning_tokens=tok.get("reasoning"),
        embedding_tokens=tok.get("embedding"),
        guardrail_tokens=tok.get("guardrail"),
        token_details=tok.get("details"),
    )
    db.add_all([user, asst])
    if latency_ms is not None:
        db.add(
            LatencyRecord(
                id=uuid.uuid4(),
                request_id=request_id,
                created_at=at,
                total_ms=latency_ms,
                **{f"{k}_ms": v for k, v in (stages or {}).items()},
            )
        )
    await db.flush()
    return user, asst


async def feedback(
    db: AsyncSession,
    asst: ChatMessageRecord,
    rating: int,
    at: datetime,
    *,
    comment: str | None = None,
    reason_tags: list[str] | None = None,
) -> MessageFeedback:
    row = MessageFeedback(
        id=uuid.uuid4(),
        message_id=asst.id,
        session_id=asst.session_id,
        actor=f"rater-{uuid.uuid4().hex[:8]}@example.com",
        rating=rating,
        comment=comment,
        reason_tags=reason_tags or [],
        created_at=at,
        updated_at=at,
    )
    db.add(row)
    await db.flush()
    return row


async def audit(
    db: AsyncSession,
    action: str,
    at: datetime,
    detail: dict[str, Any],
    *,
    ip: str | None = None,
    request_id: str | None = None,
) -> AuditLog:
    row = AuditLog(
        id=uuid.uuid4(),
        created_at=at,
        request_id=request_id or f"b7a-{uuid.uuid4().hex[:16]}",
        actor="b7-tests@example.com",
        action=action,
        detail=detail,
        ip_address=ip,
    )
    db.add(row)
    await db.flush()
    return row
