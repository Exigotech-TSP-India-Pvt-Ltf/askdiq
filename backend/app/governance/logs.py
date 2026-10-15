"""Server-side Governance Logs: filters, keyset pages, level chips, CSV rows.

Level and service are computed in Python for display (``log_summary``); the
SQL expressions below mirror those rules so filtering, counting and paging
all happen in Postgres (G12: no more ``limit=2000`` + browser filtering).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
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

from app.governance.audience import build_audience_filter
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
from app.models.db_models import AuditLog


def _text_array(values: Sequence[str]) -> Any:
    return cast(array(sorted(values), type_=Text), ARRAY(Text))


def _detail_jsonb() -> Any:
    return cast(AuditLog.detail, JSONB)


def level_sql() -> Any:
    """SQL mirror of ``log_summary.level_for`` over ``audit_log`` columns."""
    detail = _detail_jsonb()
    flags = detail["guardrail_flags"]
    whens: list[tuple[ColumnElement[bool], str]] = [
        (func.coalesce(flags.has_any(_text_array(names)), False), level)
        for level, names in LEVEL_FLAGS
    ]
    whens.append((detail["grounded"] == cast(literal("false"), JSONB), "WARNING"))
    by_level: dict[str, list[str]] = {}
    for action, level in LEVEL_BY_ACTION.items():
        by_level.setdefault(level, []).append(action)
    whens.extend(
        (AuditLog.action.in_(sorted(actions)), level)
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
    level = level_sql().label("level")
    newest = _ordered(select(level).where(*conds)).limit(cap + 1).subquery()
    grouped = select(newest.c.level, func.count()).group_by(newest.c.level)
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


def to_item(row: AuditLog) -> dict[str, Any]:
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
        "ip_address": row.ip_address,
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
