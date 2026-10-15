"""Governance monitoring and trace helpers (newplan §4.1 G1-G7, G9).

Everything here reads persisted rows (``latency_metrics``, ``chat_messages``,
``audit_log``) so the numbers survive restarts and respect the audience
scope, instead of the process-local ``METRICS`` counters.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.audience import build_audience_filter
from app.governance.outcomes import LEGACY_OUTCOME_MAP
from app.models.db_models import AuditLog, ChatMessageRecord, LatencyRecord

LATENCY_UNIT = "ms"

# Public stage name -> LatencyRecord column. All values are milliseconds.
STAGE_FIELDS: dict[str, str] = {
    "embedding": "embedding_ms",
    "retrieval": "retrieval_ms",
    "reranker": "reranker_ms",
    "llm": "llm_ms",
    "guardrail_pre": "guardrail_pre_ms",
    "guardrail_post": "guardrail_post_ms",
}

TraceStatus = Literal["ok", "ungrounded", "blocked", "failed", "unknown"]

_FAILED_FLAGS = frozenset({"llm_call_failed", "empty_llm_output"})
_BLOCKED_FLAGS = frozenset(
    {
        "unsafe_input_detected",
        "spam_detected",
        "injection_detected",
        "indirect_injection_detected",
        "secret_leak_detected",
        "query_too_long",
        "empty_query",
    }
)
_UNGROUNDED_FLAGS = frozenset(
    {
        "groundedness_check_failed",
        "possible_hallucination",
        "unsupported_numeric_claim",
        "low_retrieval_confidence",
        "no_retrieved_sources",
    }
)
_FAILED_OUTCOMES = ("llm_failed", "empty_llm_output")

# The query audit row is the one that carries the client IP for a request;
# other rows (llm_call_failed, rate_limited, online eval...) share the
# request_id, so joins must pin the action to avoid duplicate rows.
QUERY_ACTION = "query"


def labelled(value: Any, window: str, source: str) -> dict[str, Any]:
    """A count plus where it comes from and over which window (G7)."""
    return {"value": value, "window": window, "source": source}


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def stage_latency_summary(
    records: Iterable[LatencyRecord],
) -> dict[str, dict[str, Any]]:
    """Per-stage average in ms; a stage with no samples is ``None`` (G1, G2)."""
    rows = list(records)
    summary: dict[str, dict[str, Any]] = {}
    for stage, field in STAGE_FIELDS.items():
        values = [v for r in rows if (v := getattr(r, field)) is not None]
        summary[stage] = {
            "avg_ms": _mean(values),
            "sample_count": len(values),
            "unit": LATENCY_UNIT,
        }
    return summary


def derive_trace_status(
    outcome: str | None, guardrail_flags: Sequence[str] | None
) -> TraceStatus:
    """Map a turn's persisted outcome + guardrail flags to a trace status (G5).

    ``unknown`` means no assistant message was persisted for the request
    (e.g. a 4xx/5xx before generation).
    """
    flags = set(guardrail_flags or [])
    normalized = LEGACY_OUTCOME_MAP.get(outcome.lower()) if outcome else None
    if normalized == "llm_failed" or flags & _FAILED_FLAGS:
        return "failed"
    if normalized == "blocked":
        return "blocked"
    if normalized == "ungrounded":
        return "ungrounded"
    if normalized in ("answered", "conversational"):
        return "ok"
    if outcome is None and guardrail_flags is None:
        return "unknown"
    # Legacy rows without an outcome: fall back to the flags.
    if flags & _BLOCKED_FLAGS:
        return "blocked"
    if flags & _UNGROUNDED_FLAGS:
        return "ungrounded"
    return "ok"


async def scoped_latency_query(
    db: AsyncSession,
    audience: str,
    base: Select,
) -> Select:
    """Restrict a ``LatencyRecord`` select to the audience via its query audit row."""
    if audience == "overview":
        return base
    aud_filter = await build_audience_filter(db, AuditLog.ip_address, audience)
    if aud_filter is None:
        return base
    return base.join(
        AuditLog,
        (LatencyRecord.request_id == AuditLog.request_id)
        & (AuditLog.action == QUERY_ACTION),
    ).where(aud_filter)


async def count_traces(db: AsyncSession, audience: str) -> int:
    """Real trace total over the same filter as ``/governance/traces`` (G4)."""
    stmt = await scoped_latency_query(
        db, audience, select(func.count(LatencyRecord.id)).select_from(LatencyRecord)
    )
    return int(await db.scalar(stmt) or 0)


async def persisted_error_counts(
    db: AsyncSession, audience: str, since: datetime
) -> dict[str, Any]:
    """DB-derived, audience-scoped error rate (G6).

    errors   = assistant turns with outcome llm_failed + ``server_error`` rows
    requests = assistant turns + ``server_error`` rows
    """
    aud_filter = await build_audience_filter(db, AuditLog.ip_address, audience)

    turns = (
        select(func.count(ChatMessageRecord.id))
        .select_from(ChatMessageRecord)
        .where(
            ChatMessageRecord.role == "assistant",
            ChatMessageRecord.created_at >= since,
        )
    )
    if aud_filter is not None:
        turns = turns.join(
            AuditLog,
            (AuditLog.request_id == ChatMessageRecord.request_id)
            & (AuditLog.action == QUERY_ACTION),
        ).where(aud_filter)
    failed = turns.where(ChatMessageRecord.outcome.in_(_FAILED_OUTCOMES))

    server_errors = select(func.count(AuditLog.id)).where(
        AuditLog.action == "server_error", AuditLog.created_at >= since
    )
    if aud_filter is not None:
        server_errors = server_errors.where(aud_filter)

    turn_count = int(await db.scalar(turns) or 0)
    llm_failed = int(await db.scalar(failed) or 0)
    server_error_count = int(await db.scalar(server_errors) or 0)

    denominator = turn_count + server_error_count
    errors = llm_failed + server_error_count
    return {
        "error_rate": (errors * 100 / denominator) if denominator else 0.0,
        "errors": errors,
        "llm_failed": llm_failed,
        "server_errors": server_error_count,
        "requests": denominator,
    }
