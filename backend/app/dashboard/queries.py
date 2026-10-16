"""Single-statement, audience-scoped aggregates behind ``GET /admin/dashboard``.

Every public coroutine issues exactly one SQL statement and takes its own
session, so the service runs them in parallel with ``gather_scoped`` (one
session per task) and the statement count does not grow with data volume.

Windowed sources read ``[previous_start, end)`` once and group with
``GROUPING SETS ((bucket), (period))``: one row per bucket of the current
window (the sparkline) plus one row per period (current / previous totals
for the KPI and its delta). Rows from the previous period have a NULL
bucket, so a partial first bucket never mixes in previous-period data.

Audience scoping follows the endpoint each number already appears on
(visitor counts follow ``app.dashboard.active_visitors``): messages and
turns by the client IP of the user message (or its session),
sessions by ``created_ip``, latency and error rate through the request's
``query`` audit row (``app.governance.timeseries``), feedback like the
Feedback page.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Float,
    Select,
    and_,
    case,
    cast,
    func,
    literal,
    literal_column,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.dashboard import active_visitors
from app.dashboard.window import ACTIVE_SPAN, Bucket, Window
from app.feedback.repository import CLIENT_IP as FEEDBACK_CLIENT_IP
from app.feedback.repository import join_exchange
from app.governance.audience import build_audience_filter
from app.governance.outcomes import ALL_OUTCOMES
from app.governance.timeseries import (
    FAILED_OUTCOMES,
    scope_audit,
    scope_latency,
    scope_turns,
)
from app.governance.traces import normalized_outcome_sql
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    InsightTaxonomy,
    LatencyRecord,
    MessageFeedback,
    TurnInsight,
)

POSITIVE_MIN = 4
NEGATIVE_MAX = 2
TOP_INTENTS = 8
CURRENT, PREVIOUS = "cur", "prev"

# One exchange = the assistant row paired with its user row (same session and
# turn), scoped by the user's IP or the session's IP (as /admin/usage does).
ASST = aliased(ChatMessageRecord, name="dash_asst")
USER_TURN = aliased(ChatMessageRecord, name="dash_user")
PAIR_IP = func.coalesce(USER_TURN.ip_address, ChatSession.created_ip)
MESSAGE_IP = func.coalesce(ChatMessageRecord.ip_address, ChatSession.created_ip)


def trunc(bucket: Bucket, col: Any) -> Any:
    # Inline unit: a bound "day" would differ between SELECT and GROUP BY.
    unit = "hour" if bucket == "hour" else "day"
    return func.date_trunc(literal_column(f"'{unit}'"), col)


def outcome_sql(col: Any) -> Any:
    """Canonical outcome; legacy names mapped, NULL / unknown count as answered."""
    return func.coalesce(normalized_outcome_sql(col), "answered")


def pairs(*cols: Any) -> Select:
    """``SELECT cols`` over exchanges (assistant + user row + session)."""
    return (
        select(*cols)
        .select_from(ASST)
        .join(
            USER_TURN,
            and_(
                USER_TURN.session_id == ASST.session_id,
                USER_TURN.turn_index == ASST.turn_index,
                USER_TURN.role == "user",
            ),
        )
        .join(ChatSession, ASST.session_id == ChatSession.id)
        .where(ASST.role == "assistant")
    )


async def scope_pairs(db: AsyncSession, audience: str, stmt: Select) -> Select:
    aud = await build_audience_filter(db, PAIR_IP, audience)
    return stmt if aud is None else stmt.where(aud)


# ---------------------------------------------------------------------------
# GROUPING SETS helper
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Split:
    """Per-bucket rows of the current window plus current / previous totals."""

    buckets: dict[datetime, dict[str, Any]] = field(default_factory=dict)
    current: dict[str, Any] = field(default_factory=dict)
    previous: dict[str, Any] = field(default_factory=dict)


def tags(window: Window, ts: Any) -> list[Any]:
    """``bucket`` (NULL outside the current window) and ``period`` columns."""
    in_current = ts >= window.start
    return [
        case((in_current, trunc(window.bucket, ts)), else_=None).label("bucket"),
        case((in_current, literal(CURRENT)), else_=literal(PREVIOUS)).label("period"),
    ]


def in_range(window: Window, ts: Any) -> Any:
    return and_(ts >= window.previous_start, ts < window.end)


Measures = Callable[[Any], Mapping[str, Any]]


async def run_split(db: AsyncSession, inner: Select, measures: Measures) -> Split:
    sub = inner.subquery()
    aggregates = measures(sub.c)
    stmt = select(
        sub.c.bucket,
        sub.c.period,
        func.grouping(sub.c.bucket),
        *(expr.label(name) for name, expr in aggregates.items()),
    ).group_by(func.grouping_sets(sub.c.bucket, sub.c.period))
    split = Split()
    for row in (await db.execute(stmt)).all():
        values = dict(zip(aggregates, row[3:], strict=True))
        if row[2] == 0:
            if row[0] is not None:
                split.buckets[row[0]] = values
        elif row[1] == CURRENT:
            split.current = values
        elif row[1] == PREVIOUS:
            split.previous = values
    return split


# ---------------------------------------------------------------------------
# Windowed sources
# ---------------------------------------------------------------------------


async def messages_split(db: AsyncSession, audience: str, window: Window) -> Split:
    """User messages: queries, active visitors (``visitors``) and visitors
    active in the last 24 h of each period (``active``).

    Visitors follow ``app.dashboard.active_visitors`` (identity, test IPs
    excluded); queries follow the audience like before."""
    ts = ChatMessageRecord.created_at
    last_day = case(
        (ts >= window.start, ts >= window.end - ACTIVE_SPAN),
        else_=ts >= window.start - ACTIVE_SPAN,
    )
    identity = await active_visitors.identity_column(db, audience)
    inner = active_visitors.user_queries(
        *tags(window, ts),
        identity.label("visitor"),
        case((last_day, identity), else_=None).label("active_visitor"),
    ).where(in_range(window, ts))
    aud = await build_audience_filter(db, MESSAGE_IP, audience)
    if aud is not None:
        inner = inner.where(aud)
    return await run_split(
        db,
        inner,
        lambda c: {
            "queries": func.count(),
            "visitors": active_visitors.count_visitors(c.visitor),
            "active": active_visitors.count_visitors(c.active_visitor),
        },
    )


async def first_seen_per_bucket(
    db: AsyncSession, audience: str, window: Window
) -> dict[datetime, int]:
    """Active visitors by the bucket of their first query inside the window."""
    ts = ChatMessageRecord.created_at
    rows = (
        active_visitors.user_queries(
            active_visitors.IDENTITY.label("visitor"), ts.label("ts")
        )
        .where(
            ts >= window.start,
            ts < window.end,
            await active_visitors.visitor_filter(db, audience),
        )
        .subquery()
    )
    # Grouped outside the CASE: its bound literals would differ in GROUP BY.
    sub = (
        select(rows.c.visitor, func.min(rows.c.ts).label("first_at"))
        .where(rows.c.visitor.isnot(None))
        .group_by(rows.c.visitor)
        .subquery()
    )
    bucket = trunc(window.bucket, sub.c.first_at)
    stmt = select(bucket, func.count()).group_by(bucket)
    return {row[0]: int(row[1]) for row in (await db.execute(stmt)).all()}


async def sessions_split(db: AsyncSession, audience: str, window: Window) -> Split:
    ts = ChatSession.created_at
    inner = select(*tags(window, ts), ChatSession.id).where(in_range(window, ts))
    aud = await build_audience_filter(db, ChatSession.created_ip, audience)
    if aud is not None:
        inner = inner.where(aud)
    return await run_split(db, inner, lambda _c: {"sessions": func.count()})


async def feedback_split(db: AsyncSession, audience: str, window: Window) -> Split:
    """Ratings (``message_feedback``) with thumbs up (>= 4) and down (<= 2)."""
    ts = MessageFeedback.created_at
    inner = join_exchange(
        select(*tags(window, ts), MessageFeedback.rating.label("rating")).select_from(
            MessageFeedback
        )
    ).where(in_range(window, ts))
    aud = await build_audience_filter(db, FEEDBACK_CLIENT_IP, audience)
    if aud is not None:
        inner = inner.where(aud)
    return await run_split(
        db,
        inner,
        lambda c: {
            "feedback": func.count(),
            "up": func.count().filter(c.rating >= POSITIVE_MIN),
            "down": func.count().filter(c.rating <= NEGATIVE_MAX),
        },
    )


async def latency_split(db: AsyncSession, audience: str, window: Window) -> Split:
    ts = LatencyRecord.created_at
    inner = await scope_latency(
        db,
        audience,
        select(*tags(window, ts), LatencyRecord.total_ms.label("ms"))
        .select_from(LatencyRecord)
        .where(in_range(window, ts)),
    )
    return await run_split(
        db,
        inner,
        lambda c: {
            "count": func.count(c.ms),
            "avg_ms": func.avg(c.ms),
            "p50_ms": func.percentile_cont(0.5).within_group(c.ms),
            "p95_ms": func.percentile_cont(0.95).within_group(c.ms),
        },
    )


async def turn_errors_split(db: AsyncSession, audience: str, window: Window) -> Split:
    """Assistant turns and failed ones (governance error-rate numerator/denominator)."""
    msg = ChatMessageRecord
    inner = await scope_turns(
        db,
        audience,
        select(
            *tags(window, msg.created_at),
            msg.outcome.in_(FAILED_OUTCOMES).label("failed"),
        )
        .select_from(msg)
        .where(msg.role == "assistant", in_range(window, msg.created_at)),
    )
    return await run_split(
        db,
        inner,
        lambda c: {"turns": func.count(), "failed": func.count().filter(c.failed)},
    )


async def server_errors_split(db: AsyncSession, audience: str, window: Window) -> Split:
    inner = await scope_audit(
        db,
        audience,
        select(*tags(window, AuditLog.created_at), AuditLog.id).where(
            AuditLog.action == "server_error", in_range(window, AuditLog.created_at)
        ),
    )
    return await run_split(db, inner, lambda _c: {"errors": func.count()})


async def exchanges_split(db: AsyncSession, audience: str, window: Window) -> Split:
    """Exchanges: cost (USD) and counts per canonical outcome."""
    stmt = pairs(
        *tags(window, ASST.created_at),
        ASST.cost_usd.label("cost"),
        outcome_sql(ASST.outcome).label("outcome"),
    ).where(in_range(window, ASST.created_at))
    inner = await scope_pairs(db, audience, stmt)

    def measures(c: Any) -> dict[str, Any]:
        out: dict[str, Any] = {
            "cost": func.sum(c.cost),
            "cost_rows": func.count(c.cost),
        }
        for name in ALL_OUTCOMES:
            out[name] = func.count().filter(c.outcome == name)
        return out

    return await run_split(db, inner, measures)


# ---------------------------------------------------------------------------
# Non-windowed sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntentCount:
    intent: str
    label: str
    count: int
    total: int


async def top_intents(
    db: AsyncSession, audience: str, window: Window, limit: int = TOP_INTENTS
) -> list[IntentCount]:
    """Most frequent classifier intents among exchanges in the window."""
    msg = ChatMessageRecord
    counts = (
        select(
            TurnInsight.intent.label("intent"),
            func.count().label("n"),
            func.sum(func.count()).over().label("total"),
        )
        .select_from(TurnInsight)
        .join(msg, msg.id == TurnInsight.message_id)
        .join(ChatSession, ChatSession.id == msg.session_id)
        .where(msg.created_at >= window.start, msg.created_at < window.end)
        .group_by(TurnInsight.intent)
    )
    aud = await build_audience_filter(db, ChatSession.created_ip, audience)
    if aud is not None:
        counts = counts.where(aud)
    sub = counts.subquery()
    stmt = (
        select(sub.c.intent, sub.c.n, sub.c.total, InsightTaxonomy.label)
        .select_from(sub)
        .outerjoin(
            InsightTaxonomy,
            and_(
                InsightTaxonomy.kind == "intent", InsightTaxonomy.code == sub.c.intent
            ),
        )
        .order_by(sub.c.n.desc(), sub.c.intent)
        .limit(limit)
    )
    return [
        IntentCount(row[0], row[3] or row[0], int(row[1]), int(row[2]))
        for row in (await db.execute(stmt)).all()
    ]


async def daily_cost(
    db: AsyncSession, audience: str, start: datetime, end: datetime
) -> dict[date, float]:
    """Exchange cost (USD) per UTC day in ``[start, end)``."""
    day = trunc("day", ASST.created_at)
    stmt = pairs(day, func.coalesce(func.sum(ASST.cost_usd), 0.0)).where(
        ASST.created_at >= start, ASST.created_at < end
    )
    stmt = (await scope_pairs(db, audience, stmt)).group_by(day)
    return {row[0].date(): float(row[1]) for row in (await db.execute(stmt)).all()}


def cached_tokens_sql(msg: Any) -> Any:
    """``token_details.cached_tokens`` as a number (0 when absent or not numeric)."""
    value = cast(msg.token_details, JSONB)["cached_tokens"]
    return case(
        (func.jsonb_typeof(value) == "number", cast(value.astext, Float)), else_=0.0
    )
