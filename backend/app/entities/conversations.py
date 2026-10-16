"""Conversation stats, list filters and per-turn detail (B7.2).

A turn is one ``chat_messages.turn_index`` (user message + assistant answer).
Outcomes use the canonical names; legacy values are mapped and a NULL
outcome counts as ``answered`` (as ``GET /admin/usage/queries/{id}`` does).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    String,
    case,
    cast,
    exists,
    func,
    literal_column,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import ScopedRunner
from app.dashboard.queries import outcome_sql
from app.dashboard.window import DayRange, iso_z
from app.governance.audience import build_audience_filter
from app.governance.monitoring import QUERY_ACTION, STAGE_FIELDS
from app.governance.outcomes import ALL_OUTCOMES, LEGACY_OUTCOME_MAP
from app.governance.usage_queries import escape_ilike_pattern
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    Chunk,
    Document,
    EvalPromotedCase,
    EvaluationRecord,
    LatencyRecord,
    MessageFeedback,
    TurnInsight,
)

# Inclusive turn-count bins; the last one is open-ended.
TURN_BINS: tuple[tuple[int, int | None], ...] = (
    (1, 1),
    (2, 3),
    (4, 6),
    (7, 9),
    (10, None),
)
STAGE_KEYS: tuple[str, ...] = (
    "guardrail_pre",
    "embedding",
    "retrieval",
    "reranker",
    "llm",
    "guardrail_post",
)


def _day(col: Any) -> Any:
    return func.date_trunc(literal_column("'day'"), col)


async def session_conditions(
    db: AsyncSession, audience: str, ip: str | None, q: str | None
) -> list[ColumnElement[bool]]:
    """The conversation list's audience, IP and search filters on ``chat_sessions``."""
    conds: list[ColumnElement[bool]] = []
    aud = await build_audience_filter(db, ChatSession.created_ip, audience)
    if aud is not None:
        conds.append(aud)
    if ip and ip.strip():
        conds.append(ChatSession.created_ip == ip.strip())
    if q and q.strip():
        pattern = f"%{escape_ilike_pattern(q.strip())}%"
        conds.append(
            or_(
                ChatSession.title.ilike(pattern, escape="\\"),
                cast(ChatSession.id, String).ilike(pattern, escape="\\"),
                cast(ChatSession.created_ip, String).ilike(pattern, escape="\\"),
            )
        )
    return conds


def turn_count_sql() -> Any:
    """Distinct turns of the session being grouped (list query)."""
    return func.count(func.distinct(ChatMessageRecord.turn_index))


def outcome_exists(outcome: str, day: date | None) -> ColumnElement[bool]:
    """The session has an assistant turn of ``outcome`` (on ``day`` when given)."""
    msg = ChatMessageRecord.__table__.alias("outcome_turn")
    conds = [
        msg.c.session_id == ChatSession.id,
        msg.c.role == "assistant",
        outcome_sql(msg.c.outcome) == outcome,
    ]
    if day is not None:
        start = datetime.combine(day, datetime.min.time())
        conds += [
            msg.c.created_at >= start,
            msg.c.created_at < start + timedelta(days=1),
        ]
    return exists(select(msg.c.id).where(*conds))


async def outcome_counts(
    db: AsyncSession, session_ids: Sequence[uuid.UUID]
) -> dict[str, dict[str, int]]:
    """``{session_id: {outcome: count}}`` for one page of conversations."""
    if not session_ids:
        return {}
    outcome = outcome_sql(ChatMessageRecord.outcome)
    stmt = (
        select(ChatMessageRecord.session_id, outcome, func.count())
        .where(
            ChatMessageRecord.session_id.in_(session_ids),
            ChatMessageRecord.role == "assistant",
        )
        .group_by(ChatMessageRecord.session_id, outcome)
    )
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for sid, name, count in (await db.execute(stmt)).all():
        out[str(sid)][str(name)] = int(count)
    return dict(out)


# ---------------------------------------------------------------------------
# GET /admin/conversations/stats
# ---------------------------------------------------------------------------


def _bin_sql(turns: Any) -> Any:
    whens = [
        (turns <= upper, index)
        for index, (_, upper) in enumerate(TURN_BINS)
        if upper is not None
    ]
    return case(*whens, else_=len(TURN_BINS) - 1)


