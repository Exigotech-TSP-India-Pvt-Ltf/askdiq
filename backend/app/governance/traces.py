"""Governance Traces: filtered keyset list and per-request detail (newplan §4.2).

A trace is one ``latency_metrics`` row. The assistant turn (outcome, flags,
tokens, cost) and the ``query`` audit row (actor, IP, redacted query, sources)
are attached with ``LEFT JOIN LATERAL ... LIMIT 1`` so duplicates can never
multiply rows and keyset paging stays stable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    ColumnElement,
    Select,
    Text,
    case,
    cast,
    func,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.governance.audience import build_audience_filter
from app.governance.monitoring import (
    BLOCKED_FLAGS,
    FAILED_FLAGS,
    LATENCY_UNIT,
    QUERY_ACTION,
    STAGE_FIELDS,
    UNGROUNDED_FLAGS,
    derive_trace_status,
)
from app.governance.outcomes import LEGACY_OUTCOME_MAP
from app.governance.pagination import Cursor, Total, bounded_total, keyset_after
from app.governance.usage_queries import escape_ilike_pattern
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    Chunk,
    Document,
    LatencyRecord,
)

LATENCY_BUCKETS: tuple[str, ...] = ("fast", "moderate", "slow", "very_slow")
TRACE_STATUSES: tuple[str, ...] = ("ok", "ungrounded", "blocked", "failed", "unknown")
# Waterfall order of the pipeline stages.
WATERFALL_ORDER: tuple[str, ...] = (
    "guardrail_pre",
    "embedding",
    "retrieval",
    "reranker",
    "llm",
    "guardrail_post",
)
_QUERY_PREVIEW = 200


def _text_array(values: frozenset[str] | set[str]) -> Any:
    return cast(array(sorted(values), type_=Text), ARRAY(Text))


def bucket_bounds() -> list[int]:
    return sorted(get_settings().governance_latency_buckets_ms)[:3]


def latency_bucket(total_ms: float | None) -> str | None:
    """fast / moderate / slow / very_slow by the configured upper bounds."""
    if total_ms is None:
        return None
    for name, upper in zip(LATENCY_BUCKETS, bucket_bounds(), strict=False):
        if total_ms < upper:
            return name
    return LATENCY_BUCKETS[-1]


def bucket_condition(name: str) -> ColumnElement[bool]:
    bounds = bucket_bounds()
    index = LATENCY_BUCKETS.index(name)
    lower = bounds[index - 1] if index > 0 else None
    upper = bounds[index] if index < len(bounds) else None
    conds = []
    if lower is not None:
        conds.append(LatencyRecord.total_ms >= lower)
    if upper is not None:
        conds.append(LatencyRecord.total_ms < upper)
    return conds[0] if len(conds) == 1 else conds[0] & conds[1]


def normalized_outcome_sql(outcome: Any) -> Any:
    lowered = func.lower(outcome)
    return case(
        *((lowered == raw, norm) for raw, norm in sorted(LEGACY_OUTCOME_MAP.items())),
        else_=None,
    )


def status_sql(outcome: Any, flags: Any) -> Any:
    """SQL mirror of ``monitoring.derive_trace_status``."""
    norm = normalized_outcome_sql(outcome)
    flags_j = cast(flags, JSONB)
    flags_missing = func.coalesce(func.jsonb_typeof(flags_j), "null") == "null"

    def has(names: frozenset[str]) -> Any:
        return func.coalesce(flags_j.has_any(_text_array(names)), False)

    return case(
        (or_(norm == "llm_failed", has(FAILED_FLAGS)), "failed"),
        (norm == "blocked", "blocked"),
        (norm == "ungrounded", "ungrounded"),
        (norm.in_(("answered", "conversational")), "ok"),
        (outcome.is_(None) & flags_missing, "unknown"),
        (has(BLOCKED_FLAGS), "blocked"),
        (has(UNGROUNDED_FLAGS), "ungrounded"),
        else_="ok",
    )


@dataclass(frozen=True, slots=True)
class TraceFilters:
    audience: str = "overview"
    statuses: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()
    latency_buckets: tuple[str, ...] = ()
    q: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


def _laterals() -> tuple[Any, Any]:
    turn = (
        select(
            ChatMessageRecord.session_id,
            ChatMessageRecord.outcome,
            ChatMessageRecord.guardrail_flags,
            ChatMessageRecord.grounded,
            ChatMessageRecord.prompt_tokens,
            ChatMessageRecord.completion_tokens,
            ChatMessageRecord.reasoning_tokens,
            ChatMessageRecord.embedding_tokens,
            ChatMessageRecord.guardrail_tokens,
            ChatMessageRecord.total_tokens,
            ChatMessageRecord.cost_usd,
        )
        .where(
            ChatMessageRecord.request_id == LatencyRecord.request_id,
            ChatMessageRecord.role == "assistant",
        )
        .order_by(ChatMessageRecord.created_at.desc())
        .limit(1)
        .lateral("turn")
    )
    audit = (
        select(
            AuditLog.actor,
            AuditLog.ip_address,
            AuditLog.detail,
        )
        .where(
            AuditLog.request_id == LatencyRecord.request_id,
            AuditLog.action == QUERY_ACTION,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
        .lateral("qa")
    )
    return turn, audit


async def _base(db: AsyncSession, f: TraceFilters, *columns: Any) -> Select:
    turn, audit = _laterals()
    cols = columns or (
        LatencyRecord,
        turn.c.session_id,
        turn.c.outcome,
        turn.c.guardrail_flags,
        turn.c.grounded,
        turn.c.prompt_tokens,
        turn.c.completion_tokens,
        turn.c.reasoning_tokens,
        turn.c.embedding_tokens,
        turn.c.guardrail_tokens,
        turn.c.total_tokens,
        turn.c.cost_usd,
        audit.c.actor,
        audit.c.ip_address,
        audit.c.detail,
    )
    stmt = (
        select(*cols)
        .select_from(LatencyRecord)
        .outerjoin(turn, true())
        .outerjoin(audit, true())
    )
    if f.audience != "overview":
        aud = await build_audience_filter(db, audit.c.ip_address, f.audience)
        if aud is not None:
            # Same scope as before: a trace belongs to an audience via its
            # query audit row.
            stmt = stmt.where(audit.c.actor.isnot(None), aud)
    if f.date_from is not None:
        stmt = stmt.where(LatencyRecord.created_at >= f.date_from)
    if f.date_to is not None:
        stmt = stmt.where(LatencyRecord.created_at <= f.date_to)
    if f.statuses:
        stmt = stmt.where(
            status_sql(turn.c.outcome, turn.c.guardrail_flags).in_(f.statuses)
        )
    if f.outcomes:
        stmt = stmt.where(normalized_outcome_sql(turn.c.outcome).in_(f.outcomes))
    if f.latency_buckets:
        stmt = stmt.where(or_(*(bucket_condition(b) for b in f.latency_buckets)))
    if f.q:
        pattern = f"%{escape_ilike_pattern(f.q)}%"
        stmt = stmt.where(
            or_(
                cast(audit.c.detail, JSONB)["query"].astext.ilike(pattern, escape="\\"),
                LatencyRecord.request_id.ilike(pattern, escape="\\"),
            )
        )
    return stmt


def _ordered(stmt: Select) -> Select:
    return stmt.order_by(LatencyRecord.created_at.desc(), LatencyRecord.id.desc())


async def fetch_page(
    db: AsyncSession, f: TraceFilters, cursor: Cursor | None, limit: int
) -> tuple[list[dict[str, Any]], Cursor | None]:
    stmt = await _base(db, f)
    after = keyset_after(LatencyRecord.created_at, LatencyRecord.id, cursor)
    if after is not None:
        stmt = stmt.where(after)
    rows = (await db.execute(_ordered(stmt).limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        last = rows[-1][0]
        next_cursor = Cursor(created_at=last.created_at, id=last.id)
    return [to_item(row) for row in rows], next_cursor


async def count(db: AsyncSession, f: TraceFilters, cap: int) -> Total:
    return await bounded_total(db, await _base(db, f, LatencyRecord.id), cap)


def slowest_stage(record: LatencyRecord) -> dict[str, Any] | None:
    timings = {
        stage: value
        for stage, field_name in STAGE_FIELDS.items()
        if (value := getattr(record, field_name)) is not None
    }
    if not timings:
        return None
    stage = max(timings, key=lambda s: timings[s])
    return {"stage": stage, "ms": timings[stage], "unit": LATENCY_UNIT}


def _detail_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def to_item(row: Any) -> dict[str, Any]:
    record: LatencyRecord = row[0]
    has_turn = row.session_id is not None
    flags = row.guardrail_flags if has_turn else None
    detail = _detail_dict(row.detail)
    query = detail.get("query")
    return {
        "request_id": record.request_id,
        "user_id": row.actor,
        "conversation_id": str(row.session_id) if has_turn else None,
        "ip_address": row.ip_address,
        "timestamp": record.created_at.isoformat() + "Z",
        "status": derive_trace_status(row.outcome if has_turn else None, flags),
        "outcome": row.outcome if has_turn else None,
        "guardrail_flags": list(flags or []),
        "total_ms": record.total_ms,
        "latency_unit": LATENCY_UNIT,
        "latency_bucket": latency_bucket(record.total_ms),
        "slowest_stage": slowest_stage(record),
        "query": str(query)[:_QUERY_PREVIEW] if query else None,
        "tokens": {
            "prompt": row.prompt_tokens,
            "completion": row.completion_tokens,
            "reasoning": row.reasoning_tokens,
            "embedding": (row.embedding_tokens or 0) + (row.guardrail_tokens or 0)
            if has_turn
            else None,
            "total": row.total_tokens,
        },
        "cost_usd": row.cost_usd,
    }


def waterfall(record: LatencyRecord) -> dict[str, Any]:
    """Sequential stage offsets for the trace drawer's waterfall chart."""
    stages = []
    offset = 0.0
    for stage in WATERFALL_ORDER:
        duration = getattr(record, STAGE_FIELDS[stage])
        stages.append(
            {
                "stage": stage,
                "start_ms": offset if duration is not None else None,
                "duration_ms": duration,
            }
        )
        offset += duration or 0.0
    return {
        "unit": LATENCY_UNIT,
        "total_ms": record.total_ms,
        "stages": stages,
        "other_ms": max(0.0, record.total_ms - offset),
    }


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _sources(db: AsyncSession, detail: dict[str, Any]) -> list[dict[str, Any]]:
    raw = detail.get("sources")
    entries: list[dict[str, Any]] = []
    if isinstance(raw, list) and raw:
        entries = [
            {
                "chunk_id": str(s.get("chunk_id")),
                "document_id": s.get("document_id"),
                "score": s.get("score"),
                "source_name": s.get("source_name"),
            }
            for s in raw
            if isinstance(s, dict) and s.get("chunk_id")
        ]
    else:
        entries = [
            {
                "chunk_id": str(c),
                "document_id": None,
                "score": None,
                "source_name": None,
            }
            for c in detail.get("source_ids") or []
        ]
    chunk_ids = {u for e in entries if (u := _uuid_or_none(e["chunk_id"]))}
    if chunk_ids:
        rows = (
            await db.execute(
                select(Chunk.id, Document.id, Document.source_name)
                .join(Document, Chunk.document_id == Document.id)
                .where(Chunk.id.in_(chunk_ids))
            )
        ).all()
        known = {str(c): (str(d), name) for c, d, name in rows}
        for entry in entries:
            if entry["chunk_id"] in known:
                doc_id, name = known[entry["chunk_id"]]
                entry["document_id"] = entry["document_id"] or doc_id
                entry["source_name"] = entry["source_name"] or name
    return entries


async def fetch_detail(
    db: AsyncSession, request_id: str, audience: str
) -> dict[str, Any] | None:
    """Full trace for one request, or None when unknown / outside the audience."""
    stmt = await _base(db, TraceFilters(audience=audience))
    stmt = _ordered(stmt.where(LatencyRecord.request_id == request_id)).limit(1)
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    item = to_item(row)
    detail = _detail_dict(row.detail)
    item.update(
        {
            "grounded": row.grounded if row.session_id is not None else None,
            "stages": waterfall(row[0]),
            "sources": await _sources(db, detail),
            "flags": item["guardrail_flags"],
        }
    )
    return item
