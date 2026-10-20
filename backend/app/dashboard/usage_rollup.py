"""Hourly rollup behind ``GET /admin/usage/summary`` (any range, same numbers).

``usage_hourly_rollup`` holds, per UTC hour of the user turn and client IP,
the sums the summary needs: exchange tokens / cost / outcomes (also split by
the assistant row's UTC day, for the month-end forecast), the latency samples
(kept as an array, so percentiles stay exact) and the ingestion audit rows.

Reads combine the final hours (before ``usage_rollup_state.synced_until``)
with a live aggregation of everything else in the range: partial edge hours
and the recent tail. Both sides use the same aggregation (``hour_rows``), so
a summary equals the one computed from the raw rows. The refresher's
maintenance hook (``sync_usage_rollup``) rolls hours once they are older than
``OVERLAP``; database triggers move ``synced_until`` back when older rows
change (``alembic/versions/144787ba01b1``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Float,
    Select,
    and_,
    case,
    cast,
    delete,
    func,
    insert,
    literal,
    literal_column,
    null,
    select,
    text,
    true,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.dashboard.queries import cached_tokens_sql
from app.dashboard.window import Bucket, to_naive_utc
from app.governance.audience import build_audience_filter
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    LatencyRecord,
    UsageHourlyRollup,
    UsageRollupState,
)

# Hours are rolled once they are this old (late assistant rows, latency).
OVERLAP = timedelta(minutes=30)
# One roll statement covers at most this span (bounded statement time).
CHUNK = timedelta(days=7)
# Chunks rolled per maintenance call (the backfill continues next cycle).
MAX_CHUNKS = 8
HOUR = timedelta(hours=1)
STATE_ID = 1
OUTCOMES = (
    "answered",
    "ungrounded",
    "blocked",
    "conversational",
    "llm_failed",
    "clarify",
)
_COUNT_COLUMNS = (
    "turns",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "reasoning_tokens",
    "embedding_tokens",
    "guardrail_tokens",
    "estimated_turns",
    *OUTCOMES,
    "ingest_rows",
)
_SUM_COLUMNS = ("cost_usd", "cached_tokens", "ingest_tokens", "ingest_cost")
COLUMNS = ("hour", "asst_day", "ip", *_COUNT_COLUMNS, *_SUM_COLUMNS, "latency_ms")

_USER = aliased(ChatMessageRecord, name="ru_user")
_ASST = aliased(ChatMessageRecord, name="ru_asst")
# Any message carrying the request id (the summary's latency join has no role filter).
_MSG = aliased(ChatMessageRecord, name="ru_msg")
_HOUR_SQL = literal_column("'hour'")
_DAY_SQL = literal_column("'day'")

TimeCond = Callable[[Any], Any]


def floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def ceil_hour(value: datetime) -> datetime:
    floor = floor_hour(value)
    return floor if floor == value else floor + HOUR


def _zero(type_: Any = None) -> Any:
    return literal(0.0, Float) if type_ is Float else literal(0)


def _json_number(value: Any) -> Any:
    """A ``json`` member as a float (0 when absent or not a number)."""
    return case(
        (func.json_typeof(value) == "number", cast(value.as_string(), Float)), else_=0.0
    )


def _exchange_rows(cond: TimeCond) -> Select:
    """Exchanges (assistant row + user row + session) per hour, day and IP."""
    hour = func.date_trunc(_HOUR_SQL, _USER.created_at)
    day = func.date_trunc(_DAY_SQL, _ASST.created_at)
    ip = func.coalesce(_USER.ip_address, ChatSession.created_ip)
    outcome_counts = [
        func.count(case((_ASST.outcome == name, 1), else_=None)) for name in OUTCOMES
    ]
    return (
        select(
            hour,
            day,
            ip,
            func.count(_ASST.id),
            func.coalesce(func.sum(_ASST.prompt_tokens), 0),
            func.coalesce(func.sum(_ASST.completion_tokens), 0),
            func.coalesce(func.sum(_ASST.total_tokens), 0),
            func.coalesce(func.sum(_ASST.reasoning_tokens), 0),
            func.coalesce(func.sum(_ASST.embedding_tokens), 0),
            func.coalesce(func.sum(_ASST.guardrail_tokens), 0),
            func.count(
                case(
                    (_ASST.token_details["is_actual"].as_string() == "false", 1),
                    else_=None,
                )
            ),
            *outcome_counts,
            _zero(),
            func.coalesce(func.sum(_ASST.cost_usd), 0.0),
            func.coalesce(func.sum(cached_tokens_sql(_ASST)), 0.0),
            _zero(Float),
            _zero(Float),
            cast(null(), ARRAY(Float)),
        )
        .select_from(_ASST)
        .join(
            _USER,
            and_(
                _USER.session_id == _ASST.session_id,
                _USER.turn_index == _ASST.turn_index,
                _USER.role == "user",
            ),
        )
        .join(ChatSession, _ASST.session_id == ChatSession.id)
        .where(_ASST.role == "assistant", cond(_USER.created_at))
        .group_by(hour, day, ip)
    )


def _latency_rows(cond: TimeCond) -> Select:
    """Latency samples per hour and IP, joined exactly as the summary joins them."""
    hour = func.date_trunc(_HOUR_SQL, _USER.created_at)
    ip = func.coalesce(_USER.ip_address, ChatSession.created_ip)
    zeros = [_zero() for _ in _COUNT_COLUMNS]
    return (
        select(
            hour,
            cast(null(), _USER.created_at.type),
            ip,
            *zeros,
            *(_zero(Float) for _ in _SUM_COLUMNS),
            func.array_agg(cast(LatencyRecord.total_ms, Float)),
        )
        .select_from(_MSG)
        .join(LatencyRecord, _MSG.request_id == LatencyRecord.request_id)
        .join(
            _USER,
            and_(
                _MSG.session_id == _USER.session_id,
                _MSG.turn_index == _USER.turn_index,
                _USER.role == "user",
            ),
        )
        .join(ChatSession, _MSG.session_id == ChatSession.id)
        .where(cond(_USER.created_at))
        .group_by(hour, ip)
    )


def _ingest_rows(cond: TimeCond) -> Select:
    """``ingest`` audit rows (embedding tokens and cost) per hour and IP."""
    hour = func.date_trunc(_HOUR_SQL, AuditLog.created_at)
    counts = [_zero() for _ in _COUNT_COLUMNS[:-1]]
    return (
        select(
            hour,
            cast(null(), AuditLog.created_at.type),
            AuditLog.ip_address,
            *counts,
            func.count(AuditLog.id),
            _zero(Float),
            _zero(Float),
            func.coalesce(
                func.sum(_json_number(AuditLog.detail["embedding_tokens"])), 0.0
            ),
            func.coalesce(func.sum(_json_number(AuditLog.detail["cost_usd"])), 0.0),
            cast(null(), ARRAY(Float)),
        )
        .where(AuditLog.action == "ingest", cond(AuditLog.created_at))
        .group_by(hour, AuditLog.ip_address)
    )


def hour_rows(cond: TimeCond) -> list[Select]:
    """Rollup-shaped rows of the raw data whose time column satisfies ``cond``."""
    return [_exchange_rows(cond), _latency_rows(cond), _ingest_rows(cond)]


# ---------------------------------------------------------------------------
# Maintenance (refresher)
# ---------------------------------------------------------------------------

_TARGET_SQL = text(
    "SELECT date_trunc('hour', (clock_timestamp() AT TIME ZONE 'UTC')"
    " - make_interval(secs => :overlap))"
)
_FIRST_SQL = text(
    """
    SELECT date_trunc('hour', LEAST(
        (SELECT min(created_at) FROM chat_messages),
        (SELECT min(created_at) FROM audit_log WHERE action = 'ingest')))
    """
)


async def roll(session: AsyncSession, start: datetime, stop: datetime) -> None:
    """Replace the rollup rows of the hours in ``[start, stop)`` (no commit)."""

    def window(col: Any) -> Any:
        return and_(col >= start, col < stop)

    await session.execute(
        delete(UsageHourlyRollup).where(
            UsageHourlyRollup.hour >= start, UsageHourlyRollup.hour < stop
        )
    )
    await session.execute(
        insert(UsageHourlyRollup).from_select(
            list(COLUMNS), union_all(*hour_rows(window))
        )
    )


async def sync_usage_rollup(session: AsyncSession) -> int:
    """Roll the hours older than ``OVERLAP`` that are not final yet; returns
    the chunks committed. Safe to run concurrently with writers: a roll only
    commits when ``usage_rollup_state`` did not change meanwhile."""
    rolled = 0
    for _ in range(MAX_CHUNKS):
        target = await session.scalar(_TARGET_SQL, {"overlap": OVERLAP.total_seconds()})
        state = await session.get(UsageRollupState, STATE_ID, populate_existing=True)
        if state is None:
            first = await session.scalar(_FIRST_SQL)
            await session.execute(
                pg_insert(UsageRollupState)
                .values(id=STATE_ID, synced_until=first or target, version=0)
                .on_conflict_do_nothing()
            )
            await session.commit()
            continue
        start, version = state.synced_until, state.version
        if start >= target:
            break
        stop = min(start + CHUNK, target)
        await roll(session, start, stop)
        moved = await session.execute(
            update(UsageRollupState)
            .where(
                UsageRollupState.id == STATE_ID,
                UsageRollupState.version == version,
                UsageRollupState.synced_until == start,
            )
            .values(synced_until=stop, version=version + 1)
        )
        if moved.rowcount != 1:  # type: ignore[attr-defined]
            await session.rollback()
            continue
        await session.commit()
        rolled += 1
    return rolled


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


def _synced_until() -> Any:
    """``synced_until`` (−infinity before the first roll) as a scalar subquery."""
    return func.coalesce(
        select(UsageRollupState.synced_until)
        .where(UsageRollupState.id == STATE_ID)
        .scalar_subquery(),
        literal_column("'-infinity'::timestamp"),
    )


def range_rows(lo: datetime | None, hi: datetime | None) -> Any:
    """Rollup-shaped rows covering exactly ``lo <= t <= hi`` (either end open).

    Final whole hours come from the rollup; the partial first hour and the
    hours from ``min(last whole hour, synced_until)`` on are aggregated live.
    """
    lo = to_naive_utc(lo) if lo is not None else None
    hi = to_naive_utc(hi) if hi is not None else None
    first_full = ceil_hour(lo) if lo is not None else None
    end_full = floor_hour(hi + timedelta(microseconds=1)) if hi is not None else None
    synced = _synced_until()

    roll = UsageHourlyRollup
    stored = select(*(getattr(roll, name) for name in COLUMNS)).where(
        roll.hour < synced
    )
    if first_full is not None:
        stored = stored.where(roll.hour >= first_full)
    if end_full is not None:
        stored = stored.where(roll.hour < end_full)
    parts: list[Select] = [stored]

    if lo is not None and first_full is not None and first_full > lo:

        def head(col: Any) -> Any:
            cond = and_(col >= lo, col < first_full)
            return cond if hi is None else and_(cond, col <= hi)

        parts += hour_rows(head)

    live_from = synced if end_full is None else func.least(literal(end_full), synced)
    if first_full is not None:
        live_from = func.greatest(literal(first_full), live_from)

    def tail(col: Any) -> Any:
        cond = col >= live_from
        return cond if hi is None else and_(cond, col <= hi)

    parts += hour_rows(tail)
    return union_all(*parts).subquery("usage_rows")


async def _scoped(db: AsyncSession, rows: Any, audience: str, stmt: Select) -> Select:
    aud = await build_audience_filter(db, rows.c.ip, audience)
    return stmt if aud is None else stmt.where(aud)


@dataclass(frozen=True, slots=True)
class Totals:  # pylint: disable=too-many-instance-attributes
    """The summary's exchange, ingestion and outcome sums."""

    total_turns: int
    sum_prompt: int
    sum_completion: int
    sum_total_tokens: int
    sum_reasoning: int
    sum_query_embed: int
    sum_guardrail_embed: int
    sum_cost: float
    sum_cached: float
    estimated_turns: int
    outcomes: dict[str, int]
    ingest_tokens: int
    ingest_cost: float


