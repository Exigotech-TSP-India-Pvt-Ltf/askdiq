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
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.database import release_connection
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


def _sort_columns(
    sort: FeedbackSort, columns: Sequence[Any] | None
) -> list[tuple[Any, bool]]:
    """(column, descending) of ``sort``; ``columns`` replaces the table columns
    (same order), e.g. with a subquery's."""
    keys = _SORT_KEYS[sort]
    if columns is None:
        return list(keys)
    return [(c, desc) for c, (_col, desc) in zip(columns, keys, strict=True)]


def _keyset_condition(
    sort: FeedbackSort, values: Sequence[Any], columns: Sequence[Any] | None = None
) -> ColumnElement:
    """Rows strictly after ``values`` in ``sort`` order (mixed directions ok)."""
    keys = _sort_columns(sort, columns)
    branches = []
    for i, (col, desc) in enumerate(keys):
        prefix = [keys[j][0] == values[j] for j in range(i)]
        step = col < values[i] if desc else col > values[i]
        branches.append(and_(*prefix, step))
    return or_(*branches)


def _order_by(sort: FeedbackSort, columns: Sequence[Any] | None = None) -> list[Any]:
    return [
        col.desc() if desc else col.asc() for col, desc in _sort_columns(sort, columns)
    ]


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


def _needs_exchange(f: FeedbackFilters) -> bool:
    """True when a filter reads the joined exchange (audience IP, outcome,
    grounding or the question text); otherwise feedback columns suffice."""
    return (
        f.audience_cond is not None
        or f.outcome is not None
        or f.grounded is not None
        or bool(f.q and f.q.strip())
    )


def _filtered(stmt: Select, f: FeedbackFilters) -> Select:
    """``stmt`` over ``message_feedback`` with the filters; the exchange joins
    (all to-one) are added only when a filter needs them."""
    stmt = join_exchange(stmt) if _needs_exchange(f) else stmt
    conds = _conditions(f)
    return stmt.where(*conds) if conds else stmt


