"""Server-side Governance Logs: filters, keyset pages, level chips, CSV rows.

Level and service are computed in Python for display (``log_summary``); the
SQL expressions below mirror those rules so filtering, counting and paging
all happen in Postgres (G12: no more ``limit=2000`` + browser filtering).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    ARRAY,
    ColumnElement,
    Select,
    String,
    Text,
    case,
    cast,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.audit import AuthEvent
from app.governance.audience import build_audience_filter
from app.governance.audit import SYSTEM_REQUEST_ID
from app.governance.log_summary import (
    AUTH_PREFIXES,
    LEVEL_BY_ACTION,
    LEVEL_FLAGS,
    LEVELS,
    SERVICE_BY_ACTION,
    level_for,
    service_for,
    summarize,
)
from app.governance.pagination import Cursor, Total, estimated_total, keyset_after
from app.governance.usage_queries import escape_ilike_pattern
from app.models.db_models import AuditLog, AuthSession

# Login events whose ``detail.session_id`` names the auth session they opened.
SESSION_LINKED_ACTIONS: frozenset[str] = frozenset({AuthEvent.LOGIN_SUCCESS.value})


class IpSource(StrEnum):
    """Where a log row's displayed client IP comes from."""

    RECORDED = "recorded"  # stored on the row itself
    SESSION = "session"  # the auth session the login opened
    REQUEST = "request"  # another audit row of the same request


@dataclass(frozen=True, slots=True)
class IpLink:
    ip_address: str
    source: IpSource


def _text_array(values: Sequence[str]) -> Any:
    return cast(array(sorted(values), type_=Text), ARRAY(Text))


def flags_sql() -> Any:
    """``detail.guardrail_flags`` as ``jsonb`` (for ``?|``).

    ``detail`` is a ``json`` column: only the small flags array is converted,
    never the whole document (casting every row's ``detail`` to ``jsonb`` once
    per CASE branch made the level chips the slowest query on the Logs tab).
    """
    return cast(AuditLog.detail["guardrail_flags"], JSONB)


def grounded_sql() -> Any:
    """``detail.grounded`` as ``jsonb`` (so only a JSON ``false`` matches)."""
    return cast(AuditLog.detail["grounded"], JSONB)


def level_sql(
    flags: Any | None = None,
    grounded: Any | None = None,
    action: Any | None = None,
) -> Any:
    """SQL mirror of ``log_summary.level_for`` over ``audit_log`` columns.

    Pass precomputed ``flags`` / ``grounded`` / ``action`` columns (from a
    subquery) to extract them once per row instead of once per branch.
    """
    flags = flags_sql() if flags is None else flags
    grounded = grounded_sql() if grounded is None else grounded
    action = AuditLog.action if action is None else action
    whens: list[tuple[ColumnElement[bool], str]] = [
        (func.coalesce(flags.has_any(_text_array(names)), False), level)
        for level, names in LEVEL_FLAGS
    ]
    whens.append((grounded == cast(literal("false"), JSONB), "WARNING"))
    by_level: dict[str, list[str]] = {}
    for action_name, level in LEVEL_BY_ACTION.items():
        by_level.setdefault(level, []).append(action_name)
    whens.extend(
        (action.in_(sorted(actions)), level)
        for level, actions in sorted(by_level.items())
    )
    return case(*whens, else_="INFO")


def service_sql() -> Any:
    """SQL mirror of ``log_summary.service_for``."""
    by_service: dict[str, list[str]] = {}
    for action, service in SERVICE_BY_ACTION.items():
        by_service.setdefault(service, []).append(action)
    whens: list[tuple[ColumnElement[bool], str]] = [
        (AuditLog.action.in_(sorted(actions)), service)
        for service, actions in sorted(by_service.items())
    ]
    whens.append((or_(*(AuditLog.action.startswith(p) for p in AUTH_PREFIXES)), "auth"))
    return case(*whens, else_="system")


@dataclass(frozen=True, slots=True)
class LogFilters:
    audience: str = "overview"
    levels: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    q: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    # Audit actions the caller may not see (auth events without audit:auth:read).
    hidden_actions: frozenset[str] = frozenset()

    def as_audit_detail(self) -> dict[str, Any]:
        return {
            "audience": self.audience,
            "level": ",".join(self.levels),
            "service": ",".join(self.services),
            "q": self.q or "",
            "from": self.date_from.isoformat() if self.date_from else "",
            "to": self.date_to.isoformat() if self.date_to else "",
        }


async def _conditions(
    db: AsyncSession, f: LogFilters, *, with_level: bool = True
) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    aud = await build_audience_filter(db, AuditLog.ip_address, f.audience)
    if aud is not None:
        conds.append(aud)
    if f.date_from is not None:
        conds.append(AuditLog.created_at >= f.date_from)
    if f.date_to is not None:
        conds.append(AuditLog.created_at <= f.date_to)
    if f.hidden_actions:
        conds.append(AuditLog.action.not_in(f.hidden_actions))
    if f.services:
        conds.append(service_sql().in_(f.services))
    if f.q:
        pattern = f"%{escape_ilike_pattern(f.q)}%"
        conds.append(
            or_(
                AuditLog.action.ilike(pattern, escape="\\"),
                cast(AuditLog.detail, String).ilike(pattern, escape="\\"),
                AuditLog.request_id.ilike(pattern, escape="\\"),
            )
        )
    if with_level and f.levels:
        conds.append(level_sql().in_(f.levels))
    return conds


def _ordered(stmt: Select) -> Select:
    return stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())