async def turns_histogram(
    db: AsyncSession, conds: list[ColumnElement[bool]], days: DayRange
) -> tuple[dict[int, int], int, int]:
    """Conversations started in the range per turn-count bin, plus totals."""
    per_session = (
        select(
            ChatMessageRecord.session_id.label("sid"),
            func.count(func.distinct(ChatMessageRecord.turn_index)).label("turns"),
        )
        .group_by(ChatMessageRecord.session_id)
        .subquery("per_session")
    )
    scoped = (
        select(per_session.c.turns.label("turns"))
        .select_from(ChatSession)
        .join(per_session, per_session.c.sid == ChatSession.id)
        .where(
            ChatSession.created_at >= days.start,
            ChatSession.created_at < days.end,
            *conds,
        )
        .subquery("scoped")
    )
    bin_index = _bin_sql(scoped.c.turns).label("bin")
    inner = select(bin_index, scoped.c.turns).subquery("binned")
    stmt = select(
        inner.c.bin,
        func.grouping(inner.c.bin),
        func.count(),
        func.coalesce(func.sum(inner.c.turns), 0),
    ).group_by(func.rollup(inner.c.bin))
    bins: dict[int, int] = {}
    conversations = turns = 0
    for row in (await db.execute(stmt)).all():
        if row[1]:
            conversations, turns = int(row[2]), int(row[3])
        else:
            bins[int(row[0])] = int(row[2])
    return bins, conversations, turns


async def outcomes_by_day(
    db: AsyncSession, conds: list[ColumnElement[bool]], days: DayRange
) -> dict[date, dict[str, int]]:
    msg = ChatMessageRecord
    day = _day(msg.created_at)
    outcome = outcome_sql(msg.outcome)
    stmt = (
        select(day, outcome, func.count())
        .select_from(msg)
        .join(ChatSession, ChatSession.id == msg.session_id)
        .where(
            msg.role == "assistant",
            msg.created_at >= days.start,
            msg.created_at < days.end,
            *conds,
        )
        .group_by(day, outcome)
    )
    out: dict[date, dict[str, int]] = defaultdict(dict)
    for d, name, count in (await db.execute(stmt)).all():
        out[d.date()][str(name)] = int(count)
    return dict(out)


async def conversation_stats(
    runner: ScopedRunner,
    db: AsyncSession,
    audience: str,
    days: DayRange,
    ip: str | None,
    q: str | None,
) -> dict[str, Any]:
    conds = await session_conditions(db, audience, ip, q)
    (bins, conversations, turns), by_day = await runner.gather(
        lambda s: turns_histogram(s, conds, days),
        lambda s: outcomes_by_day(s, conds, days),
    )
    return {
        "from": days.first.isoformat(),
        "to": days.last.isoformat(),
        "totals": {
            "conversations": conversations,
            "turns": turns,
            "avg_turns": round(turns / conversations, 2) if conversations else None,
        },
        "turns_histogram": [
            {"min_turns": low, "max_turns": high, "conversations": bins.get(i, 0)}
            for i, (low, high) in enumerate(TURN_BINS)
        ],
        "outcome_by_day": [
            {
                "day": d.isoformat(),
                "counts": {
                    **dict.fromkeys(ALL_OUTCOMES, 0),
                    **by_day.get(d, {}),
                },
            }
            for d in days.days()
        ],
    }


# ---------------------------------------------------------------------------
# GET /admin/conversations/{id}?turn= (per-turn detail)
# ---------------------------------------------------------------------------


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _int_or_none(value: Any) -> int | None:
    return (
        int(value) if isinstance(value, int) and not isinstance(value, bool) else None
    )


async def _latency(db: AsyncSession, request_ids: set[str]) -> dict[str, LatencyRecord]:
    if not request_ids:
        return {}
    rows = await db.execute(
        select(LatencyRecord)
        .where(LatencyRecord.request_id.in_(request_ids))
        .order_by(LatencyRecord.created_at)
    )
    return {r.request_id: r for r in rows.scalars().all()}


async def _feedback(
    db: AsyncSession, message_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[MessageFeedback]]:
    if not message_ids:
        return {}
    rows = await db.execute(
        select(MessageFeedback)
        .where(MessageFeedback.message_id.in_(message_ids))
        .order_by(MessageFeedback.created_at, MessageFeedback.id)
    )
    out: dict[uuid.UUID, list[MessageFeedback]] = defaultdict(list)
    for fb in rows.scalars().all():
        out[fb.message_id].append(fb)
    return out


