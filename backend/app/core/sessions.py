"""Server-side chat session lifecycle: creation, expiry, and history loading.

Sessions and their messages are stored per-user in the database (see
ChatSession / ChatMessageRecord in app.models.db_models) — never on the
client — so:
  - history never leaks between two different sessions (each /query only
    ever loads messages scoped to one session_id), and
  - conversation history survives page reloads / different devices, and
    isn't lost if the browser tab is closed.
"""

import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.db_models import ChatMessageRecord, ChatSession
from app.schemas.query import ChatTurn
from app.schemas.session import ChatMessageOut, ChatSessionDetail, ChatSessionSummary

settings = get_settings()


def _session_timeout() -> timedelta:
    return timedelta(minutes=settings.session_timeout_minutes)


def _is_expired(session: ChatSession, now: datetime) -> bool:
    return session.expires_at < now


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


async def _get_owned_session(
    db: AsyncSession, session_id: str, user_email: str
) -> ChatSession:
    sid = _parse_uuid(session_id)
    session = await db.get(ChatSession, sid) if sid else None

    if session is None or session.user_email != user_email:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat session not found")

    return session


async def resolve_session_for_query(
    db: AsyncSession,
    user_email: str,
    session_id: str | None,
    client_ip: str | None = None,
) -> ChatSession:
    """Return the session a new /query call should read/write.

    - Explicit, valid, non-expired session_id -> reuse it.
    - Explicit but expired/unknown/foreign session_id -> silently start a
      fresh session instead of erroring, so a stale tab never gets "stuck".
    - No session_id -> resume the user's most recent non-expired session
      FOR THIS SAME IP, or start a new one if there isn't one — so a
      different IP (e.g. a different network/location) always gets its own
      session instead of silently continuing one created elsewhere.
    """
    now = datetime.utcnow()
    session: ChatSession | None = None

    if session_id:
        sid = _parse_uuid(session_id)
        candidate = await db.get(ChatSession, sid) if sid else None
        if (
            candidate is not None
            and candidate.user_email == user_email
            and not _is_expired(candidate, now)
        ):
            session = candidate
    else:
        result = await db.execute(
            select(ChatSession)
            .where(
                ChatSession.user_email == user_email,
                ChatSession.created_ip == client_ip,
            )
            .order_by(ChatSession.last_activity_at.desc())
            .limit(1)
        )
        candidate = result.scalar_one_or_none()
        if candidate is not None and not _is_expired(candidate, now):
            session = candidate

    if session is not None:
        return session

    session = ChatSession(
        user_email=user_email,
        title="New chat",
        created_at=now,
        last_activity_at=now,
        expires_at=now + _session_timeout(),
        created_ip=client_ip,
    )
    db.add(session)
    await db.flush()
    return session


async def load_recent_history(db: AsyncSession, session: ChatSession) -> list[ChatTurn]:
    """Load the most recent turns for this session as LLM-ready history."""
    result = await db.execute(
        select(ChatMessageRecord)
        .where(ChatMessageRecord.session_id == session.id)
        .order_by(ChatMessageRecord.created_at.desc())
        .limit(settings.session_history_max_messages)
    )
    records = list(reversed(result.scalars().all()))
    return [ChatTurn(role=r.role, content=r.content) for r in records]


async def _next_turn_index(db: AsyncSession, session_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(ChatMessageRecord.turn_index), -1)).where(
            ChatMessageRecord.session_id == session_id
        )
    )
    return result.scalar_one() + 1


