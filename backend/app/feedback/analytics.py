"""Feedback analytics: SQL aggregates run in parallel.

Each section is one aggregate statement executed by ``gather_scoped`` on its
own session. None of them take the list filters, so ``total`` is the
unfiltered audience total the KPI cards need.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Date,
    Select,
    String,
    and_,
    cast,
    column,
    func,
    or_,
    select,
    table,
    text,
    true,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.concurrency import gather_scoped
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.feedback.repository import UserMessage, join_exchange, latest_eval
from app.governance.pii import redact_pii
from app.models.db_models import ChatMessageRecord, ChatSession, MessageFeedback
from app.schemas.feedback import TriageStatus

logger = get_logger(__name__)

POSITIVE_MIN = 4
NEGATIVE_MAX = 2
NEUTRAL = 3
TOP_REASONS = 10
LOW_CONFIDENCE_OUTCOMES = ("ungrounded", "llm_failed")

Factory = Callable[[AsyncSession], Awaitable[Any]]

_POS = MessageFeedback.rating >= POSITIVE_MIN
_NEG = MessageFeedback.rating <= NEGATIVE_MAX


def _scoped(stmt: Select, aud: ColumnElement[bool] | None) -> Select:
    stmt = join_exchange(stmt.select_from(MessageFeedback))
    return stmt.where(aud) if aud is not None else stmt


def _count_if(cond: ColumnElement[bool]) -> Any:
    return func.count(MessageFeedback.id).filter(cond)


def _ratio(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total else 0.0


def _summary(aud: ColumnElement[bool] | None) -> Factory:
    async def run(db: AsyncSession) -> dict[str, int]:
        row = (
            await db.execute(
                _scoped(
                    select(
                        func.count(MessageFeedback.id).label("total"),
                        _count_if(_POS).label("pos"),
                        _count_if(_NEG).label("neg"),
                        _count_if(MessageFeedback.rating == NEUTRAL).label("neu"),
                    ),
                    aud,
                )
            )
        ).one()
        return {"total": row.total, "pos": row.pos, "neg": row.neg, "neu": row.neu}

    return run


def _coverage(aud: ColumnElement[bool] | None) -> Factory:
    async def run(db: AsyncSession) -> int:
        stmt = (
            select(func.count(ChatMessageRecord.id))
            .select_from(ChatMessageRecord)
            .join(ChatSession, ChatMessageRecord.session_id == ChatSession.id)
            .where(ChatMessageRecord.role == "assistant")
        )
        if aud is not None:
            stmt = stmt.where(aud)
        return int((await db.scalar(stmt)) or 0)

    return run


def _answers_per_day(aud: ColumnElement[bool] | None, since: datetime) -> Factory:
    """Assistant answers per UTC day: the denominator of daily coverage."""

    async def run(db: AsyncSession) -> dict[str, int]:
        day = cast(ChatMessageRecord.created_at, Date).label("day")
        stmt = (
            select(day, func.count(ChatMessageRecord.id))
            .select_from(ChatMessageRecord)
            .join(ChatSession, ChatMessageRecord.session_id == ChatSession.id)
            .where(
                ChatMessageRecord.role == "assistant",
                ChatMessageRecord.created_at >= since,
            )
        )
        if aud is not None:
            stmt = stmt.where(aud)
        rows = (await db.execute(stmt.group_by(day))).all()
        return {d.isoformat(): int(n) for d, n in rows}

    return run


def with_daily_coverage(
    trend: list[dict[str, Any]], answers: dict[str, int]
) -> list[dict[str, Any]]:
    """Add ``answers`` and ``coverage`` (ratings / answers, 0-1) to each day."""
    return [
        {
            **point,
            "answers": answers.get(point["day"], 0),
            "coverage": (
                round(point["total"] / answers[point["day"]], 4)
                if answers.get(point["day"])
                else None
            ),
        }
        for point in trend
    ]


def _trend(aud: ColumnElement[bool] | None, since: datetime) -> Factory:
    async def run(db: AsyncSession) -> list[dict[str, Any]]:
        day = cast(MessageFeedback.created_at, Date).label("day")
        stmt = _scoped(
            select(
                day,
                func.count(MessageFeedback.id).label("total"),
                _count_if(_POS).label("pos"),
                _count_if(_NEG).label("neg"),
                func.avg(MessageFeedback.rating).label("avg"),
            ),
            aud,
        ).where(MessageFeedback.created_at >= since)
        rows = (await db.execute(stmt.group_by(day).order_by(day))).all()
        return [
            {
                "day": r.day.isoformat(),
                "total": r.total,
                "positive": r.pos,
                "negative": r.neg,
                "avg_rating": round(float(r.avg), 2) if r.avg is not None else None,
            }
            for r in rows
        ]

    return run


def _distribution(aud: ColumnElement[bool] | None) -> Factory:
    async def run(db: AsyncSession) -> dict[str, int]:
        stmt = _scoped(
            select(MessageFeedback.rating, func.count(MessageFeedback.id)), aud
        ).group_by(MessageFeedback.rating)
        counts = {str(k): 0 for k in range(1, 6)}
        for rating, n in (await db.execute(stmt)).all():
            counts[str(rating)] = n
        return counts

    return run


def _by_outcome(aud: ColumnElement[bool] | None) -> Factory:
    async def run(db: AsyncSession) -> dict[str, dict[str, int]]:
        stmt = _scoped(
            select(
                ChatMessageRecord.outcome,
                func.count(MessageFeedback.id).label("total"),
                _count_if(_POS).label("pos"),
                _count_if(_NEG).label("neg"),
            ),
            aud,
        ).group_by(ChatMessageRecord.outcome)
        buckets: dict[str, dict[str, int]] = {}
        for r in (await db.execute(stmt)).all():
            # Legacy rows have no outcome; they were answered turns.
            b = buckets.setdefault(
                r.outcome or "answered", {"positive": 0, "negative": 0, "total": 0}
            )
            b["positive"] += r.pos
            b["negative"] += r.neg
            b["total"] += r.total
        return buckets

    return run


def _top_reasons(aud: ColumnElement[bool] | None) -> Factory:
    async def run(db: AsyncSession) -> list[dict[str, Any]]:
        tags = _scoped(
            select(
                func.jsonb_array_elements_text(MessageFeedback.reason_tags).label("tag")
            ),
            aud,
        ).subquery()
        stmt = (
            select(tags.c.tag, func.count().label("n"))
            .group_by(tags.c.tag)
            .order_by(func.count().desc(), tags.c.tag)
            .limit(TOP_REASONS)
        )
        return [{"key": r.tag, "count": r.n} for r in (await db.execute(stmt)).all()]

    return run


def _funnel(aud: ColumnElement[bool] | None) -> Factory:
    async def run(db: AsyncSession) -> dict[str, int]:
        stmt = _scoped(
            select(MessageFeedback.triage_status, func.count(MessageFeedback.id)), aud
        ).group_by(MessageFeedback.triage_status)
        funnel = {s.value: 0 for s in TriageStatus}
        for status_, n in (await db.execute(stmt)).all():
            funnel[status_] = n
        return funnel

    return run


def _disagreement(aud: ColumnElement[bool] | None) -> Factory:
    async def run(db: AsyncSession) -> dict[str, int]:
        grounded = ChatMessageRecord.grounded.is_(True)
        ungrounded = ChatMessageRecord.grounded.is_(False)
        row = (
            await db.execute(
                _scoped(
                    select(
                        _count_if(and_(_POS, grounded)).label("pg"),
                        _count_if(and_(_POS, ungrounded)).label("pu"),
                        _count_if(and_(_NEG, grounded)).label("ng"),
                        _count_if(and_(_NEG, ungrounded)).label("nu"),
                    ),
                    aud,
                )
            )
        ).one()
        return {
            "positive_grounded": row.pg,
            "positive_ungrounded": row.pu,
            "negative_grounded": row.ng,
            "negative_ungrounded": row.nu,
        }

    return run


def _knowledge_gaps(
    aud: ColumnElement[bool] | None, threshold: float, limit: int
) -> Factory:
    async def run(db: AsyncSession) -> list[dict[str, Any]]:
        ev = latest_eval()
        topic = func.lower(func.btrim(UserMessage.content)).label("topic")
        low_confidence = or_(
            ev.c.confidence < threshold,
            ChatMessageRecord.grounded.is_(False),
            ChatMessageRecord.outcome.in_(LOW_CONFIDENCE_OUTCOMES),
        )
        stmt = (
            _scoped(
                select(
                    topic,
                    func.min(UserMessage.content).label("query"),
                    func.count(MessageFeedback.id).label("n"),
                    func.avg(MessageFeedback.rating).label("avg_rating"),
                    func.avg(ev.c.confidence).label("avg_conf"),
                    func.max(MessageFeedback.created_at).label("last_seen"),
                    func.max(cast(MessageFeedback.message_id, String)).label(
                        "message_id"
                    ),
                ),
                aud,
            )
            .outerjoin(ev, true())
            .where(_NEG, UserMessage.content.is_not(None), low_confidence)
            .group_by(topic)
            .order_by(func.count(MessageFeedback.id).desc(), topic)
            .limit(limit)
        )
        return [
            {
                "query": redact_pii(r.query)[0],
                "count": r.n,
                "avg_rating": round(float(r.avg_rating), 2),
                "avg_confidence": (
                    round(float(r.avg_conf), 3) if r.avg_conf is not None else None
                ),
                "last_seen": r.last_seen.isoformat() + "Z" if r.last_seen else None,
                "message_id": r.message_id,
            }
            for r in (await db.execute(stmt)).all()
        ]

    return run


def _by_intent(aud: ColumnElement[bool] | None) -> Factory:
    """Only once the ``turn_insights`` table exists; otherwise None."""

    async def run(db: AsyncSession) -> list[dict[str, Any]] | None:
        exists = await db.scalar(text("SELECT to_regclass('public.turn_insights')"))
        if exists is None:
            return None
        ti = table("turn_insights", column("message_id"), column("intent")).alias("ti")
        try:
            stmt = (
                _scoped(select(ti.c.intent, func.count(MessageFeedback.id)), aud)
                .join(ti, ti.c.message_id == MessageFeedback.message_id)
                .group_by(ti.c.intent)
                .order_by(func.count(MessageFeedback.id).desc())
            )
            rows = (await db.execute(stmt)).all()
        except DBAPIError:
            logger.warning("feedback_by_intent_unavailable", exc_info=True)
            return None
        return [{"key": str(k), "count": n} for k, n in rows]

    return run


def _within(
    cond: ColumnElement[bool] | None,
    column_: Any,
    window: tuple[datetime, datetime] | None,
) -> ColumnElement[bool] | None:
    """``cond`` narrowed to ``start <= column_ < end`` when a window is given."""
    if window is None:
        return cond
    in_window = and_(column_ >= window[0], column_ < window[1])
    return in_window if cond is None else and_(cond, in_window)


async def compute_analytics(
    aud_feedback: ColumnElement[bool] | None,
    aud_queries: ColumnElement[bool] | None,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    window: tuple[datetime, datetime] | None = None,
) -> dict[str, Any]:
    """All sections, optionally limited to ratings (and answers) in ``window``.

    Without a window the totals are all time and the trend covers the last
    ``feedback_trend_days``; with one, every section (KPIs, trend, funnel,
    gaps, coverage) counts only ratings given in ``[start, end)``.
    """
    settings = get_settings()
    since = (now or datetime.utcnow()) - timedelta(days=settings.feedback_trend_days)
    if window is not None:
        since = window[0]
        aud_feedback = _within(aud_feedback, MessageFeedback.created_at, window)
        aud_queries = _within(aud_queries, ChatMessageRecord.created_at, window)
    (
        summary,
        total_queries,
        trend,
        distribution,
        by_outcome,
        reasons,
        funnel,
        disagreement,
        gaps,
        by_intent,
        answers_by_day,
    ) = await gather_scoped(
        _summary(aud_feedback),
        _coverage(aud_queries),
        _trend(aud_feedback, since),
        _distribution(aud_feedback),
        _by_outcome(aud_feedback),
        _top_reasons(aud_feedback),
        _funnel(aud_feedback),
        _disagreement(aud_feedback),
        _knowledge_gaps(
            aud_feedback,
            settings.feedback_gap_confidence_threshold,
            settings.feedback_gap_limit,
        ),
        _by_intent(aud_feedback),
        _answers_per_day(aud_queries, since),
        session_factory=session_factory,
    )
    total = summary["total"]
    payload: dict[str, Any] = {
        "total": total,
        "total_feedback": total,
        "positive_count": summary["pos"],
        "negative_count": summary["neg"],
        "neutral_count": summary["neu"],
        "positive_ratio": _ratio(summary["pos"], total),
        "negative_ratio": _ratio(summary["neg"], total),
        "feedback_rate": _ratio(total, total_queries),
        "feedback_by_outcome": by_outcome,
        "daily_trend": with_daily_coverage(trend, answers_by_day),
        "rating_distribution": distribution,
        "top_reasons": reasons,
        "triage_funnel": funnel,
        "disagreement": disagreement,
        "knowledge_gaps": gaps,
    }
    if by_intent is not None:
        payload["by_intent"] = by_intent
    return payload