async def _labels(
    db: AsyncSession, message_ids: set[uuid.UUID]
) -> dict[uuid.UUID, TurnInsight]:
    if not message_ids:
        return {}
    rows = await db.execute(
        select(TurnInsight).where(TurnInsight.message_id.in_(message_ids))
    )
    return {r.message_id: r for r in rows.scalars().all()}


async def _promoted(
    db: AsyncSession, message_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not message_ids:
        return {}
    rows = await db.execute(
        select(EvalPromotedCase.source_message_id, EvalPromotedCase.id).where(
            EvalPromotedCase.source_message_id.in_(message_ids)
        )
    )
    return {mid: case_id for mid, case_id in rows.all() if mid is not None}


async def _raw_sources(
    db: AsyncSession, message_ids: set[uuid.UUID], request_ids: set[str]
) -> tuple[dict[uuid.UUID, list[dict]], dict[str, list[dict]]]:
    """Retrieved chunks from online evaluation, else the query audit row."""
    by_message: dict[uuid.UUID, list[dict]] = {}
    by_request: dict[str, list[dict]] = {}
    if message_ids:
        rows = await db.execute(
            select(EvaluationRecord.message_id, EvaluationRecord.retrieved_chunks)
            .where(EvaluationRecord.message_id.in_(message_ids))
            .order_by(EvaluationRecord.created_at)
        )
        for mid, chunks in rows.all():
            if isinstance(chunks, list) and chunks:
                by_message[mid] = [c for c in chunks if isinstance(c, dict)]
    if request_ids:
        rows = await db.execute(
            select(AuditLog.request_id, AuditLog.detail).where(
                AuditLog.request_id.in_(request_ids), AuditLog.action == QUERY_ACTION
            )
        )
        for rid, detail in rows.all():
            raw = detail.get("sources") if isinstance(detail, dict) else None
            if isinstance(raw, list) and raw:
                by_request[rid] = [s for s in raw if isinstance(s, dict)]
    return by_message, by_request


async def _chunks(db: AsyncSession, chunk_ids: set[uuid.UUID]) -> dict[str, Any]:
    if not chunk_ids:
        return {}
    rows = await db.execute(
        select(
            Chunk.id,
            Chunk.chunk_index,
            Chunk.chunk_metadata,
            Document.id,
            Document.source_name,
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(chunk_ids))
    )
    return {str(r[0]): r for r in rows.all()}


def _source(raw: dict, chunk: Any) -> dict[str, Any]:
    meta = (chunk[2] or {}) if chunk is not None else {}
    heading = raw.get("section_heading") or (
        meta.get("section_heading") if isinstance(meta, dict) else None
    )
    chunk_id = str(raw.get("chunk_id") or "") or None
    return {
        "document_id": str(chunk[3])
        if chunk is not None
        else (str(raw["document_id"]) if raw.get("document_id") else None),
        "chunk_id": chunk_id,
        "chunk_index": int(chunk[1]) if chunk is not None else None,
        "source_name": (chunk[4] if chunk is not None else None)
        or raw.get("source_name"),
        "section_heading": heading or None,
        "score": _number(raw.get("score")),
    }


def _tokens(asst: ChatMessageRecord) -> dict[str, int] | None:
    if asst.prompt_tokens is None and asst.completion_tokens is None:
        return None
    prompt = asst.prompt_tokens or 0
    completion = asst.completion_tokens or 0
    embedding = asst.embedding_tokens or 0
    guardrail = asst.guardrail_tokens or 0
    details = asst.token_details if isinstance(asst.token_details, dict) else {}
    cached = _int_or_none(details.get("cached_tokens")) or 0
    return {
        "prompt": prompt,
        "cached_prompt": min(cached, prompt),
        "completion": completion,
        "reasoning": asst.reasoning_tokens or 0,
        "embedding": embedding,
        "guardrail": guardrail,
        "total": (asst.total_tokens or prompt + completion) + embedding + guardrail,
    }


def _stages(record: LatencyRecord | None) -> dict[str, float | None]:
    return {
        key: (getattr(record, STAGE_FIELDS[key]) if record is not None else None)
        for key in STAGE_KEYS
    }


def _outcome(raw: str | None) -> str | None:
    if raw is None:
        return None
    return LEGACY_OUTCOME_MAP.get(raw.lower(), raw)


@dataclass(slots=True)
class TurnContext:
    """Everything joined onto a conversation's assistant messages."""

    latency: dict[str, LatencyRecord] = field(default_factory=dict)
    feedback: dict[uuid.UUID, list[MessageFeedback]] = field(default_factory=dict)
    labels: dict[uuid.UUID, TurnInsight] = field(default_factory=dict)
    promoted: dict[uuid.UUID, str] = field(default_factory=dict)
    sources: dict[uuid.UUID, list[dict]] = field(default_factory=dict)
    chunks: dict[str, Any] = field(default_factory=dict)


async def load_context(
    db: AsyncSession, assistants: Sequence[ChatMessageRecord]
) -> TurnContext:
    """One bounded IN-list query per related table (no per-turn queries)."""
    asst_ids = {a.id for a in assistants}
    request_ids = {a.request_id for a in assistants if a.request_id}
    by_message, by_request = await _raw_sources(db, asst_ids, request_ids)
    sources = {
        a.id: by_message.get(a.id) or by_request.get(a.request_id or "", [])
        for a in assistants
    }
    chunk_ids = {
        u for raw in sources.values() for s in raw if (u := _uuid(s.get("chunk_id")))
    }
    return TurnContext(
        latency=await _latency(db, request_ids),
        feedback=await _feedback(db, asst_ids),
        labels=await _labels(db, asst_ids),
        promoted=await _promoted(db, asst_ids),
        sources=sources,
        chunks=await _chunks(db, chunk_ids),
    )


def _feedback_item(f: MessageFeedback) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "rating": f.rating,
        "comment": f.comment,
        "reason_tags": [str(t) for t in (f.reason_tags or [])],
        "triage_status": f.triage_status,
        "created_at": iso_z(f.created_at),
    }


