"""Usage & Cost chart additions (B7.4): cost by stage over time, the month-end
forecast and prompt-cache statistics for ``GET /admin/usage/summary``.

Stage costs use the same token sums and per-million rates as the summary's
``financial.stage_costs``, per bucket, so the series adds up to the totals.
Both read the hourly rollup (``app.dashboard.usage_rollup``); the raw-row
aggregations below (``stage_token_rows``, ``ingestion_cost_rows``) are the
reference the rollup is checked against.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.dashboard import usage_rollup
from app.dashboard.queries import (
    ASST,
    USER_TURN,
    cached_tokens_sql,
    pairs,
    scope_pairs,
    trunc,
)
from app.dashboard.window import (
    Bucket,
    bucket_for,
    bucket_label,
    bucket_starts,
    money,
    month_bounds,
    month_forecast,
    ratio,
    to_naive_utc,
    truncate,
)
from app.governance.audience import build_audience_filter
from app.governance.costing import RateCard
from app.model_settings.service import current_model_settings
from app.models.db_models import AuditLog

PER_MILLION = 1_000_000
_TOKEN_KEYS = ("prompt", "completion", "reasoning", "query_embed", "guardrail_embed")


def series_bucket(start: datetime | None, end: datetime) -> Bucket:
    """Hourly for a bounded range of 48 h or less, else daily."""
    return "day" if start is None else bucket_for(end - start)


async def stage_token_rows(
    db: AsyncSession,
    audience: str,
    bucket: Bucket,
    start: datetime | None,
    end: datetime | None,
) -> dict[datetime, dict[str, float]]:
    """Token sums per bucket over the summary's filters (user turn time, inclusive ``end``)."""
    b = trunc(bucket, USER_TURN.created_at)
    stmt = pairs(
        b,
        func.coalesce(func.sum(ASST.prompt_tokens), 0),
        func.coalesce(func.sum(ASST.completion_tokens), 0),
        func.coalesce(func.sum(ASST.reasoning_tokens), 0),
        func.coalesce(func.sum(ASST.embedding_tokens), 0),
        func.coalesce(func.sum(ASST.guardrail_tokens), 0),
        func.coalesce(func.sum(cached_tokens_sql(ASST)), 0.0),
    )
    if start is not None:
        stmt = stmt.where(USER_TURN.created_at >= start)
    if end is not None:
        stmt = stmt.where(USER_TURN.created_at <= end)
    stmt = (await scope_pairs(db, audience, stmt)).group_by(b)
    out: dict[datetime, dict[str, float]] = {}
    for row in (await db.execute(stmt)).all():
        values = dict(zip(_TOKEN_KEYS, (float(v) for v in row[1:6]), strict=True))
        values["cached"] = float(row[6])
        out[row[0]] = values
    return out


async def ingestion_cost_rows(
    db: AsyncSession,
    audience: str,
    bucket: Bucket,
    start: datetime | None,
    end: datetime | None,
) -> dict[datetime, float]:
    """``ingest`` audit rows' ``detail.cost_usd`` per bucket (as the summary sums it)."""
    cost = cast(AuditLog.detail, JSONB)["cost_usd"]
    value = case(
        (func.jsonb_typeof(cost) == "number", cast(cost.astext, Float)), else_=0.0
    )
    b = trunc(bucket, AuditLog.created_at)
    stmt = select(b, func.coalesce(func.sum(value), 0.0)).where(
        AuditLog.action == "ingest"
    )
    if start is not None:
        stmt = stmt.where(AuditLog.created_at >= start)
    if end is not None:
        stmt = stmt.where(AuditLog.created_at <= end)
    aud = await build_audience_filter(db, AuditLog.ip_address, audience)
    if aud is not None:
        stmt = stmt.where(aud)
    stmt = stmt.group_by(b)
    return {row[0]: float(row[1]) for row in (await db.execute(stmt)).all()}


