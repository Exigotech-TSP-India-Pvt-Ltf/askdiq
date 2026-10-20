"""``azure_sync_status``: per-source run status and the monthly call budget.

Every write is an upsert on ``source`` (idempotent). Error text is stored
already sanitised (:mod:`app.azure_insights.errors`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights.client import CallBudget
from app.azure_insights.errors import (
    AzureBudgetExhaustedError,
    AzureInsightsError,
    AzureThrottledError,
    AzureUnsupportedError,
    sanitize,
    sanitize_code,
)
from app.core.concurrency import SessionFactory
from app.jobs.queue import utcnow
from app.models.db_models import AzureSyncStatus

STATE_NEVER_RUN = "never_run"
STATE_OK = "ok"
STATE_ERROR = "error"
STATE_THROTTLED = "throttled"
STATE_BUDGET_EXHAUSTED = "budget_exhausted"
# The API is not offered for this subscription type (e.g. Cost Management
# on classic CSP); waits for the next slot like an error, without a streak.
STATE_UNSUPPORTED = "unsupported"
_NO_STREAK = frozenset({STATE_THROTTLED, STATE_UNSUPPORTED})


async def _upsert(session: AsyncSession, source: str, values: dict[str, Any]) -> None:
    values = {**values, "updated_at": utcnow()}
    stmt = insert(AzureSyncStatus).values(source=source, **values)
    await session.execute(
        stmt.on_conflict_do_update(index_elements=[AzureSyncStatus.source], set_=values)
    )


async def mark_started(session: AsyncSession, source: str, now: datetime) -> None:
    await _upsert(session, source, {"last_run_at": now})


async def record_success(
    session: AsyncSession,
    source: str,
    *,
    now: datetime,
    detail: dict[str, Any] | None = None,
) -> None:
    values: dict[str, Any] = {
        "state": STATE_OK,
        "last_success_at": now,
        "consecutive_failures": 0,
    }
    if detail is not None:
        values["detail"] = detail
    await _upsert(session, source, values)


def error_state(error: BaseException) -> str:
    if isinstance(error, AzureBudgetExhaustedError):
        return STATE_BUDGET_EXHAUSTED
    if isinstance(error, AzureThrottledError):
        return STATE_THROTTLED
    if isinstance(error, AzureUnsupportedError):
        return STATE_UNSUPPORTED
    return STATE_ERROR


def error_parts(error: BaseException) -> tuple[str, str]:
    """(code, message), sanitised; unexpected exceptions show only their type."""
    if isinstance(error, AzureInsightsError):
        return error.code, error.message
    return sanitize_code(type(error).__name__), "Unexpected error (see server logs)"


async def record_failure(
    session: AsyncSession, source: str, error: BaseException, *, now: datetime
) -> None:
    """A failed (or throttled / capped / unsupported) run; throttling and
    "unsupported" are not a failure streak."""
    code, message = error_parts(error)
    state = error_state(error)
    values: dict[str, Any] = {
        "state": state,
        "last_error_at": now,
        "last_error_code": code,
        "last_error_message": sanitize(message),
    }
    if state not in _NO_STREAK:
        values["consecutive_failures"] = AzureSyncStatus.consecutive_failures + 1
    stmt = insert(AzureSyncStatus).values(
        source=source,
        **{
            **values,
            "consecutive_failures": 0 if state in _NO_STREAK else 1,
            "updated_at": now,
        },
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[AzureSyncStatus.source],
            set_={**values, "updated_at": now},
        )
    )


async def set_next_run(
    session: AsyncSession, source: str, when: datetime | None
) -> None:
    await _upsert(session, source, {"next_run_at": when})


async def read_all(session: AsyncSession) -> dict[str, AzureSyncStatus]:
    rows = (await session.execute(select(AzureSyncStatus))).scalars().all()
    return {row.source: row for row in rows}


_CONSUME_SQL = text(
    """
    INSERT INTO azure_sync_status (source, calls_month, calls_this_month, updated_at)
    VALUES (:source, :month, 1, :now)
    ON CONFLICT (source) DO UPDATE SET
        calls_this_month = CASE
            WHEN azure_sync_status.calls_month = :month
            THEN azure_sync_status.calls_this_month + 1 ELSE 1 END,
        calls_month = :month,
        updated_at = :now
    WHERE azure_sync_status.calls_month IS DISTINCT FROM :month
       OR azure_sync_status.calls_this_month < :cap
    RETURNING calls_this_month
    """
)


def month_start(day: date) -> date:
    return day.replace(day=1)


async def consume_call(
    session: AsyncSession, source: str, cap: int, *, now: datetime | None = None
) -> int:
    """Atomically count one call for ``source`` this (UTC) month; raises
    :class:`AzureBudgetExhaustedError` instead when the cap is reached.

    One statement, so concurrent workers can never exceed the cap together.
    The caller commits."""
    if cap <= 0:
        raise AzureBudgetExhaustedError(source, cap)
    now = now or utcnow()
    used = await session.scalar(
        _CONSUME_SQL,
        {"source": source, "month": month_start(now.date()), "cap": cap, "now": now},
    )
    if used is None:
        raise AzureBudgetExhaustedError(source, cap)
    return int(used)


def call_budget(source: str, cap: int, session_factory: SessionFactory) -> CallBudget:
    """A :data:`app.azure_insights.client.CallBudget` that commits each call
    in its own transaction (the count survives a failed run)."""

    async def _consume() -> None:
        async with session_factory() as session:
            await consume_call(session, source, cap)
            await session.commit()

    return _consume


def calls_this_month(row: AzureSyncStatus | None, today: date) -> int:
    """The counter, or 0 when it belongs to an earlier month."""
    if row is None or row.calls_month != month_start(today):
        return 0
    return int(row.calls_this_month or 0)
