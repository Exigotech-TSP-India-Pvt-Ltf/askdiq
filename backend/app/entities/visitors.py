"""Visitor stats and per-visitor timeline.

A visitor is one client IP, grouped on ``chat_sessions.created_ip`` exactly
like ``GET /admin/users``. ``active_now`` and ``active_visitors`` use the
shared active-visitor definition (``app.dashboard.active_visitors``). Days
are UTC. The stats run as four single
statements in parallel (``gather_scoped``); the timeline reads one visitor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    extract,
    func,
    literal_column,
    select,
    union,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import ScopedRunner
from app.dashboard import active_visitors
from app.dashboard.queries import ASST, USER_TURN, pairs
from app.dashboard.window import DayRange, iso_z, money, ratio
from app.governance.audience import build_audience_filter
from app.governance.outcomes import LEGACY_OUTCOME_MAP
from app.models.db_models import (
    ChatMessageRecord,
    ChatSession,
    LatencyRecord,
    MessageFeedback,
    User,
)

CLIENT_PREFIX = "client-"
MAX_SESSIONS = 200
MAX_QUERIES = 100
MAX_FEEDBACK = 100
QUERY_TEXT_MAX = 500
ISO_WEEKDAY_OFFSET = 1  # Postgres isodow: Monday = 1


def _day(col: Any) -> Any:
    return func.date_trunc(literal_column("'day'"), col)


async def _visitor_scope(db: AsyncSession, audience: str) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [ChatSession.created_ip.isnot(None)]
    aud = await build_audience_filter(db, ChatSession.created_ip, audience)
    if aud is not None:
        conds.append(aud)
    return conds


# ---------------------------------------------------------------------------
# GET /admin/visitors/stats
# ---------------------------------------------------------------------------


async def new_vs_returning(
    db: AsyncSession, audience: str, days: DayRange
) -> tuple[dict[date, tuple[int, int]], tuple[int, int]]:
    """Per day (new, returning) plus (visitors, new visitors) for the range.

    A visitor is active on a day when it started a session or sent a message
    that day; it is new on the day of its first-ever session.
    """
    scope = await _visitor_scope(db, audience)
    s, m = ChatSession, ChatMessageRecord
    session_days = select(
        s.created_ip.label("ip"), _day(s.created_at).label("d")
    ).where(s.created_at >= days.start, s.created_at < days.end, *scope)
    message_days = (
        select(s.created_ip.label("ip"), _day(m.created_at).label("d"))
        .select_from(m)
        .join(s, s.id == m.session_id)
        .where(m.role == "user", m.created_at >= days.start, m.created_at < days.end)
        .where(*scope)
    )
    active = union(session_days, message_days).cte("active")
    first = (
        select(s.created_ip.label("ip"), func.min(s.created_at).label("first_at"))
        .where(s.created_ip.in_(select(active.c.ip)))
        .group_by(s.created_ip)
        .subquery("first_seen")
    )
    first_day = _day(first.c.first_at)
    stmt = (
        select(
            active.c.d,
            func.grouping(active.c.d),
            func.count().filter(first_day == active.c.d),
            func.count().filter(first_day < active.c.d),
            func.count(func.distinct(active.c.ip)),
            func.count(func.distinct(active.c.ip)).filter(
                first.c.first_at >= days.start
            ),
        )
        .select_from(active.join(first, first.c.ip == active.c.ip))
        .group_by(func.rollup(active.c.d))
    )
    per_day: dict[date, tuple[int, int]] = {}
    totals = (0, 0)
    for row in (await db.execute(stmt)).all():
        if row[1]:
            totals = (int(row[4]), int(row[5]))
        else:
            per_day[row[0].date()] = (int(row[2]), int(row[3]))
    return per_day, totals


async def session_heatmap(
    db: AsyncSession, audience: str, days: DayRange
) -> list[tuple[int, int, int]]:
    """(weekday 0=Mon, hour, sessions started) for non-empty slots."""
    scope = await _visitor_scope(db, audience)
    weekday = extract("isodow", ChatSession.created_at)
    hour = extract("hour", ChatSession.created_at)
    stmt = (
        select(weekday, hour, func.count())
        .where(
            ChatSession.created_at >= days.start,
            ChatSession.created_at < days.end,
            *scope,
        )
        .group_by(weekday, hour)
        .order_by(weekday, hour)
    )
    return [
        (int(w) - ISO_WEEKDAY_OFFSET, int(h), int(n))
        for w, h, n in (await db.execute(stmt)).all()
    ]


async def query_count(db: AsyncSession, audience: str, days: DayRange) -> int:
    scope = await _visitor_scope(db, audience)
    m = ChatMessageRecord
    stmt = (
        select(func.count(m.id))
        .select_from(m)
        .join(ChatSession, ChatSession.id == m.session_id)
        .where(m.role == "user", m.created_at >= days.start, m.created_at < days.end)
        .where(*scope)
    )
    return int(await db.scalar(stmt) or 0)


async def visitor_stats(
    runner: ScopedRunner, audience: str, days: DayRange, now: datetime
) -> dict[str, Any]:
    (per_day, (visitors, new)), heat, queries, live = await runner.gather(
        lambda s: new_vs_returning(s, audience, days),
        lambda s: session_heatmap(s, audience, days),
        lambda s: query_count(s, audience, days),
        lambda s: active_visitors.active_counts(s, audience, now),
    )
    sessions = sum(n for _, _, n in heat)
    return {
        "from": days.first.isoformat(),
        "to": days.last.isoformat(),
        "tz": "UTC",
        "kpis": {
            "visitors": visitors,
            "new_visitors": new,
            "returning_visitors": visitors - new,
            "active_now": live["active_now"],
            "sessions": sessions,
            "queries": queries,
            "avg_sessions_per_visitor": ratio(sessions, visitors),
        },
        "new_vs_returning": [
            {
                "day": d.isoformat(),
                "new": per_day.get(d, (0, 0))[0],
                "returning": per_day.get(d, (0, 0))[1],
            }
            for d in days.days()
        ],
        "heatmap": [{"weekday": w, "hour": h, "sessions": n} for w, h, n in heat],
        "active_visitors": active_visitors.summary(live),
    }


# ---------------------------------------------------------------------------
# GET /admin/users?weekday=&hour=
# ---------------------------------------------------------------------------


async def ips_in_slot(
    db: AsyncSession, weekday: int, hour: int, days: DayRange
) -> set[str]:
    """Visitors with a session started on ``weekday`` (0 = Mon) at ``hour`` (UTC)."""
    stmt = (
        select(ChatSession.created_ip)
        .where(
            ChatSession.created_ip.isnot(None),
            ChatSession.created_at >= days.start,
            ChatSession.created_at < days.end,
            extract("isodow", ChatSession.created_at) == weekday + ISO_WEEKDAY_OFFSET,
            extract("hour", ChatSession.created_at) == hour,
        )
        .distinct()
    )
    return {str(ip) for ip in (await db.execute(stmt)).scalars().all()}


# ---------------------------------------------------------------------------
# GET /admin/visitors/{visitor_id}/timeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Visitor:
    visitor_id: str
    ip_address: str | None
    condition: ColumnElement[bool]


async def resolve_visitor(db: AsyncSession, visitor_id: str) -> Visitor | None:
    """``client-<ip>`` (sessions from that IP) or a user UUID (their sessions)."""
    if visitor_id.startswith(CLIENT_PREFIX):
        ip = visitor_id.removeprefix(CLIENT_PREFIX)
        cond = ChatSession.created_ip == ip
        exists = await db.scalar(select(ChatSession.id).where(cond).limit(1))
        return Visitor(visitor_id, ip, cond) if ip and exists else None
    try:
        user_id = uuid.UUID(visitor_id)
    except ValueError:
        return None
    email = await db.scalar(select(User.email).where(User.id == user_id))
    if email is None:
        return None
    cond = ChatSession.user_email == email
    ip = await db.scalar(
        select(ChatSession.created_ip)
        .where(cond, ChatSession.created_ip.isnot(None))
        .order_by(ChatSession.last_activity_at.desc())
        .limit(1)
    )
    return Visitor(visitor_id, ip, cond)


async def timeline_sessions(
    db: AsyncSession, visitor: Visitor, days: DayRange
) -> list[Any]:
    turns = (
        select(func.count(func.distinct(ChatMessageRecord.turn_index)))
        .where(
            ChatMessageRecord.session_id == ChatSession.id,
            ChatMessageRecord.role == "user",
        )
        .scalar_subquery()
    )
    cost = (
        select(func.coalesce(func.sum(ChatMessageRecord.cost_usd), 0.0))
        .where(
            ChatMessageRecord.session_id == ChatSession.id,
            ChatMessageRecord.role == "assistant",
        )
        .scalar_subquery()
    )
    stmt = (
        select(ChatSession, turns, cost)
        .where(
            visitor.condition,
            ChatSession.created_at < days.end,
            ChatSession.last_activity_at >= days.start,
        )
        .order_by(ChatSession.created_at, ChatSession.id)
        .limit(MAX_SESSIONS + 1)
    )
    return list((await db.execute(stmt)).all())


async def timeline_queries(
    db: AsyncSession, visitor: Visitor, days: DayRange
) -> list[Any]:
    stmt = (
        pairs(
            USER_TURN.session_id,
            USER_TURN.turn_index,
            USER_TURN.content,
            USER_TURN.created_at,
            ASST.request_id,
            ASST.outcome,
            ASST.grounded,
            ASST.cost_usd,
            LatencyRecord.total_ms,
        )
        .outerjoin(LatencyRecord, LatencyRecord.request_id == ASST.request_id)
        .where(
            visitor.condition,
            USER_TURN.created_at >= days.start,
            USER_TURN.created_at < days.end,
        )
        .order_by(USER_TURN.created_at.desc(), USER_TURN.turn_index.desc())
        .limit(MAX_QUERIES + 1)
    )
    return list((await db.execute(stmt)).all())


async def timeline_cost(
    db: AsyncSession, visitor: Visitor, days: DayRange
) -> dict[date, tuple[float, int]]:
    day = _day(USER_TURN.created_at)
    stmt = (
        pairs(day, func.coalesce(func.sum(ASST.cost_usd), 0.0), func.count())
        .where(
            visitor.condition,
            USER_TURN.created_at >= days.start,
            USER_TURN.created_at < days.end,
        )
        .group_by(day)
    )
    return {
        row[0].date(): (float(row[1]), int(row[2]))
        for row in (await db.execute(stmt)).all()
    }


async def timeline_feedback(
    db: AsyncSession, visitor: Visitor, days: DayRange
) -> list[Any]:
    fb, msg = MessageFeedback, ChatMessageRecord
    stmt = (
        select(fb, msg.turn_index, msg.request_id)
        .join(msg, msg.id == fb.message_id)
        .join(ChatSession, ChatSession.id == fb.session_id)
        .where(visitor.condition, fb.created_at >= days.start, fb.created_at < days.end)
        .order_by(fb.created_at.desc(), fb.id.desc())
        .limit(MAX_FEEDBACK + 1)
    )
    return list((await db.execute(stmt)).all())


async def timeline_activity(
    db: AsyncSession, visitor: Visitor, days: DayRange
) -> dict[tuple[date, str], int]:
    """Sessions started and feedback given per UTC day (one grouped statement)."""
    started = select(
        _day(ChatSession.created_at).label("day"),
        literal_column("'sessions'").label("kind"),
    ).where(
        visitor.condition,
        ChatSession.created_at >= days.start,
        ChatSession.created_at < days.end,
    )
    rated = (
        select(
            _day(MessageFeedback.created_at).label("day"),
            literal_column("'feedback'").label("kind"),
        )
        .join(ChatSession, ChatSession.id == MessageFeedback.session_id)
        .where(
            visitor.condition,
            MessageFeedback.created_at >= days.start,
            MessageFeedback.created_at < days.end,
        )
    )
    events = union_all(started, rated).subquery()
    stmt = select(events.c.day, events.c.kind, func.count()).group_by(
        events.c.day, events.c.kind
    )
    return {
        (row[0].date(), str(row[1])): int(row[2])
        for row in (await db.execute(stmt)).all()
    }


def _normalized_outcome(raw: str | None) -> str | None:
    if raw is None:
        return None
    return LEGACY_OUTCOME_MAP.get(raw.lower(), raw)


async def visitor_timeline(
    db: AsyncSession, visitor: Visitor, days: DayRange
) -> dict[str, Any]:
    sessions = await timeline_sessions(db, visitor, days)
    queries = await timeline_queries(db, visitor, days)
    cost = await timeline_cost(db, visitor, days)
    feedback = await timeline_feedback(db, visitor, days)
    activity = await timeline_activity(db, visitor, days)
    return {
        "visitor_id": visitor.visitor_id,
        "ip_address": visitor.ip_address,
        "sessions": [
            {
                "session_id": str(s.id),
                "title": s.title,
                "started_at": iso_z(s.created_at),
                "ended_at": iso_z(
                    max(s.created_at, s.last_activity_at or s.created_at)
                ),
                "turns": int(turns or 0),
                "cost_usd": money(total),
            }
            for s, turns, total in sessions[:MAX_SESSIONS]
        ],
        "queries": [
            {
                "request_id": q.request_id,
                "session_id": str(q.session_id),
                "turn_index": q.turn_index,
                "query": (q.content or "")[:QUERY_TEXT_MAX],
                "outcome": _normalized_outcome(q.outcome),
                "grounded": q.grounded,
                "created_at": iso_z(q.created_at),
                "latency_ms": q.total_ms,
                "cost_usd": q.cost_usd,
            }
            for q in queries[:MAX_QUERIES]
        ],
        "cost_series": [
            {
                "day": d.isoformat(),
                "cost_usd": money(cost.get(d, (0.0, 0))[0]),
                "queries": cost.get(d, (0.0, 0))[1],
            }
            for d in days.days()
        ],
        # Additive: per-day counts for the visitor KPI sparklines.
        "activity_series": [
            {
                "day": d.isoformat(),
                "sessions": activity.get((d, "sessions"), 0),
                "queries": cost.get(d, (0.0, 0))[1],
                "feedback": activity.get((d, "feedback"), 0),
            }
            for d in days.days()
        ],
        "feedback": [
            {
                "id": str(f.id),
                "message_id": str(f.message_id),
                "session_id": str(f.session_id),
                "turn_index": turn_index,
                "request_id": request_id,
                "rating": f.rating,
                "comment": f.comment,
                "reason_tags": [str(t) for t in (f.reason_tags or [])],
                "created_at": iso_z(f.created_at),
            }
            for f, turn_index, request_id in feedback[:MAX_FEEDBACK]
        ],
        "truncated": {
            "sessions": len(sessions) > MAX_SESSIONS,
            "queries": len(queries) > MAX_QUERIES,
            "feedback": len(feedback) > MAX_FEEDBACK,
        },
    }