async def totals(
    db: AsyncSession, audience: str, lo: datetime | None, hi: datetime | None
) -> Totals:
    rows = range_rows(lo, hi)
    c = rows.c
    names = (*_COUNT_COLUMNS, *_SUM_COLUMNS)
    stmt = select(*(func.coalesce(func.sum(getattr(c, n)), 0) for n in names))
    row = (await db.execute(await _scoped(db, rows, audience, stmt))).one()
    v = dict(zip(names, row, strict=True))
    return Totals(
        total_turns=int(v["turns"]),
        sum_prompt=int(v["prompt_tokens"]),
        sum_completion=int(v["completion_tokens"]),
        sum_total_tokens=int(v["total_tokens"]),
        sum_reasoning=int(v["reasoning_tokens"]),
        sum_query_embed=int(v["embedding_tokens"]),
        sum_guardrail_embed=int(v["guardrail_tokens"]),
        sum_cost=float(v["cost_usd"]),
        sum_cached=float(v["cached_tokens"]),
        estimated_turns=int(v["estimated_turns"]),
        outcomes={name: int(v[name]) for name in OUTCOMES},
        ingest_tokens=int(v["ingest_tokens"]),
        ingest_cost=float(v["ingest_cost"]),
    )


async def latency(
    db: AsyncSession, audience: str, lo: datetime | None, hi: datetime | None
) -> tuple[float, float, float]:
    """``(avg, p50, p95)`` of the latency samples (0 when there are none)."""
    rows = range_rows(lo, hi)
    samples = (
        func.unnest(rows.c.latency_ms)
        .table_valued("ms")
        .render_derived(name="samples")
        .lateral()
    )
    sample = samples.c.ms
    stmt = (
        select(
            func.coalesce(func.avg(sample), 0.0),
            func.coalesce(func.percentile_cont(0.50).within_group(sample), 0.0),
            func.coalesce(func.percentile_cont(0.95).within_group(sample), 0.0),
        )
        .select_from(rows)
        .join(samples, true())
    )
    row = (await db.execute(await _scoped(db, rows, audience, stmt))).one()
    return float(row[0]), float(row[1]), float(row[2])


