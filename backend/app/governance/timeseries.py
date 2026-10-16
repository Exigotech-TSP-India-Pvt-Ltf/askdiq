"""Persisted, audience-scoped governance aggregates (newplan §4.2, B4.2.3/4).

Every function issues exactly one SQL statement, so callers can run them in
parallel with ``gather_scoped`` (one session per task) and the query count of
``/governance/overview`` and ``/governance/monitoring`` is fixed:

- per-stage p50/p95/p99 with ``percentile_cont`` (one ordered-set query),
- 24 zero-filled hourly buckets (``GROUP BY ROLLUP`` also yields the window
  total in the same statement),
- guardrail flag breakdown and outcome mix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    ARRAY,
    Float,
    Select,
    case,
    cast,
    func,
    literal,
    literal_column,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.audience import build_audience_filter
from app.governance.monitoring import LATENCY_UNIT, QUERY_ACTION, STAGE_FIELDS
from app.governance.traces import normalized_outcome_sql, status_sql
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    LatencyRecord,
)

HOURS = 24
PERCENTILES: tuple[float, ...] = (0.5, 0.95, 0.99)
FAILED_OUTCOMES: tuple[str, ...] = ("llm_failed", "empty_llm_output")


def utcnow() -> datetime:
    """Naive UTC, matching the ``DateTime`` columns (``datetime.utcnow`` writes)."""
    return datetime.utcnow()  # noqa: DTZ003 - DB columns are naive UTC


def hour_starts(now: datetime, hours: int = HOURS) -> list[datetime]:
    """``hours`` hourly bucket starts, oldest first, ending with the current hour."""
    current = now.replace(minute=0, second=0, microsecond=0)
    return [current - timedelta(hours=hours - 1 - i) for i in range(hours)]


def _hour(col: Any) -> Any:
    # Inline unit: a bound "hour" would differ between SELECT and GROUP BY.
    return func.date_trunc(literal_column("'hour'"), col)


def _pct_array(col: Any) -> Any:
    return func.percentile_cont(
        cast(literal(list(PERCENTILES), ARRAY(Float)), ARRAY(Float))
    ).within_group(col)


async def scope_latency(db: AsyncSession, audience: str, stmt: Select) -> Select:
    if audience == "overview":
        return stmt
    aud = await build_audience_filter(db, AuditLog.ip_address, audience)
    if aud is None:
        return stmt
    return stmt.join(
        AuditLog,
        (LatencyRecord.request_id == AuditLog.request_id)
        & (AuditLog.action == QUERY_ACTION),
    ).where(aud)


async def scope_turns(db: AsyncSession, audience: str, stmt: Select) -> Select:
    if audience == "overview":
        return stmt
    aud = await build_audience_filter(db, AuditLog.ip_address, audience)
    if aud is None:
        return stmt
    return stmt.join(
        AuditLog,
        (AuditLog.request_id == ChatMessageRecord.request_id)
        & (AuditLog.action == QUERY_ACTION),
    ).where(aud)


async def scope_audit(db: AsyncSession, audience: str, stmt: Select) -> Select:
    aud = await build_audience_filter(db, AuditLog.ip_address, audience)
    return stmt if aud is None else stmt.where(aud)


# ---------------------------------------------------------------------------
# Per-stage percentiles
# ---------------------------------------------------------------------------


def _pct_entry(values: list[float] | None, samples: int) -> dict[str, Any]:
    vals = list(values) if values and samples else [None] * len(PERCENTILES)
    return {
        "p50_ms": vals[0],
        "p95_ms": vals[1],
        "p99_ms": vals[2],
        "sample_count": samples,
        "unit": LATENCY_UNIT,
    }


async def stage_percentiles(
    db: AsyncSession, audience: str, since: datetime
) -> dict[str, dict[str, Any]]:
    """p50 / p95 / p99 (``percentile_cont``) per stage plus ``total``; empty -> null."""
    fields = {**STAGE_FIELDS, "total": "total_ms"}
    cols: list[Any] = []
    for name in fields.values():
        col = getattr(LatencyRecord, name)
        cols.extend([func.count(col), _pct_array(col)])
    stmt = await scope_latency(
        db,
        audience,
        select(*cols)
        .select_from(LatencyRecord)
        .where(LatencyRecord.created_at >= since),
    )
    row = (await db.execute(stmt)).one()
    out: dict[str, dict[str, Any]] = {}
    for i, stage in enumerate(fields):
        samples = int(row[2 * i] or 0)
        out[stage] = _pct_entry(row[2 * i + 1], samples)
    return out


# ---------------------------------------------------------------------------
# Hourly series (ROLLUP: 24 buckets + the window total in one statement)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Rollup:
    """Hourly rows keyed by bucket start, plus the whole-window row."""

    hours: dict[datetime, dict[str, Any]] = field(default_factory=dict)
    total: dict[str, Any] = field(default_factory=dict)


def _rollup(rows: list[Any], keys: tuple[str, ...]) -> Rollup:
    result = Rollup()
    for row in rows:
        values = dict(zip(keys, row[2:], strict=True))
        if row[1]:  # grouping() == 1 -> the ROLLUP total row
            result.total = values
        else:
            result.hours[row[0]] = values
    return result


_LATENCY_KEYS = (
    "requests",
    "avg_ms",
    "p50_ms",
    "p95_ms",
    "recent_count",
    "recent_p95_ms",
)


async def latency_rollup(
    db: AsyncSession, audience: str, start: datetime, recent_since: datetime
) -> Rollup:
    hour = _hour(LatencyRecord.created_at)
    recent = case(
        (LatencyRecord.created_at >= recent_since, LatencyRecord.total_ms), else_=None
    )
    stmt = (
        select(
            hour,
            func.grouping(hour),
            func.count(LatencyRecord.id),
            func.avg(LatencyRecord.total_ms),
            func.percentile_cont(0.5).within_group(LatencyRecord.total_ms),
            func.percentile_cont(0.95).within_group(LatencyRecord.total_ms),
            func.count(recent),
            func.percentile_cont(0.95).within_group(recent),
        )
        .select_from(LatencyRecord)
        .where(LatencyRecord.created_at >= start)
        .group_by(func.rollup(hour))
    )
    stmt = await scope_latency(db, audience, stmt)
    return _rollup(list((await db.execute(stmt)).all()), _LATENCY_KEYS)


_TURN_KEYS = (
    "turns",
    "failed",
    "blocked_or_flagged",
    "grounded",
    "grounded_known",
    "cost_usd",
    "cost_today_usd",
    "recent_turns",
    "recent_failed",
)


async def turns_rollup(
    db: AsyncSession,
    audience: str,
    start: datetime,
    recent_since: datetime,
    today_start: datetime,
) -> Rollup:
    msg = ChatMessageRecord
    hour = _hour(msg.created_at)
    failed = msg.outcome.in_(FAILED_OUTCOMES)
    flags_j = cast(msg.guardrail_flags, JSONB)
    flagged = or_(
        status_sql(msg.outcome, msg.guardrail_flags) == "blocked",
        case(
            (
                func.jsonb_typeof(flags_j) == "array",
                func.jsonb_array_length(flags_j) > 0,
            ),
            else_=False,
        ),
    )
    recent = msg.created_at >= recent_since

    def n(cond: Any) -> Any:
        return func.count(case((cond, 1), else_=None))

    stmt = (
        select(
            hour,
            func.grouping(hour),
            func.count(msg.id),
            n(failed),
            n(flagged),
            n(msg.grounded.is_(True)),
            n(msg.grounded.isnot(None)),
            func.coalesce(func.sum(msg.cost_usd), 0.0),
            func.coalesce(
                func.sum(
                    case((msg.created_at >= today_start, msg.cost_usd), else_=None)
                ),
                0.0,
            ),
            n(recent),
            n(recent & failed),
        )
        .select_from(msg)
        .join(ChatSession, ChatSession.id == msg.session_id)
        .where(msg.role == "assistant", msg.created_at >= start)
        .group_by(func.rollup(hour))
    )
    stmt = await scope_turns(db, audience, stmt)
    return _rollup(list((await db.execute(stmt)).all()), _TURN_KEYS)


async def server_error_rollup(
    db: AsyncSession, audience: str, start: datetime, recent_since: datetime
) -> Rollup:
    hour = _hour(AuditLog.created_at)
    stmt = (
        select(
            hour,
            func.grouping(hour),
            func.count(AuditLog.id),
            func.count(case((AuditLog.created_at >= recent_since, 1), else_=None)),
        )
        .where(AuditLog.action == "server_error", AuditLog.created_at >= start)
        .group_by(func.rollup(hour))
    )
    stmt = await scope_audit(db, audience, stmt)
    return _rollup(list((await db.execute(stmt)).all()), ("errors", "recent_errors"))


def hourly_series(
    starts: list[datetime], latency: Rollup, turns: Rollup, server_errors: Rollup
) -> list[dict[str, Any]]:
    """24 buckets, oldest first; empty hours are zero (latency null, samples 0)."""
    series = []
    for start in starts:
        lat = latency.hours.get(start, {})
        turn = turns.hours.get(start, {})
        errs = int(server_errors.hours.get(start, {}).get("errors") or 0)
        requests = int(turn.get("turns") or 0) + errs
        errors = int(turn.get("failed") or 0) + errs
        series.append(
            {
                "hour": start.isoformat() + "Z",
                "requests": requests,
                "errors": errors,
                "error_rate": errors * 100 / requests if requests else 0.0,
                "latency_samples": int(lat.get("requests") or 0),
                "latency_avg_ms": lat.get("avg_ms"),
                "latency_p50_ms": lat.get("p50_ms"),
                "latency_p95_ms": lat.get("p95_ms"),
            }
        )
    return series


# ---------------------------------------------------------------------------
# Breakdown queries
# ---------------------------------------------------------------------------


async def guardrail_flag_breakdown(
    db: AsyncSession, audience: str, since: datetime
) -> dict[str, int]:
    flags_j = cast(ChatMessageRecord.guardrail_flags, JSONB)
    inner = (
        select(func.jsonb_array_elements_text(flags_j).label("flag"))
        .select_from(ChatMessageRecord)
        .where(
            ChatMessageRecord.role == "assistant",
            ChatMessageRecord.created_at >= since,
            func.jsonb_typeof(flags_j) == "array",
        )
    )
    inner = (await scope_turns(db, audience, inner)).subquery()
    stmt = (
        select(inner.c.flag, func.count())
        .group_by(inner.c.flag)
        .order_by(func.count().desc(), inner.c.flag)
    )
    return {str(flag): int(count) for flag, count in (await db.execute(stmt)).all()}


async def outcome_mix(
    db: AsyncSession, audience: str, since: datetime
) -> dict[str, int]:
    outcome = func.coalesce(
        normalized_outcome_sql(ChatMessageRecord.outcome), "unknown"
    ).label("outcome")
    inner = (
        select(outcome)
        .select_from(ChatMessageRecord)
        .where(
            ChatMessageRecord.role == "assistant",
            ChatMessageRecord.created_at >= since,
        )
    )
    inner = (await scope_turns(db, audience, inner)).subquery()
    stmt = select(inner.c.outcome, func.count()).group_by(inner.c.outcome)
    return {str(name): int(count) for name, count in (await db.execute(stmt)).all()}
