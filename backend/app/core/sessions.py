"""Server-side chat session lifecycle: creation, expiry, and history loading.

Sessions and their messages are stored per-user in the database (see
ChatSession / ChatMessageRecord in app.models.db_models) — never on the
client — so:
  - history never leaks between two different sessions (each /query only
    ever loads messages scoped to one session_id), and
  - conversation history survives page reloads / different devices, and
    isn't lost if the browser tab is closed.
"""

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging_config import get_logger
from app.feedback import repository as feedback_repo
from app.governance.audit import (
    current_request_id,
    emit_audit_event,
    record_audit_event,
)
from app.governance.costing import compute_exchange_cost
from app.governance.outcomes import derive_outcome
from app.governance.pii import redact_pii
from app.insights.triggers import enqueue_classification_in_transaction
from app.models.db_models import ChatMessageRecord, ChatSession, MessageFeedback
from app.schemas.query import ChatTurn, Clarification
from app.schemas.session import (
    ChatMessageOut,
    ChatSessionDetail,
    ChatSessionSummary,
    UserFeedbackResponse,
)

# Feedback alert: enough recent ratings, and too many of them negative.
_FEEDBACK_ALERT_MIN_SAMPLES = 5
_NEGATIVE_RATING_MAX = 2
_FEEDBACK_ALERT_NEGATIVE_RATE = 0.25

settings = get_settings()
logger = get_logger(__name__)

# user_email -> in-flight background exchange writes (see schedule_exchange_write).
_pending_writes: dict[str, set[asyncio.Task]] = defaultdict(set)
# assistant_message_id -> its in-flight exchange write (online eval waits on it).
_writes_by_message: dict[str, asyncio.Task] = {}


def exchange_write_task(assistant_message_id: str) -> asyncio.Task | None:
    """The background write that will create ``assistant_message_id``, if pending."""
    return _writes_by_message.get(assistant_message_id)


async def drain_pending_writes(user_email: str) -> None:
    """Wait for this user's background writes so reads see their latest turn."""
    tasks = list(_pending_writes.get(user_email, ()))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _forget_write(user_email: str, task: asyncio.Task) -> None:
    bucket = _pending_writes.get(user_email)
    if bucket is not None:
        bucket.discard(task)
        if not bucket:
            _pending_writes.pop(user_email, None)


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
    await drain_pending_writes(user_email)
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
    rotated_from: str | None = None
    rotate_reason: str | None = None

    await drain_pending_writes(user_email)

    if session_id:
        sid = _parse_uuid(session_id)
        candidate = await db.get(ChatSession, sid) if sid else None
        if candidate is None or candidate.user_email != user_email:
            rotated_from, rotate_reason = session_id[:64], "not_found"
        elif _is_expired(candidate, now):
            rotated_from, rotate_reason = str(candidate.id), "expired"
            _emit_session_expired(candidate, user_email, client_ip)
        else:
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
        elif candidate is not None:
            rotated_from, rotate_reason = str(candidate.id), "expired"
            _emit_session_expired(candidate, user_email, client_ip)

    if session is not None:
        return session

    # Persisted later by schedule_exchange_write, so the id is assigned here.
    session = ChatSession(
        id=uuid.uuid4(),
        user_email=user_email,
        title="New chat",
        created_at=now,
        last_activity_at=now,
        expires_at=now + _session_timeout(),
        created_ip=client_ip,
    )
    if rotate_reason:
        emit_audit_event(
            "session_rotated",
            {
                "from_session_id": rotated_from,
                "to_session_id": str(session.id),
                "reason": rotate_reason,
            },
            actor=user_email,
            ip_address=client_ip,
        )
    return session


def _emit_session_expired(
    session: ChatSession, user_email: str, client_ip: str | None
) -> None:
    emit_audit_event(
        "session_expired",
        {
            "session_id": str(session.id),
            "expired_at": session.expires_at.isoformat() + "Z",
        },
        actor=user_email,
        ip_address=client_ip,
    )