async def list_feedback(
    db: AsyncSession,
    filters: FeedbackFilters,
    *,
    sort: FeedbackSort,
    limit: int,
    offset: int = 0,
    cursor_values: Sequence[Any] | None = None,
) -> tuple[list[Any], int, bool]:
    """(rows, filtered total, has_more), usually in one statement.

    The matching rows' sort keys are collected once (a CTE): the count and
    the page both read it, the page is cut on the keys alone (no per-row
    lateral), and only its rows are enriched. The exchange joins are added
    to the match only when a filter needs them. A second statement (the
    count) runs only for an empty page after the first.
    """
    keys = [col for col, _desc in _SORT_KEYS[sort]]
    matched = _filtered(select(*keys).select_from(MessageFeedback), filters).cte(
        "matched"
    )
    key_cols = [matched.c[col.key] for col in keys]
    page_stmt = select(*key_cols)
    if cursor_values is not None:
        page_stmt = page_stmt.where(_keyset_condition(sort, cursor_values, key_cols))
    else:
        page_stmt = page_stmt.offset(offset)
    page = (
        page_stmt.order_by(*_order_by(sort, key_cols)).limit(limit + 1).subquery("page")
    )
    total_col = (
        select(func.count()).select_from(matched).scalar_subquery().label("total_count")
    )
    ev = latest_eval()
    stmt = (
        join_exchange(
            select(*_item_columns(ev), total_col)
            .select_from(page)
            .join(MessageFeedback, MessageFeedback.id == page.c.id)
        )
        .outerjoin(ev, true())
        .order_by(*_order_by(sort))
    )

    rows = list((await db.execute(stmt)).all())
    has_more = len(rows) > limit
    if rows:
        total = int(rows[0].total_count)
    elif cursor_values is None and offset == 0:
        total = 0
    else:
        count_stmt = _filtered(
            select(func.count(MessageFeedback.id)).select_from(MessageFeedback),
            filters,
        )
        total = int((await db.scalar(count_stmt)) or 0)
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
    keep_details: bool = False,
) -> Any:
    """One row per (message, actor): insert, or replace the user's fields.

    ``keep_details`` leaves an existing row's ``reason_tags`` and
    ``corrected_answer`` untouched (the client did not send them).
    """
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
    set_: dict[str, Any] = {
        "rating": stmt.excluded.rating,
        "comment": stmt.excluded.comment,
        "updated_at": stmt.excluded.updated_at,
        "version": MessageFeedback.version + 1,
    }
    if not keep_details:
        set_["reason_tags"] = stmt.excluded.reason_tags
        set_["corrected_answer"] = stmt.excluded.corrected_answer
    stmt = stmt.on_conflict_do_update(
        constraint="uq_message_feedback_message_actor", set_=set_
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
    db: AsyncSession,
    limit: int,
    offset: int,
    *,
    window: tuple[datetime, datetime] | None = None,
) -> tuple[list[EvalPromotedCase], int]:
    """Golden cases, newest first; ``window`` keeps cases promoted in ``[start, end)``."""
    conds = (
        [
            EvalPromotedCase.promoted_at >= window[0],
            EvalPromotedCase.promoted_at < window[1],
        ]
        if window
        else []
    )
    total = int(
        (
            await db.scalar(
                select(func.count()).select_from(EvalPromotedCase).where(*conds)
            )
        )
        or 0
    )
    rows = (
        await db.execute(
            select(EvalPromotedCase)
            .where(*conds)
            .order_by(EvalPromotedCase.promoted_at.desc(), EvalPromotedCase.id)
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    return list(rows), total


# ---------------------------------------------------------------------------
# Export (server-side cursor, flat memory)
# ---------------------------------------------------------------------------


def pairs_stmt(
    *,
    triage_status: TriageStatus | None,
    include_dismissed: bool,
    window: tuple[datetime, datetime] | None,
    corrected_only: bool,
) -> Select:
    """Pair rows oldest first; the question and the eval record are per-row
    ``LATERAL`` lookups.

    A plain join to the question lets the planner, misled by a missing or
    stale ``role`` estimate (a fresh table, a bulk load), filter every user
    message once per feedback row (``rows x messages``, minutes at 10k). The
    ``LIMIT 1`` lateral can only be a parameterised probe of
    ``ix_chat_messages_session_turn``: one index lookup per row, whatever the
    statistics say.
    """
    ev = latest_eval()
    question = (
        select(UserMessage.content.label("prompt"))
        .where(
            UserMessage.session_id == ChatMessageRecord.session_id,
            UserMessage.turn_index == ChatMessageRecord.turn_index,
            UserMessage.role == "user",
        )
        .limit(1)
        .lateral("question")
    )
    stmt = (
        select(
            MessageFeedback.id,
            MessageFeedback.message_id,
            MessageFeedback.rating,
            MessageFeedback.reason_tags,
            MessageFeedback.corrected_answer,
            MessageFeedback.triage_status,
            MessageFeedback.created_at,
            question.c.prompt,
            ChatMessageRecord.content.label("answer"),
            ev.c.retrieved_chunks,
        )
        .select_from(MessageFeedback)
        .outerjoin(
            ChatMessageRecord, MessageFeedback.message_id == ChatMessageRecord.id
        )
        .outerjoin(question, true())
        .outerjoin(ev, true())
        .order_by(MessageFeedback.created_at, MessageFeedback.id)
    )
    if triage_status is not None:
        stmt = stmt.where(MessageFeedback.triage_status == triage_status.value)
    elif not include_dismissed:
        stmt = stmt.where(MessageFeedback.triage_status != TriageStatus.DISMISSED.value)
    if window is not None:
        stmt = stmt.where(
            MessageFeedback.created_at >= window[0],
            MessageFeedback.created_at < window[1],
        )
    if corrected_only:
        # A complete pair needs a reviewer answer (chosen) and the bot's (rejected).
        stmt = stmt.where(
            MessageFeedback.corrected_answer.is_not(None),
            MessageFeedback.corrected_answer != "",
        )
    return stmt


def pairs_page(
    stmt: Select, after: tuple[datetime, uuid.UUID] | None, batch_size: int
) -> Select:
    """The ``batch_size`` pair rows of ``stmt`` strictly after ``after``."""
    if after is not None:
        stmt = stmt.where(
            tuple_(MessageFeedback.created_at, MessageFeedback.id) > after
        )
    return stmt.limit(batch_size)


async def iter_pair_batches(
    db: AsyncSession,
    *,
    triage_status: TriageStatus | None,
    include_dismissed: bool,
    batch_size: int,
    window: tuple[datetime, datetime] | None = None,
    corrected_only: bool = False,
) -> AsyncIterator[list[Any]]:
    """Pair rows oldest first, in keyset pages of ``batch_size``.

    The read transaction ends after every page, so no pooled connection is
    held while the caller waits (a slow or vanished download client cannot
    pin one). The cursor is a row comparison on ``(created_at, id)`` (unique,
    so ties advance), which ``ix_message_feedback_created_id`` can serve as
    one range, unlike the equivalent ``OR``.
    """
    stmt = pairs_stmt(
        triage_status=triage_status,
        include_dismissed=include_dismissed,
        window=window,
        corrected_only=corrected_only,
    )
    after: tuple[datetime, uuid.UUID] | None = None
    while True:
        rows = list((await db.execute(pairs_page(stmt, after, batch_size))).all())
        await release_connection(db)
        if rows:
            yield rows
        if len(rows) < batch_size:
            return
        after = (rows[-1].created_at, rows[-1].id)