async def fetch_page(
    db: AsyncSession, f: LogFilters, cursor: Cursor | None, limit: int
) -> tuple[list[AuditLog], Cursor | None]:
    """One newest-first page plus the cursor of the next one (None at the end)."""
    conds = await _conditions(db, f)
    after = keyset_after(AuditLog.created_at, AuditLog.id, cursor)
    if after is not None:
        conds.append(after)
    stmt = _ordered(select(AuditLog).where(*conds)).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id)
        if has_more and rows
        else None
    )
    return rows, next_cursor


async def level_counts_and_total(
    db: AsyncSession, f: LogFilters, cap: int
) -> tuple[dict[str, int], bool, Total]:
    """Level chip counts (ignoring the level filter) and the filtered total.

    One grouped query over at most ``cap + 1`` newest rows: while under the
    cap the counts and total are exact; above it, counts cover the newest
    ``cap`` rows (``capped``) and the total is the planner estimate.
    """
    conds = await _conditions(db, f, with_level=False)
    # The LIMIT keeps this subquery from being flattened, so each row's flags
    # and grounded values are extracted once; the CASE then runs on them.
    newest = (
        _ordered(
            select(
                flags_sql().label("flags"),
                grounded_sql().label("grounded"),
                AuditLog.action.label("action"),
            ).where(*conds)
        )
        .limit(cap + 1)
        .subquery()
    )
    level = level_sql(newest.c.flags, newest.c.grounded, newest.c.action).label("level")
    grouped = select(level, func.count()).group_by(level)
    counts = dict.fromkeys(LEVELS, 0)
    for name, n in (await db.execute(grouped)).all():
        counts[str(name)] = int(n)
    seen = sum(counts.values())
    capped = seen > cap
    if not capped:
        value = sum(counts[name] for name in f.levels) if f.levels else seen
        return counts, False, Total(value, is_estimate=False)
    filtered = await _conditions(db, f)
    total = await estimated_total(db, select(AuditLog.id).where(*filtered), cap)
    return counts, True, total


def _session_id(row: AuditLog) -> uuid.UUID | None:
    if row.action not in SESSION_LINKED_ACTIONS or not isinstance(row.detail, dict):
        return None
    try:
        return uuid.UUID(str(row.detail.get("session_id") or ""))
    except ValueError:
        return None


async def linked_ips(
    db: AsyncSession, rows: Sequence[AuditLog], hidden_actions: frozenset[str]
) -> dict[Any, IpLink]:
    """Correlated client IPs for a page's rows that have none recorded.

    Two batched lookups (never one per row): the auth session a login opened,
    then another audit row of the same request. Only real stored values are
    returned, marked with their source; nothing is backfilled or guessed. Rows
    of ``hidden_actions`` never lend their IP to a visible row.
    """
    missing = [row for row in rows if not row.ip_address]
    links: dict[Any, IpLink] = {}
    sessions = {sid: row for row in missing if (sid := _session_id(row))}
    if sessions:
        found = await db.execute(
            select(AuthSession.id, AuthSession.ip_address).where(
                AuthSession.id.in_(sessions), AuthSession.ip_address.is_not(None)
            )
        )
        for sid, ip in found.all():
            if ip:
                links[sessions[sid].id] = IpLink(ip, IpSource.SESSION)
    by_request: dict[str, list[AuditLog]] = {}
    for row in missing:
        if row.id not in links and row.request_id != SYSTEM_REQUEST_ID:
            by_request.setdefault(row.request_id, []).append(row)
    if by_request:
        conds = [
            AuditLog.request_id.in_(by_request),
            AuditLog.ip_address.is_not(None),
            AuditLog.ip_address != "",
        ]
        if hidden_actions:
            conds.append(AuditLog.action.not_in(hidden_actions))
        found = await db.execute(
            select(AuditLog.request_id, func.min(AuditLog.ip_address))
            .where(*conds)
            .group_by(AuditLog.request_id)
        )
        for request_id, ip in found.all():
            for row in by_request.get(request_id, []):
                links[row.id] = IpLink(ip, IpSource.REQUEST)
    return links


async def fetch_items(
    db: AsyncSession, f: LogFilters, cursor: Cursor | None, limit: int
) -> tuple[list[dict[str, Any]], Cursor | None]:
    """One page as API items, with correlated IPs for rows that lack one."""
    rows, next_cursor = await fetch_page(db, f, cursor, limit)
    links = await linked_ips(db, rows, f.hidden_actions)
    return [to_item(row, links.get(row.id)) for row in rows], next_cursor


def to_item(row: AuditLog, link: IpLink | None = None) -> dict[str, Any]:
    action = str(getattr(row.action, "value", row.action))
    detail = row.detail if isinstance(row.detail, dict) else {}
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat() + "Z",
        "level": level_for(detail, action),
        "service": service_for(action),
        "action": action,
        "summary": summarize(action, detail),
        "detail": detail,
        # Deprecated legacy form of ``detail`` (kept for old clients).
        "message": json.dumps(detail, default=str)[:300] if detail else "",
        "user_id": row.actor,
        "ip_address": row.ip_address or (link.ip_address if link else None),
        "ip_source": (
            IpSource.RECORDED.value
            if row.ip_address
            else (link.source.value if link else None)
        ),
        "request_id": row.request_id,
    }


async def stream_rows(
    db: AsyncSession, f: LogFilters, max_rows: int, batch: int = 1000
) -> AsyncIterator[dict[str, Any]]:
    """Newest-first rows for export, streamed server-side (flat memory)."""
    conds = await _conditions(db, f)
    stmt = (
        _ordered(select(AuditLog).where(*conds))
        .limit(max_rows)
        .execution_options(yield_per=batch)
    )
    result = await db.stream_scalars(stmt)
    async for row in result:
        yield to_item(row)