async def load_recent_history(db: AsyncSession, session: ChatSession) -> list[ChatTurn]:
    """Load the most recent turns for this session as LLM-ready history."""
    result = await db.execute(
        select(ChatMessageRecord)
        .where(ChatMessageRecord.session_id == session.id)
        .order_by(ChatMessageRecord.created_at.desc())
        .limit(settings.session_history_max_messages)
    )
    records = list(reversed(result.scalars().all()))
    return [
        ChatTurn(
            role=r.role,
            content=r.content,
            outcome=r.outcome,
            clarification=stored_clarification(r.token_details),
        )
        for r in records
    ]


def stored_clarification(token_details: dict | None) -> Clarification | None:
    """The clarifying question persisted with an assistant message, if any."""
    raw = (token_details or {}).get("clarification")
    if not isinstance(raw, dict):
        return None
    try:
        return Clarification.model_validate(raw)
    except ValidationError:
        return None


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
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    embedding_tokens: int | None = None,
    guardrail_tokens: int | None = None,
    cost_usd: float | None = None,
    token_details: dict | None = None,
    outcome: str | None = None,
    commit: bool = True,
    user_message_id: uuid.UUID | None = None,
    assistant_message_id: uuid.UUID | None = None,
) -> tuple[str, str]:
    """Persist the user question + assistant answer, refresh session TTL,
    and return (user_message_id, assistant_message_id) for the new pair."""
    now = datetime.utcnow()
    turn_index = await _next_turn_index(db, session.id)

    # Derive canonical outcome if not supplied
    if outcome is None:
        outcome = derive_outcome(
            outcome_hint=(token_details or {}).get("outcome"),
            guardrail_flags=guardrail_flags,
            grounded=grounded,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            query=user_text,
            answer=answer_text,
            token_details=token_details,
        )

    # Compute cost & details if not supplied
    if cost_usd is None:
        computed_cost, computed_details = compute_exchange_cost(
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            reasoning_tokens=reasoning_tokens or 0,
            cached_tokens=(token_details or {}).get("cached_tokens", 0),
            query_embed_tokens=embedding_tokens or 0,
            guardrail_embed_tokens=guardrail_tokens or 0,
        )
        cost_usd = computed_cost
        token_details = {**computed_details, **(token_details or {})}

    user_message = ChatMessageRecord(
        **({"id": user_message_id} if user_message_id else {}),
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
        **({"id": assistant_message_id} if assistant_message_id else {}),
        session_id=session.id,
        turn_index=turn_index,
        role="assistant",
        content=answer_text,
        created_at=now,
        request_id=request_id,
        grounded=grounded,
        guardrail_flags=guardrail_flags,
        outcome=outcome,
        # Actual Azure OpenAI usage for this exchange's LLM call (None when
        # there wasn't one — guardrail-blocked/fallback/conversational).
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        embedding_tokens=embedding_tokens,
        guardrail_tokens=guardrail_tokens,
        cost_usd=cost_usd,
        token_details=token_details,
    )
    db.add_all([user_message, assistant_message])

    if session.title == "New chat":
        session.title = user_text.strip()[:80] or session.title

    session.last_activity_at = now
    session.expires_at = now + _session_timeout()

    if commit:
        await db.commit()
    else:
        # Caller commits once; flush assigns the message ids.
        await db.flush()

    return str(user_message.id), str(assistant_message.id)