def _labels_item(label: TurnInsight | None) -> dict[str, str | None] | None:
    if label is None:
        return None
    return {"intent": label.intent, "quality": label.bot_quality, "issue": label.issue}


def turn_item(
    index: int,
    user: ChatMessageRecord | None,
    asst: ChatMessageRecord | None,
    ctx: TurnContext,
) -> dict[str, Any]:
    """One ``turns[]`` entry; every assistant-derived field is null without a reply."""
    first = user or asst
    item: dict[str, Any] = {
        "turn_index": index,
        "request_id": None,
        "user_message_id": str(user.id) if user else None,
        "assistant_message_id": None,
        "created_at": iso_z(first.created_at) if first and first.created_at else None,
        "outcome": None,
        "grounded": None,
        "stages_ms": _stages(None),
        "total_ms": None,
        "tokens": None,
        "cost_usd": None,
        "sources": [],
        "feedback": [],
        "labels": None,
        "promoted_case_id": None,
    }
    if asst is None:
        return item
    record = ctx.latency.get(asst.request_id) if asst.request_id else None
    feedback = ctx.feedback.get(asst.id, [])
    promoted = ctx.promoted.get(asst.id) or next(
        (f.promoted_case_id for f in feedback if f.promoted_case_id), None
    )
    item.update(
        {
            "request_id": asst.request_id,
            "assistant_message_id": str(asst.id),
            "outcome": _outcome(asst.outcome),
            "grounded": asst.grounded,
            "stages_ms": _stages(record),
            "total_ms": record.total_ms if record else None,
            "tokens": _tokens(asst),
            "cost_usd": asst.cost_usd,
            "sources": [
                _source(s, ctx.chunks.get(str(s.get("chunk_id"))))
                for s in ctx.sources.get(asst.id, [])
            ],
            "feedback": [_feedback_item(f) for f in feedback],
            "labels": _labels_item(ctx.labels.get(asst.id)),
            "promoted_case_id": promoted,
        }
    )
    return item


async def turn_details(
    db: AsyncSession, messages: Iterable[ChatMessageRecord]
) -> list[dict[str, Any]]:
    """One item per ``turn_index`` (ascending) with timings, tokens, cost,
    sources, feedback, insight labels and the promoted golden case."""
    turns: dict[int, dict[str, ChatMessageRecord]] = defaultdict(dict)
    for message in messages:
        turns[message.turn_index].setdefault(message.role, message)
    ctx = await load_context(
        db, [t["assistant"] for t in turns.values() if "assistant" in t]
    )
    return [
        turn_item(index, turns[index].get("user"), turns[index].get("assistant"), ctx)
        for index in sorted(turns)
    ]
