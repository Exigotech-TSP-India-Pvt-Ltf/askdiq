"""Keyset (cursor) pagination and bounded totals for large governance tables.

newplan §13.2: lists over ``audit_log`` / ``latency_metrics`` page on
``(created_at, id)`` instead of OFFSET, and an exact ``total`` is only
computed while it is cheap. Above ``GOVERNANCE_EXACT_COUNT_CAP`` matching
rows the total becomes the planner's estimate and is flagged as such.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger

logger = get_logger(__name__)

_MAX_CURSOR_LEN = 200


@dataclass(frozen=True, slots=True)
class Cursor:
    """Position after the last row of a page (newest-first ordering)."""

    created_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        raw = json.dumps(
            {"t": self.created_at.isoformat(), "id": str(self.id)},
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str | None) -> Cursor | None:
        """Parse an opaque cursor; anything malformed is a 422, never a 500."""
        if not token:
            return None
        try:
            if len(token) > _MAX_CURSOR_LEN:
                raise ValueError("cursor too long")
            padded = token + "=" * (-len(token) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode()))
            created_at = datetime.fromisoformat(str(data["t"]))
            if created_at.tzinfo is not None:
                raise ValueError("cursor timestamps are naive UTC")
            return cls(created_at=created_at, id=uuid.UUID(str(data["id"])))
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid cursor."
            ) from exc


def keyset_after(
    created_col: Any, id_col: Any, cursor: Cursor | None
) -> ColumnElement[bool] | None:
    """Rows strictly older than ``cursor`` in ``(created_at DESC, id DESC)``."""
    if cursor is None:
        return None
    return tuple_(created_col, id_col) < tuple_(cursor.created_at, cursor.id)


@dataclass(frozen=True, slots=True)
class Total:
    value: int
    is_estimate: bool

    @property
    def estimate(self) -> int | None:
        return self.value if self.is_estimate else None


async def planner_estimate(db: AsyncSession, stmt: Select) -> int | None:
    """Row estimate of ``stmt`` from ``EXPLAIN (FORMAT JSON)``; no rows are read.

    The statement is compiled by SQLAlchemy and its values are sent as bound
    parameters (never interpolated), so user filters stay inert.
    """
    conn = await db.connection()
    compiled = stmt.compile(
        dialect=conn.dialect, compile_kwargs={"render_postcompile": True}
    )
    names = compiled.positiontup or []
    params = tuple(compiled.params[name] for name in names)
    try:
        # A savepoint keeps a failed EXPLAIN from aborting the caller's transaction.
        async with conn.begin_nested():
            result = await conn.exec_driver_sql(
                "EXPLAIN (FORMAT JSON) " + compiled.string,  # nosec B608 - compiled SA statement, values are bound
                params,
            )
            plan = result.scalar()
    except Exception:
        logger.warning("planner_estimate_failed", exc_info=True)
        return None
    if isinstance(plan, str):
        plan = json.loads(plan)
    try:
        return int(plan[0]["Plan"]["Plan Rows"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def capped_subquery(stmt: Select, cap: int) -> Any:
    """``stmt`` limited to ``cap + 1`` rows, as a subquery (bounded count)."""
    return stmt.limit(cap + 1).subquery()


async def bounded_total(db: AsyncSession, rows: Select, cap: int) -> Total:
    """Exact count up to ``cap``; above it, the planner estimate (flagged)."""
    sub = capped_subquery(rows, cap)
    count = int(await db.scalar(select(func.count()).select_from(sub)) or 0)
    if count <= cap:
        return Total(count, is_estimate=False)
    return await estimated_total(db, rows, cap)


async def estimated_total(db: AsyncSession, rows: Select, cap: int) -> Total:
    estimate = await planner_estimate(db, rows)
    return Total(max(cap + 1, estimate or 0), is_estimate=True)
