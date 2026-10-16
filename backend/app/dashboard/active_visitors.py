"""Active visitors: the single definition behind every "active users" number.

An active visitor is a distinct end-user identity with at least one user
query (``chat_messages.role = 'user'``) in the period:

- identity = the session's actor (``chat_sessions.user_email``: the signed-in
  user, historically the sanitized ``X-Client-Id``) when present and not the
  default / anonymous actor, else the client IP of the query (the message IP,
  falling back to the session's ``created_ip``);
- queries from test IPs (``DEV_TEST_IPS`` + ``test_ip_configs``) are excluded
  through the shared audience filter. On the ``testing`` audience the same
  count is taken over test traffic instead.

Every dashboard card that reports active users counts ``IDENTITY`` under
``visitor_filter`` (embedded via ``identity_column``) or calls the helpers
below. Raw identities and IPs never leave SQL: only counts are returned.
Window filters use ``ix_chat_messages_created_at``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    case,
    func,
    literal,
    literal_column,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.audit import ANONYMOUS_ACTOR
from app.dashboard.window import delta
from app.governance.audience import Audience, build_audience_filter
from app.governance.auth import DEFAULT_ACTOR
from app.governance.timeseries import Rollup
from app.models.db_models import ChatMessageRecord, ChatSession

ACTIVE_NOW_SPAN = timedelta(minutes=5)
PERIODS: Mapping[str, timedelta] = {
    "dau": timedelta(days=1),
    "wau": timedelta(days=7),
    "mau": timedelta(days=30),
}
ANONYMOUS_ACTORS: tuple[str, ...] = (DEFAULT_ACTOR, ANONYMOUS_ACTOR)
VISITORS = "visitors"
SOURCE = "chat_messages.role=user (visitor identity, test IPs excluded)"
DEFINITION = (
    "Active visitors are distinct end users with at least one query in the "
    "period. A visitor is identified by their client id, or by their IP "
    "address when the client id is missing or the default anonymous value. "
    "Traffic from test IPs is excluded."
)

# Client IP of a query: the message's own IP, else the session's.
VISITOR_IP = func.coalesce(ChatMessageRecord.ip_address, ChatSession.created_ip)


def identity_sql(actor: Any, ip: Any) -> Any:
    """``client:<actor>`` unless the actor is empty / default, else ``ip:<ip>``."""
    client = func.nullif(func.lower(func.trim(actor)), "")
    return case(
        (client.not_in(ANONYMOUS_ACTORS), literal("client:") + client),
        else_=literal("ip:") + ip,
    )


IDENTITY = identity_sql(ChatSession.user_email, VISITOR_IP)


def visitor_audience(audience: str) -> Audience:
    """Test IPs are always excluded, except on the ``testing`` audience."""
    return "testing" if audience == "testing" else "users"


async def visitor_filter(db: AsyncSession, audience: str) -> ColumnElement[bool]:
    """Rows that count as visitor traffic (reuses the audience test-IP filter)."""
    cond = await build_audience_filter(db, VISITOR_IP, visitor_audience(audience))
    if cond is None:  # pragma: no cover - only the 'overview' audience has none
        return VISITOR_IP.isnot(None)
    return cond


async def identity_column(db: AsyncSession, audience: str) -> Any:
    """``IDENTITY`` for visitor rows, NULL otherwise (for queries over
    ``chat_messages`` joined to ``chat_sessions``); count with ``count_visitors``."""
    return case((await visitor_filter(db, audience), IDENTITY), else_=None)


def count_visitors(col: Any) -> Any:
    return func.count(func.distinct(col))


def user_queries(*cols: Any) -> Select:
    """``SELECT cols`` over user queries joined to their session."""
    return (
        select(*cols)
        .select_from(ChatMessageRecord)
        .outerjoin(ChatSession, ChatMessageRecord.session_id == ChatSession.id)
        .where(ChatMessageRecord.role == "user")
    )


async def count_active(
    db: AsyncSession, audience: str, start: datetime | None, end: datetime
) -> int:
    """Active visitors with a query in ``[start, end)`` (``start=None``: all time)."""
    stmt = user_queries(count_visitors(IDENTITY)).where(
        await visitor_filter(db, audience), ChatMessageRecord.created_at < end
    )
    if start is not None:
        stmt = stmt.where(ChatMessageRecord.created_at >= start)
    return int(await db.scalar(stmt) or 0)


def _between(ts: Any, start: datetime, end: datetime) -> Any:
    return (ts >= start) & (ts < end)


async def active_counts(
    db: AsyncSession, audience: str, now: datetime
) -> dict[str, int]:
    """DAU / WAU / MAU, their previous equal windows and active-now, in one
    statement over ``[now - 2 * 30 days, now)``."""
    oldest = now - 2 * max(PERIODS.values())
    rows = (
        user_queries(
            IDENTITY.label("visitor"), ChatMessageRecord.created_at.label("ts")
        )
        .where(
            await visitor_filter(db, audience),
            _between(ChatMessageRecord.created_at, oldest, now),
        )
        .subquery()
    )
    measures: dict[str, Any] = {
        "active_now": _between(rows.c.ts, now - ACTIVE_NOW_SPAN, now),
    }
    for key, span in PERIODS.items():
        measures[key] = _between(rows.c.ts, now - span, now)
        measures[f"{key}_previous"] = _between(rows.c.ts, now - 2 * span, now - span)
    stmt = select(
        *(
            count_visitors(rows.c.visitor).filter(cond).label(name)
            for name, cond in measures.items()
        )
    )
    row = (await db.execute(stmt)).one()
    return {name: int(row[i] or 0) for i, name in enumerate(measures)}


def summary(counts: Mapping[str, int]) -> dict[str, Any]:
    """The ``active_visitors`` response block (counts only, no identities)."""
    out: dict[str, Any] = {
        "definition": DEFINITION,
        "active_now": counts["active_now"],
        "active_now_minutes": int(ACTIVE_NOW_SPAN.total_seconds() // 60),
    }
    for key, span in PERIODS.items():
        value, previous = counts[key], counts[f"{key}_previous"]
        out[key] = {
            "value": value,
            "previous": previous,
            "delta": delta(value, previous),
            "days": span.days,
        }
    return out


async def hourly_active(
    db: AsyncSession, audience: str, start: datetime, end: datetime
) -> Rollup:
    """Active visitors per hour of ``[start, end)`` plus the whole window."""
    hour = func.date_trunc(literal_column("'hour'"), ChatMessageRecord.created_at)
    stmt = (
        user_queries(hour, func.grouping(hour), count_visitors(IDENTITY))
        .where(
            await visitor_filter(db, audience),
            _between(ChatMessageRecord.created_at, start, end),
        )
        .group_by(func.rollup(hour))
    )
    result = Rollup()
    for bucket, grouped, visitors in (await db.execute(stmt)).all():
        if grouped:
            result.total = {VISITORS: int(visitors)}
        else:
            result.hours[bucket] = {VISITORS: int(visitors)}
    return result