def stage_costs(
    tokens: dict[str, float], ingestion: float, cfg: RateCard
) -> dict[str, float]:
    """USD per stage for one bucket (the summary's formulas, cache split out)."""
    prompt = tokens.get("prompt", 0.0)
    cached = min(tokens.get("cached", 0.0), prompt)
    completion = tokens.get("completion", 0.0)
    reasoning = tokens.get("reasoning", 0.0)
    return {
        "query_embed": money(
            tokens.get("query_embed", 0.0) * cfg.price_embed_per_million / PER_MILLION
        ),
        "guardrail_embed": money(
            tokens.get("guardrail_embed", 0.0)
            * cfg.price_embed_per_million
            / PER_MILLION
        ),
        "prompt": money((prompt - cached) * cfg.price_prompt_per_million / PER_MILLION),
        "cached_prompt": money(cached * cfg.price_cached_per_million / PER_MILLION),
        "reasoning": money(reasoning * cfg.price_output_per_million / PER_MILLION),
        "visible": money(
            max(0.0, completion - reasoning)
            * cfg.price_output_per_million
            / PER_MILLION
        ),
        "ingestion": money(ingestion),
    }


def series_points(
    bucket: Bucket,
    starts: list[datetime],
    tokens: dict[datetime, dict[str, float]],
    ingestion: dict[datetime, float],
    cfg: RateCard,
) -> list[dict[str, Any]]:
    return [
        {
            "t": bucket_label(s, bucket),
            **stage_costs(tokens.get(s, {}), ingestion.get(s, 0.0), cfg),
        }
        for s in starts
    ]


def zero_fill_starts(
    bucket: Bucket,
    start: datetime | None,
    end: datetime,
    seen: list[datetime],
    max_days: int,
) -> list[datetime]:
    """Bucket starts from ``start`` (or the first bucket with data) to ``end``."""
    first = start if start is not None else (min(seen) if seen else None)
    if first is None:
        return []
    floor = end - timedelta(days=max_days)
    first = max(first, floor)
    return bucket_starts(
        truncate(first, bucket), end + timedelta(microseconds=1), bucket
    )


async def cost_by_stage_series(
    db: AsyncSession,
    audience: str,
    start: datetime | None,
    end: datetime | None,
    now: datetime,
    cfg: Settings,
) -> dict[str, Any]:
    start = to_naive_utc(start) if start is not None else None
    end = to_naive_utc(end) if end is not None else None
    stop = end or now
    bucket = series_bucket(start, stop)
    rows = await usage_rollup.stage_buckets(db, audience, bucket, start, end)
    starts = zero_fill_starts(
        bucket, start, stop, [*rows.tokens, *rows.ingestion], cfg.usage_max_range_days
    )
    return {
        "bucket": bucket,
        "points": series_points(
            bucket, starts, rows.tokens, rows.ingestion, current_model_settings()
        ),
    }


async def month_cost_forecast(
    db: AsyncSession, audience: str, now: datetime, cfg: Settings
) -> dict[str, Any]:
    """Current UTC month: MTD exchange cost and the linear month-end projection."""
    month_start, month_end = month_bounds(now.date())
    daily = await usage_rollup.daily_cost(db, audience, month_start, month_end)
    forecast = month_forecast(daily, now.date(), cfg.monthly_cost_budget_usd)
    return {
        "currency": "USD",
        "month": forecast.month,
        "method": "linear",
        "mtd_usd": forecast.mtd_usd,
        "forecast_month_end_usd": forecast.forecast_month_end_usd,
        "budget_usd": forecast.budget_usd,
        "daily": forecast.daily,
    }


def cache_stats(cached_tokens: int, prompt_tokens: int) -> dict[str, Any]:
    return {
        "cached_prompt_tokens": cached_tokens,
        "prompt_tokens": prompt_tokens,
        "hit_ratio": ratio(cached_tokens, prompt_tokens),
    }