async def record_exchange(
    db: AsyncSession,
    session: ChatSession,
    user_text: str,
    answer_text: str,
    request_id: str,
    grounded: bool,
    guardrail_flags: list[str],
    client_ip: str | None = None,
) -> tuple[str, str]:
    """Persist the user question + assistant answer, refresh session TTL,
    and return (user_message_id, assistant_message_id) for the new pair."""
    now = datetime.utcnow()
    turn_index = await _next_turn_index(db, session.id)

    user_message = ChatMessageRecord(
        session_id=session.id,
        turn_index=turn_index,
        role="user",
        content=user_text,
        created_at=now,
        # IP belongs to the incoming request/human message, not the
        # server-generated reply — stored once here, not duplicated onto
        # the assistant row too.
        ip_address=client_ip,
    )
    assistant_message = ChatMessageRecord(
        session_id=session.id,
        turn_index=turn_index,
        role="assistant",
        content=answer_text,
        created_at=now,
        request_id=request_id,
        grounded=grounded,
        guardrail_flags=guardrail_flags,
    )
    db.add_all([user_message, assistant_message])

    if session.title == "New chat":
        session.title = user_text.strip()[:80] or session.title

    session.last_activity_at = now
    session.expires_at = now + _session_timeout()

    await db.commit()

    return str(user_message.id), str(assistant_message.id)


def _to_summary(
    session: ChatSession, message_count: int, now: datetime
) -> ChatSessionSummary:
    return ChatSessionSummary(
        id=str(session.id),
        title=session.title,
        created_at=session.created_at,
        last_activity_at=session.last_activity_at,
        expires_at=session.expires_at,
        is_expired=_is_expired(session, now),
        message_count=message_count,
    )


async def list_sessions(db: AsyncSession, user_email: str) -> list[ChatSessionSummary]:
    now = datetime.utcnow()
    result = await db.execute(
        select(ChatSession, func.count(ChatMessageRecord.id))
        .outerjoin(ChatMessageRecord, ChatMessageRecord.session_id == ChatSession.id)
        .where(ChatSession.user_email == user_email)
        .group_by(ChatSession.id)
        .order_by(ChatSession.last_activity_at.desc())
    )
    return [_to_summary(session, count, now) for session, count in result.all()]


async def create_session(
    db: AsyncSession, user_email: str, title: str | None, client_ip: str | None = None
) -> ChatSessionSummary:
    now = datetime.utcnow()
    session = ChatSession(
        user_email=user_email,
        title=(title or "New chat").strip()[:256] or "New chat",
        created_at=now,
        last_activity_at=now,
        expires_at=now + _session_timeout(),
        created_ip=client_ip,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _to_summary(session, 0, now)


async def get_session_detail(
    db: AsyncSession, session_id: str, user_email: str
) -> ChatSessionDetail:
    session = await _get_owned_session(db, session_id, user_email)

    result = await db.execute(
        select(ChatMessageRecord)
        .where(ChatMessageRecord.session_id == session.id)
        .order_by(ChatMessageRecord.created_at)
    )
    messages = result.scalars().all()
    now = datetime.utcnow()
    summary = _to_summary(session, len(messages), now)

    return ChatSessionDetail(
        **summary.model_dump(),
        messages=[
            ChatMessageOut(
                id=str(m.id),
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                grounded=m.grounded,
                guardrail_flags=m.guardrail_flags,
            )
            for m in messages
        ],
    )


async def rename_session(
    db: AsyncSession, session_id: str, user_email: str, title: str
) -> ChatSessionSummary:
    session = await _get_owned_session(db, session_id, user_email)
    session.title = title.strip()[:256] or session.title
    await db.commit()
    await db.refresh(session)

    count_result = await db.execute(
        select(func.count(ChatMessageRecord.id)).where(
            ChatMessageRecord.session_id == session.id
        )
    )
    return _to_summary(session, count_result.scalar_one(), datetime.utcnow())


async def delete_session(db: AsyncSession, session_id: str, user_email: str) -> None:
    session = await _get_owned_session(db, session_id, user_email)
    await db.delete(session)
    await db.commit()


async def delete_exchange(
    db: AsyncSession, session_id: str, message_id: str, user_email: str
) -> None:
    """Delete one user+assistant pair, matched by their shared turn_index."""
    session = await _get_owned_session(db, session_id, user_email)

    mid = _parse_uuid(message_id)
    message = await db.get(ChatMessageRecord, mid) if mid else None

    if message is None or message.session_id != session.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")

    await db.execute(
        delete(ChatMessageRecord).where(
            ChatMessageRecord.session_id == session.id,
            ChatMessageRecord.turn_index == message.turn_index,
        )
    )
    await db.commit()
