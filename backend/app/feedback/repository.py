"""SQL for the feedback loop: enriched list, upsert, triage updates, export.

Every list/detail read is a single statement: the assistant turn, the user
question (same ``turn_index``), the latest online-eval record (``LATERAL``)
and the request latency are joined in, so there is no N+1 (F1).
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    and_,
    cast,
    func,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.governance.usage_queries import escape_ilike_pattern
from app.models.db_models import (
    ChatMessageRecord,
    ChatSession,
    EvalPromotedCase,
    EvaluationRecord,
    LatencyRecord,
    MessageFeedback,
)
from app.schemas.feedback import FeedbackSort, TriageStatus

UserMessage = aliased(ChatMessageRecord, name="user_msg")

CLIENT_IP = func.coalesce(
    ChatSession.created_ip, ChatMessageRecord.ip_address, "Unknown"
)


class InvalidCursorError(ValueError):
    """The keyset cursor could not be decoded."""


@dataclass(frozen=True, slots=True)
class FeedbackFilters:
    audience_cond: ColumnElement[bool] | None = None
    rating: int | None = None
    q: str | None = None
    triage_status: TriageStatus | None = None
    outcome: str | None = None
    grounded: bool | None = None
    has_comment: bool | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    message_id: uuid.UUID | None = None
    feedback_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------


def latest_eval():
    """Latest online-eval record of the rated message (LATERAL, at most 1 row)."""
    return (
        select(
            EvaluationRecord.retrieved_chunks.label("retrieved_chunks"),
            EvaluationRecord.confidence.label("confidence"),
            EvaluationRecord.latency_ms.label("eval_latency_ms"),
        )
        .where(EvaluationRecord.message_id == MessageFeedback.message_id)
        .order_by(EvaluationRecord.created_at.desc())
        .limit(1)
        .lateral("ev")
    )


def join_exchange(stmt: Select) -> Select:
    """Joins shared by the list, count, analytics and export queries."""
    return (
        stmt.outerjoin(ChatSession, MessageFeedback.session_id == ChatSession.id)
        .outerjoin(
            ChatMessageRecord, MessageFeedback.message_id == ChatMessageRecord.id
        )
        .outerjoin(
            UserMessage,
            and_(
                UserMessage.session_id == ChatMessageRecord.session_id,
                UserMessage.turn_index == ChatMessageRecord.turn_index,
                UserMessage.role == "user",
            ),
        )
    )


def base_feedback_from() -> Select:
    """``SELECT ... FROM message_feedback`` joined to its exchange (no columns)."""
    return join_exchange(select(MessageFeedback.id).select_from(MessageFeedback))


def _conditions(f: FeedbackFilters) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    if f.audience_cond is not None:
        conds.append(f.audience_cond)
    if f.feedback_id is not None:
        conds.append(MessageFeedback.id == f.feedback_id)
    if f.message_id is not None:
        conds.append(MessageFeedback.message_id == f.message_id)
    if f.rating is not None:
        conds.append(MessageFeedback.rating == f.rating)
    if f.triage_status is not None:
        conds.append(MessageFeedback.triage_status == f.triage_status.value)
    if f.outcome is not None:
        conds.append(func.coalesce(ChatMessageRecord.outcome, "answered") == f.outcome)
    if f.grounded is not None:
        conds.append(ChatMessageRecord.grounded.is_(f.grounded))
    if f.has_comment is not None:
        has = and_(MessageFeedback.comment.is_not(None), MessageFeedback.comment != "")
        conds.append(has if f.has_comment else ~has)
    if f.date_from is not None:
        conds.append(MessageFeedback.created_at >= f.date_from)
    if f.date_to is not None:
        conds.append(MessageFeedback.created_at <= f.date_to)
    if f.q and f.q.strip():
        pattern = f"%{escape_ilike_pattern(f.q.strip())}%"
        conds.append(
            or_(
                MessageFeedback.comment.ilike(pattern),
                MessageFeedback.actor.ilike(pattern),
                CLIENT_IP.ilike(pattern),
                UserMessage.content.ilike(pattern),
                cast(MessageFeedback.session_id, String).ilike(pattern),
                cast(MessageFeedback.message_id, String).ilike(pattern),
            )
        )
    return conds


_SORT_KEYS: dict[FeedbackSort, list[tuple[Any, bool]]] = {
    # (column, descending)
    FeedbackSort.CREATED_AT_DESC: [
        (MessageFeedback.created_at, True),
        (MessageFeedback.id, True),
    ],
    FeedbackSort.CREATED_AT_ASC: [
        (MessageFeedback.created_at, False),
        (MessageFeedback.id, False),
    ],
    FeedbackSort.RATING_DESC: [
        (MessageFeedback.rating, True),
        (MessageFeedback.created_at, True),
        (MessageFeedback.id, True),
    ],
    FeedbackSort.RATING_ASC: [
        (MessageFeedback.rating, False),
        (MessageFeedback.created_at, True),
        (MessageFeedback.id, True),
    ],
}


def encode_cursor(sort: FeedbackSort, row: Any) -> str:
    values: list[Any] = []
    for col, _desc in _SORT_KEYS[sort]:
        value = getattr(row, col.key)
        values.append(value.isoformat() if isinstance(value, datetime) else str(value))
    raw = json.dumps({"s": sort.value, "v": values}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(sort: FeedbackSort, cursor: str) -> list[Any]:
    """Typed key values from ``cursor``; raises InvalidCursorError."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if data.get("s") != sort.value:
            raise InvalidCursorError("cursor was issued for another sort order")
        raw_values = data["v"]
        keys = _SORT_KEYS[sort]
        if not isinstance(raw_values, list) or len(raw_values) != len(keys):
            raise InvalidCursorError("malformed cursor")
        out: list[Any] = []
        for (col, _desc), raw in zip(keys, raw_values, strict=True):
            if col is MessageFeedback.rating:
                out.append(int(raw))
            elif col is MessageFeedback.created_at:
                out.append(datetime.fromisoformat(str(raw)))
            else:
                out.append(uuid.UUID(str(raw)))
        return out
    except InvalidCursorError:
        raise
    except (ValueError, KeyError, TypeError, binascii.Error, AttributeError) as exc:
        raise InvalidCursorError("malformed cursor") from exc