@dataclass(frozen=True, slots=True)
class StageBuckets:
    """Per bucket: token sums (buckets with exchanges) and ingestion cost."""

    tokens: dict[datetime, dict[str, float]]
    ingestion: dict[datetime, float]


async def stage_buckets(
    db: AsyncSession,
    audience: str,
    bucket: Bucket,
    lo: datetime | None,
    hi: datetime | None,
) -> StageBuckets:
    rows = range_rows(lo, hi)
    c = rows.c
    b = func.date_trunc(_HOUR_SQL if bucket == "hour" else _DAY_SQL, c.hour)
    stmt = select(
        b,
        func.sum(c.turns),
        func.sum(c.prompt_tokens),
        func.sum(c.completion_tokens),
        func.sum(c.reasoning_tokens),
        func.sum(c.embedding_tokens),
        func.sum(c.guardrail_tokens),
        func.sum(c.cached_tokens),
        func.sum(c.ingest_rows),
        func.sum(c.ingest_cost),
    ).select_from(rows)
    stmt = (await _scoped(db, rows, audience, stmt)).group_by(b)
    tokens: dict[datetime, dict[str, float]] = {}
    ingestion: dict[datetime, float] = {}
    for row in (await db.execute(stmt)).all():
        if row[1]:
            tokens[row[0]] = {
                "prompt": float(row[2]),
                "completion": float(row[3]),
                "reasoning": float(row[4]),
                "query_embed": float(row[5]),
                "guardrail_embed": float(row[6]),
                "cached": float(row[7]),
            }
        if row[8]:
            ingestion[row[0]] = float(row[9])
    return StageBuckets(tokens, ingestion)


# A turn's user row precedes its assistant row by far less than this.
_TURN_SPAN = timedelta(days=1)


async def daily_cost(
    db: AsyncSession, audience: str, start: datetime, end: datetime
) -> dict[date, float]:
    """Exchange cost per UTC day of the assistant row in ``[start, end)``
    (``app.dashboard.queries.daily_cost``; ``start`` / ``end`` are midnights)."""
    rows = range_rows(start - _TURN_SPAN, None)
    c = rows.c
    stmt = (
        select(c.asst_day, func.sum(c.cost_usd))
        .select_from(rows)
        .where(c.asst_day >= start, c.asst_day < end)
    )
    stmt = (await _scoped(db, rows, audience, stmt)).group_by(c.asst_day)
    return {row[0].date(): float(row[1]) for row in (await db.execute(stmt)).all()}