async def _write_exchange(
    *,
    user_email: str,
    session_id: uuid.UUID,
    new_session: ChatSession | None,
    user_message_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
    request_id: str,
    client_ip: str | None,
    audit_detail: dict,
    **exchange,
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            if new_session is not None:
                db.add(new_session)
                session = new_session
                await record_audit_event(
                    db,
                    request_id=request_id,
                    actor=user_email,
                    action="session_created",
                    detail={"session_id": str(new_session.id), "source": "query"},
                    ip_address=client_ip,
                    commit=False,
                )
            else:
                session = await db.get(ChatSession, session_id)
                if session is None:
                    logger.warning("exchange_persist_skipped session=%s", session_id)
                    return

            await record_exchange(
                db,
                session=session,
                request_id=request_id,
                client_ip=client_ip,
                commit=False,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                **exchange,
            )
            await record_audit_event(
                db,
                request_id=request_id,
                actor=user_email,
                action="query",
                detail={**audit_detail, "message_id": str(assistant_message_id)},
                ip_address=client_ip,
                commit=False,
            )
            # Labelled later by the job worker, never in the request path.
            await enqueue_classification_in_transaction(db, assistant_message_id)
            await db.commit()
    except Exception:
        logger.warning(
            "exchange_persist_failed request_id=%s", request_id, exc_info=True
        )


def schedule_exchange_write(
    *,
    user_email: str,
    session: ChatSession,
    audit_detail: dict,
    request_id: str,
    client_ip: str | None,
    **exchange,
) -> tuple[str, str, str]:
    """Persist the exchange + audit row in the background.

    Returns (user_message_id, assistant_message_id, session_title) immediately;
    ids are generated here so the response needn't wait for the database.
    """
    user_message_id = uuid.uuid4()
    assistant_message_id = uuid.uuid4()

    title = session.title
    if title == "New chat":
        title = exchange["user_text"].strip()[:80] or title

    task = asyncio.create_task(
        _write_exchange(
            user_email=user_email,
            session_id=session.id,
            new_session=session if sa_inspect(session).transient else None,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            request_id=request_id,
            client_ip=client_ip,
            audit_detail=audit_detail,
            **exchange,
        )
    )
    _pending_writes[user_email].add(task)
    task.add_done_callback(lambda t: _forget_write(user_email, t))
    message_key = str(assistant_message_id)
    _writes_by_message[message_key] = task
    task.add_done_callback(lambda _t: _writes_by_message.pop(message_key, None))

    return str(user_message_id), str(assistant_message_id), title


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
    await drain_pending_writes(user_email)
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
    await db.flush()
    await record_audit_event(
        db,
        request_id=current_request_id(),
        actor=user_email,
        action="session_created",
        detail={"session_id": str(session.id), "source": "explicit"},
        ip_address=client_ip,
        commit=False,
    )
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

    feedback_map: dict[uuid.UUID, int] = {}
    if messages:
        msg_ids = [m.id for m in messages]
        feedback_rows = (
            await db.execute(
                select(MessageFeedback.message_id, MessageFeedback.rating).where(
                    MessageFeedback.message_id.in_(msg_ids),
                    MessageFeedback.actor == user_email,
                )
            )
        ).all()
        feedback_map = dict(feedback_rows)

    return ChatSessionDetail(
        **summary.model_dump(),
        messages=[
            ChatMessageOut(
                id=str(m.id),
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                request_id=m.request_id,
                grounded=m.grounded,
                guardrail_flags=m.guardrail_flags,
                feedback_rating=feedback_map.get(m.id),
                prompt_tokens=m.prompt_tokens,
                completion_tokens=m.completion_tokens,
                total_tokens=m.total_tokens,
                reasoning_tokens=m.reasoning_tokens,
                embedding_tokens=m.embedding_tokens,
                guardrail_tokens=m.guardrail_tokens,
                cost_usd=m.cost_usd,
                token_details=m.token_details,
                outcome=m.outcome,
                clarification=stored_clarification(m.token_details),
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


async def record_feedback(
    db: AsyncSession,
    *,
    session_id: str,
    message_id: str,
    actor: str,
    rating: int,
    comment: str | None = None,
    client_ip: str | None = None,
    request_id: str | None = None,
    reason_tags: list[str] | None = None,
    corrected_answer: str | None = None,
    details_sent: bool = True,
) -> UserFeedbackResponse:
    """Upsert feedback for an assistant message and record an audit event (Subtasks 1.3.2 & 1.3.3).

    ``details_sent=False`` means the client sent neither ``reason_tags`` nor
    ``corrected_answer`` (e.g. a note saved on a reloaded thumbs-down). For a
    negative rating the stored details are then kept instead of being wiped;
    a positive rating always clears them (they describe what was wrong).
    """
    session = await _get_owned_session(db, session_id, actor)

    mid = _parse_uuid(message_id)
    if not mid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    message = await db.scalar(
        select(ChatMessageRecord).where(
            ChatMessageRecord.id == mid,
            ChatMessageRecord.session_id == session.id,
        )
    )
    if message is None or message.role != "assistant":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant message not found in this session",
        )

    now = datetime.utcnow()
    clean_comment = comment.strip() if comment else None
    # Stored and audited masked: free text can carry emails, phones, etc.
    redacted_comment = redact_pii(clean_comment)[0] if clean_comment else None
    tags = [str(t) for t in (reason_tags or [])]
    # The "better answer" becomes training data: mask PII before it is stored.
    masked_answer = redact_pii(corrected_answer)[0] if corrected_answer else None
    keep_details = not details_sent and rating <= _NEGATIVE_RATING_MAX

    # Atomic upsert on (message_id, actor): a resubmission updates the row.
    feedback_row = await feedback_repo.upsert_chat_feedback(
        db,
        message_id=mid,
        session_id=session.id,
        actor=actor,
        rating=rating,
        comment=redacted_comment,
        reason_tags=tags,
        corrected_answer=masked_answer,
        now=now,
        keep_details=keep_details,
    )

    # Append audit event with expected keys
    req_id = request_id or f"feedback-{uuid.uuid4().hex[:12]}"
    effective_ip = (
        session.created_ip
        if (
            client_ip in ("127.0.0.1", "::1", "localhost", "testclient", None)
            and session.created_ip
        )
        else (client_ip or session.created_ip)
    )
    await record_audit_event(
        db,
        request_id=req_id,
        actor=actor,
        action="user_feedback",
        detail={
            "rating": rating,
            "comment": redacted_comment,
            "reason_tags": list(feedback_row.reason_tags or []),
            "has_corrected_answer": feedback_row.corrected_answer is not None,
            "conversation_id": str(session.id),
            "message_id": str(mid),
        },
        ip_address=effective_ip,
        commit=False,
    )

    logger.info(
        "user_feedback_recorded",
        actor=actor,
        rating=rating,
        session_id=str(session.id),
        message_id=str(mid),
    )

    # Check for negative feedback spike in the last 1 hour
    one_hour_ago = now - timedelta(hours=1)
    recent_ratings = (
        (
            await db.execute(
                select(MessageFeedback.rating).where(
                    MessageFeedback.created_at >= one_hour_ago
                )
            )
        )
        .scalars()
        .all()
    )

    if len(recent_ratings) >= _FEEDBACK_ALERT_MIN_SAMPLES:
        neg_count = sum(1 for r in recent_ratings if r <= _NEGATIVE_RATING_MAX)
        neg_rate = neg_count / len(recent_ratings)
        if neg_rate > _FEEDBACK_ALERT_NEGATIVE_RATE:
            await record_audit_event(
                db,
                request_id=req_id,
                actor="system",
                action="negative_feedback_spike",
                detail={
                    "negative_rate": round(neg_rate, 3),
                    "threshold": 0.25,
                    "total_1h": len(recent_ratings),
                    "negative_count": neg_count,
                    "trigger_message_id": str(mid),
                },
                ip_address=effective_ip,
                commit=False,
            )
            logger.warning(
                "negative_feedback_spike",
                negative_rate=round(neg_rate, 3),
                threshold=0.25,
                total_1h=len(recent_ratings),
                negative_count=neg_count,
            )

    await db.commit()

    return UserFeedbackResponse(
        message_id=str(feedback_row.message_id),
        session_id=str(feedback_row.session_id),
        rating=feedback_row.rating,
        comment=feedback_row.comment,
        reason_tags=list(feedback_row.reason_tags or []),
        corrected_answer=feedback_row.corrected_answer,
        triage_status=feedback_row.triage_status,
        created_at=feedback_row.created_at,
        updated_at=feedback_row.updated_at,
    )