def _keyset_condition(sort: FeedbackSort, values: Sequence[Any]) -> ColumnElement:
    """Rows strictly after ``values`` in ``sort`` order (mixed directions ok)."""
    keys = _SORT_KEYS[sort]
    branches = []
    for i, (col, desc) in enumerate(keys):
        prefix = [keys[j][0] == values[j] for j in range(i)]
        step = col < values[i] if desc else col > values[i]
        branches.append(and_(*prefix, step))
    return or_(*branches)


def _order_by(sort: FeedbackSort) -> list[Any]:
    return [col.desc() if desc else col.asc() for col, desc in _SORT_KEYS[sort]]


def _item_columns(ev: Any) -> list[Any]:
    latency = (
        select(LatencyRecord.total_ms)
        .where(LatencyRecord.request_id == ChatMessageRecord.request_id)
        .order_by(LatencyRecord.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    return [
        MessageFeedback.id,
        MessageFeedback.rating,
        MessageFeedback.comment,
        MessageFeedback.session_id,
        MessageFeedback.message_id,
        MessageFeedback.actor,
        MessageFeedback.created_at,
        MessageFeedback.updated_at,
        MessageFeedback.reason_tags,
        MessageFeedback.corrected_answer,
        MessageFeedback.triage_status,
        MessageFeedback.reviewed_by,
        MessageFeedback.reviewed_at,
        MessageFeedback.review_note,
        MessageFeedback.promoted_case_id,
        MessageFeedback.version,
        CLIENT_IP.label("ip_address"),
        UserMessage.content.label("user_query"),
        ChatMessageRecord.content.label("answer"),
        ChatMessageRecord.outcome.label("outcome"),
        ChatMessageRecord.grounded.label("grounded"),
        ChatMessageRecord.guardrail_flags.label("guardrail_flags"),
        ChatMessageRecord.cost_usd.label("cost_usd"),
        ev.c.retrieved_chunks,
        ev.c.confidence,
        func.coalesce(latency, ev.c.eval_latency_ms).label("latency_ms"),
    ]


async def list_feedback(
    db: AsyncSession,
    filters: FeedbackFilters,
    *,
    sort: FeedbackSort,
    limit: int,
    offset: int = 0,
    cursor_values: Sequence[Any] | None = None,
) -> tuple[list[Any], int, bool]:
    """(rows, filtered total, has_more) in exactly two statements."""
    ev = latest_eval()
    conds = _conditions(filters)
    stmt = join_exchange(
        select(*_item_columns(ev)).select_from(MessageFeedback)
    ).outerjoin(ev, true())
    count_stmt = join_exchange(
        select(func.count(MessageFeedback.id)).select_from(MessageFeedback)
    )
    if conds:
        stmt = stmt.where(*conds)
        count_stmt = count_stmt.where(*conds)
    if cursor_values is not None:
        stmt = stmt.where(_keyset_condition(sort, cursor_values))
    else:
        stmt = stmt.offset(offset)
    stmt = stmt.order_by(*_order_by(sort)).limit(limit + 1)

    total = int((await db.scalar(count_stmt)) or 0)
    rows = list((await db.execute(stmt)).all())
    has_more = len(rows) > limit
    return rows[:limit], total, has_more


async def get_feedback_item(db: AsyncSession, feedback_id: uuid.UUID) -> Any | None:
    ev = latest_eval()
    stmt = (
        join_exchange(select(*_item_columns(ev)).select_from(MessageFeedback))
        .outerjoin(ev, true())
        .where(MessageFeedback.id == feedback_id)
    )
    return (await db.execute(stmt)).first()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def upsert_chat_feedback(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    session_id: uuid.UUID,
    actor: str,
    rating: int,
    comment: str | None,
    reason_tags: list[str],
    corrected_answer: str | None,
    now: datetime,
) -> Any:
    """One row per (message, actor): insert, or replace the user's fields."""
    stmt = pg_insert(MessageFeedback).values(
        id=uuid.uuid4(),
        message_id=message_id,
        session_id=session_id,
        actor=actor,
        rating=rating,
        comment=comment,
        reason_tags=reason_tags,
        corrected_answer=corrected_answer,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_message_feedback_message_actor",
        set_={
            "rating": stmt.excluded.rating,
            "comment": stmt.excluded.comment,
            "reason_tags": stmt.excluded.reason_tags,
            "corrected_answer": stmt.excluded.corrected_answer,
            "updated_at": stmt.excluded.updated_at,
            "version": MessageFeedback.version + 1,
        },
    ).returning(
        MessageFeedback.id,
        MessageFeedback.message_id,
        MessageFeedback.session_id,
        MessageFeedback.rating,
        MessageFeedback.comment,
        MessageFeedback.reason_tags,
        MessageFeedback.corrected_answer,
        MessageFeedback.triage_status,
        MessageFeedback.created_at,
        MessageFeedback.updated_at,
    )
    return (await db.execute(stmt)).one()


async def update_status_if_version(
    db: AsyncSession,
    feedback_id: uuid.UUID,
    expected_version: int,
    values: dict[str, Any],
) -> bool:
    """Conditional UPDATE (optimistic lock); False when the version moved on."""
    result = await db.execute(
        update(MessageFeedback)
        .where(
            MessageFeedback.id == feedback_id,
            MessageFeedback.version == expected_version,
        )
        .values(**values, version=MessageFeedback.version + 1)
        .returning(MessageFeedback.id)
        .execution_options(synchronize_session=False)
    )
    return result.first() is not None


async def lock_feedback_rows(
    db: AsyncSession, ids: Sequence[uuid.UUID]
) -> list[MessageFeedback]:
    """``SELECT ... FOR UPDATE`` in id order (stable lock order, no deadlocks)."""
    result = await db.execute(
        select(MessageFeedback)
        .where(MessageFeedback.id.in_(list(ids)))
        .order_by(MessageFeedback.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def insert_promoted_case(db: AsyncSession, values: dict[str, Any]) -> bool:
    """Insert a golden case; False when the message was already promoted."""
    result = await db.execute(
        pg_insert(EvalPromotedCase)
        .values(**values)
        .on_conflict_do_nothing()
        .returning(EvalPromotedCase.id)
    )
    return result.first() is not None


async def case_id_for_message(db: AsyncSession, message_id: uuid.UUID) -> str | None:
    return await db.scalar(
        select(EvalPromotedCase.id).where(
            EvalPromotedCase.source_message_id == message_id
        )
    )


async def mark_message_promoted(
    db: AsyncSession,
    message_id: uuid.UUID,
    case_id: str,
    actor: str,
    now: datetime,
    note: str | None,
) -> list[uuid.UUID]:
    """Flip every open (new/reviewed) rating of the message to promoted."""
    values: dict[str, Any] = {
        "triage_status": TriageStatus.PROMOTED.value,
        "promoted_case_id": case_id,
        "reviewed_by": actor,
        "reviewed_at": now,
        "version": MessageFeedback.version + 1,
    }
    if note is not None:
        values["review_note"] = note
    result = await db.execute(
        update(MessageFeedback)
        .where(
            MessageFeedback.message_id == message_id,
            MessageFeedback.triage_status.in_(
                [TriageStatus.NEW.value, TriageStatus.REVIEWED.value]
            ),
        )
        .values(**values)
        .returning(MessageFeedback.id)
        .execution_options(synchronize_session=False)
    )
    return [r[0] for r in result.all()]


# ---------------------------------------------------------------------------
# Golden cases
# ---------------------------------------------------------------------------


async def list_promoted_cases(
    db: AsyncSession, limit: int, offset: int
) -> tuple[list[EvalPromotedCase], int]:
    total = int(
        (await db.scalar(select(func.count()).select_from(EvalPromotedCase))) or 0
    )
    rows = (
        await db.execute(
            select(EvalPromotedCase)
            .order_by(EvalPromotedCase.promoted_at.desc(), EvalPromotedCase.id)
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    return list(rows), total


# ---------------------------------------------------------------------------
# Export (server-side cursor, flat memory)
# ---------------------------------------------------------------------------


async def stream_pair_rows(
    db: AsyncSession,
    *,
    triage_status: TriageStatus | None,
    include_dismissed: bool,
    batch_size: int,
) -> AsyncIterator[Any]:
    ev = latest_eval()
    stmt = (
        join_exchange(
            select(
                MessageFeedback.id,
                MessageFeedback.message_id,
                MessageFeedback.rating,
                MessageFeedback.reason_tags,
                MessageFeedback.corrected_answer,
                MessageFeedback.triage_status,
                UserMessage.content.label("prompt"),
                ChatMessageRecord.content.label("answer"),
                ev.c.retrieved_chunks,
            ).select_from(MessageFeedback)
        )
        .outerjoin(ev, true())
        .order_by(MessageFeedback.created_at, MessageFeedback.id)
        .execution_options(yield_per=batch_size)
    )
    if triage_status is not None:
        stmt = stmt.where(MessageFeedback.triage_status == triage_status.value)
    elif not include_dismissed:
        stmt = stmt.where(MessageFeedback.triage_status != TriageStatus.DISMISSED.value)
    result = await db.stream(stmt)
    async for row in result:
        yield row
