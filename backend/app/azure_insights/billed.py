"""Billed vs estimated: Azure's billed cost next to AskDIQ's own estimate.

Read model of ``GET /admin/azure/costs`` and the cost alert rules; reads
Postgres only (``azure_billed_costs`` + the usage tables), never Azure.

- **Estimate** per UTC day: answered chats (``chat_messages.cost_usd``),
  ingestion (``ingest`` audit ``detail.cost_usd``) and the insights
  classifier (``insight_llm_usage.cost_usd``), in USD at the model prices.
- **Currency**: a USD bill compares directly. A non-USD bill (e.g. AUD) is
  compared with the estimate re-priced from the stored Azure retail prices
  in that currency (``azure_retail_prices``, Phase 1): token sums per role x
  retail price per million, ingestion scaled by the retail embedding price
  ratio. Without those prices both figures are shown and no variance is
  computed (``currency_mismatch``); FX is never guessed.
- **Status** per day: ``final`` (month closed + 3 days), ``estimated``
  (billed, can still be rerated), ``pending`` (not billed yet: Azure lags
  8-72 h; yesterday and today are always pending), ``no_data`` (before the
  first poll). Only ``closed`` days (72 h+ and billed) count for drift.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, cast, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights import status as sync_status
from app.azure_insights.cost_schemas import (
    AzureCostsRead,
    BilledDay,
    CostsPreset,
    CostsRange,
    CostTotals,
    DayStatus,
    MonthToDate,
)
from app.azure_insights.costs import (
    COSTS_KIND,
    COSTS_SOURCE,
    is_final_day,
    month_start,
    previous_month_start,
)
from app.core.config import Settings, get_settings
from app.core.logging_config import get_logger
from app.dashboard.queries import daily_cost
from app.dashboard.snapshots import Gather, register
from app.dashboard.usage_series import (
    PER_MILLION,
    ingestion_cost_rows,
    stage_costs,
    stage_token_rows,
)
from app.jobs.queue import utcnow
from app.models.db_models import AzureBilledCost, InsightLlmUsage

logger = get_logger(__name__)

USD = "USD"
# Every caller of the resource is billed: no audience filter.
AUDIENCE = "overview"
LAG_NOTE = "Azure billed cost lags 8-72 h and can be rerated until the month closes."
# Yesterday and today are always pending (partial in Azure).
PENDING_DAYS = 1
# A billed day this old counts as closed (drift alerts).
CLOSED_AFTER_DAYS = 3
ROLLING_PRESETS: dict[str, int] = {"24h": 2, "7d": 7, "30d": 30, "90d": 90}
PRESETS: tuple[CostsPreset, ...] = ("24h", "7d", "30d", "90d", "mtd", "last_month")
DEFAULT_PRESET: CostsPreset = "30d"
_ROLES = ("prompt", "cached", "output", "embed")
_MONEY_DP = 6
_PCT_DP = 2


# ---------------------------------------------------------------------------
# Ranges
# ---------------------------------------------------------------------------


def preset_range(preset: str, today: date) -> tuple[date, date]:
    """Inclusive UTC days of a preset (rolling presets end today)."""
    if preset in ROLLING_PRESETS:
        return today - timedelta(days=ROLLING_PRESETS[preset] - 1), today
    if preset == "mtd":
        return month_start(today), today
    if preset == "last_month":
        first = previous_month_start(today)
        return first, month_start(today) - timedelta(days=1)
    raise ValueError(f"unknown preset {preset!r}")


def range_params(
    preset: str | None, from_date: date | None, to_date: date | None, today: date
) -> dict[str, Any]:
    """Snapshot params: a preset (kept warm) or a custom range. A custom
    range equal to a preset (what the shared range picker sends) maps to it."""
    if preset is not None:
        return {"range": preset}
    if from_date is None or to_date is None:
        return {"range": DEFAULT_PRESET}
    for name in PRESETS:
        if preset_range(name, today) == (from_date, to_date):
            return {"range": name}
    return {"from": from_date.isoformat(), "to": to_date.isoformat()}


def resolve_params(
    params: dict[str, Any], today: date
) -> tuple[str | None, date, date]:
    if "range" in params:
        start, end = preset_range(params["range"], today)
        return params["range"], start, end
    return None, date.fromisoformat(params["from"]), date.fromisoformat(params["to"])


# ---------------------------------------------------------------------------
# Estimate (Postgres only)
# ---------------------------------------------------------------------------


def _naive(day: date) -> datetime:
    return datetime(day.year, day.month, day.day)  # noqa: DTZ001 - naive UTC columns


def _by_date(rows: dict[datetime, Any]) -> dict[date, Any]:
    return {k.date(): v for k, v in rows.items()}


def _add(target: dict[date, float], extra: dict[date, float]) -> None:
    for day, value in extra.items():
        target[day] = target.get(day, 0.0) + float(value)


async def _insights_by_day(
    db: AsyncSession, start: date, end: date
) -> dict[date, tuple[float, float, float]]:
    """(cost_usd, prompt_tokens, completion_tokens) of the classifier per UTC day."""
    day = cast(func.timezone("UTC", InsightLlmUsage.created_at), Date)
    stmt = (
        select(
            day,
            func.coalesce(func.sum(InsightLlmUsage.cost_usd), 0.0),
            func.coalesce(func.sum(InsightLlmUsage.prompt_tokens), 0),
            func.coalesce(func.sum(InsightLlmUsage.completion_tokens), 0),
        )
        .where(
            InsightLlmUsage.created_at
            >= datetime.combine(start, datetime.min.time(), UTC),
            InsightLlmUsage.created_at
            < datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC),
        )
        .group_by(day)
    )
    return {
        row[0]: (float(row[1]), float(row[2]), float(row[3]))
        for row in (await db.execute(stmt)).all()
    }


async def usd_estimate(db: AsyncSession, start: date, end: date) -> dict[date, float]:
    """AskDIQ's estimate (USD) per UTC day in ``[start, end]``."""
    lo, hi = _naive(start), _naive(end + timedelta(days=1))
    out: dict[date, float] = {}
    _add(out, await daily_cost(db, AUDIENCE, lo, hi))
    ingest = await ingestion_cost_rows(
        db, AUDIENCE, "day", lo, hi - timedelta(microseconds=1)
    )
    _add(out, _by_date(ingest))
    _add(out, {d: v[0] for d, v in (await _insights_by_day(db, start, end)).items()})
    return out


@dataclass(frozen=True, slots=True)
class RetailCard:
    """Per-million prices in one currency (a ``RateCard``)."""

    price_prompt_per_million: float
    price_cached_per_million: float
    price_output_per_million: float
    price_embed_per_million: float


_RETAIL_PRICE_SQL = text(
    """
    SELECT price_per_million FROM azure_retail_prices
    WHERE currency = :currency AND role = :role AND model_key = :model
      AND arm_region_name = :region AND price_per_million IS NOT NULL
      AND (deployment_type IS NULL OR deployment_type = :dtype)
      AND effective_start_date <= CURRENT_DATE
    ORDER BY effective_start_date DESC, fetched_at DESC
    LIMIT 1
    """
)


async def retail_card(
    db: AsyncSession, settings: Settings, currency: str
) -> RetailCard | None:
    """The latest effective stored retail price per role in ``currency``
    (``azure_retail_prices``, Phase 1), or None when any role's price is
    missing (never synced, or no meter for this model / deployment type)."""
    models = {
        "prompt": settings.azure_retail_llm_model,
        "cached": settings.azure_retail_llm_model,
        "output": settings.azure_retail_llm_model,
        "embed": settings.azure_retail_embedding_model,
    }
    prices: dict[str, float] = {}
    try:
        async with db.begin_nested():
            for role in _ROLES:
                value = await db.scalar(
                    _RETAIL_PRICE_SQL,
                    {
                        "currency": currency,
                        "role": role,
                        "model": models[role],
                        "region": settings.azure_retail_region,
                        "dtype": settings.azure_deployment_type,
                    },
                )
                if value is None:
                    return None
                prices[role] = float(value)
    except DBAPIError:
        logger.warning("azure_retail_lookup_failed", exc_info=True)
        return None
    return RetailCard(
        price_prompt_per_million=prices["prompt"],
        price_cached_per_million=prices["cached"],
        price_output_per_million=prices["output"],
        price_embed_per_million=prices["embed"],
    )


async def repriced_estimate(  # pylint: disable=too-many-locals
    db: AsyncSession,
    start: date,
    end: date,
    card: RetailCard,
    usd_card: RetailCard,
) -> dict[date, float]:
    """The estimate in the card's currency: token sums x retail prices;
    ingestion (stored in USD) scaled by the retail embedding price ratio."""
    lo, hi = _naive(start), _naive(end + timedelta(days=1)) - timedelta(microseconds=1)
    tokens = _by_date(await stage_token_rows(db, AUDIENCE, "day", lo, hi))
    ingest = _by_date(await ingestion_cost_rows(db, AUDIENCE, "day", lo, hi))
    insights = await _insights_by_day(db, start, end)
    embed_ratio = (
        card.price_embed_per_million / usd_card.price_embed_per_million
        if usd_card.price_embed_per_million
        else 0.0
    )
    out: dict[date, float] = {}
    for day in {*tokens, *ingest, *insights}:
        stages = stage_costs(
            tokens.get(day, {}), ingest.get(day, 0.0) * embed_ratio, card
        )
        _, prompt, completion = insights.get(day, (0.0, 0.0, 0.0))
        classifier = (
            prompt * card.price_prompt_per_million
            + completion * card.price_output_per_million
        ) / PER_MILLION
        out[day] = sum(stages.values()) + classifier
    return out


@dataclass(slots=True)
class Estimate:
    by_day: dict[date, float]
    usd_by_day: dict[date, float]
    currency: str
    repriced: bool
    mismatch: bool
    note: str | None = None


EMBED_DERIVED_NOTE = (
    "The {cur} retail embedding price rounds to 0 at 4 decimals; embeddings "
    "are re-priced with the ratio of the {cur} and USD retail prompt prices."
)


def fill_rounded_embed(
    card: RetailCard, usd_card: RetailCard
) -> tuple[RetailCard, bool]:
    """Azure rounds non-USD retail prices to 4 decimals, so a cheap
    embedding meter can read 0. Then derive it from Microsoft's own prompt
    price ratio (both retail prices, no external FX rate)."""
    if (
        card.price_embed_per_million > 0
        or usd_card.price_embed_per_million <= 0
        or usd_card.price_prompt_per_million <= 0
    ):
        return card, False
    ratio = card.price_prompt_per_million / usd_card.price_prompt_per_million
    return (
        replace(card, price_embed_per_million=usd_card.price_embed_per_million * ratio),
        True,
    )


async def estimate_for(
    db: AsyncSession,
    settings: Settings,
    start: date,
    end: date,
    billing_currency: str | None,
) -> Estimate:
    """The estimate in the billing currency when possible (see module doc)."""
    usd = await usd_estimate(db, start, end)
    if billing_currency is None or billing_currency == USD:
        return Estimate(usd, usd, USD, repriced=False, mismatch=False)
    card = await retail_card(db, settings, billing_currency)
    usd_card = await retail_card(db, settings, USD) if card is not None else None
    if card is None or usd_card is None:
        return Estimate(usd, usd, USD, repriced=False, mismatch=True)
    card, derived = fill_rounded_embed(card, usd_card)
    repriced = await repriced_estimate(db, start, end, card, usd_card)
    note = EMBED_DERIVED_NOTE.format(cur=billing_currency) if derived else None
    return Estimate(
        repriced, usd, billing_currency, repriced=True, mismatch=False, note=note
    )


# ---------------------------------------------------------------------------
# Billed rows and day status
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BilledInfo:
    by_day: dict[date, float]
    currency: str | None
    billed_through: date | None


async def billed_info(db: AsyncSession, start: date, end: date) -> BilledInfo:
    stmt = (
        select(AzureBilledCost.usage_date, func.sum(AzureBilledCost.cost))
        .where(AzureBilledCost.usage_date.between(start, end))
        .group_by(AzureBilledCost.usage_date)
    )
    by_day = {row[0]: float(row[1]) for row in (await db.execute(stmt)).all()}
    latest = (
        await db.execute(
            select(AzureBilledCost.usage_date, AzureBilledCost.currency)
            .order_by(
                AzureBilledCost.usage_date.desc(), AzureBilledCost.fetched_at.desc()
            )
            .limit(1)
        )
    ).first()
    return BilledInfo(
        by_day,
        latest[1] if latest else None,
        latest[0] if latest else None,
    )


def day_status(
    day: date,
    today: date,
    through: date | None,
    covered_from: date | None,
) -> DayStatus:
    if covered_from is not None and day < covered_from:
        return "no_data"
    if through is None or day > through or day >= today - timedelta(days=PENDING_DAYS):
        return "pending"
    return "final" if is_final_day(day, today) else "estimated"


def _money(value: float) -> float:
    return round(value, _MONEY_DP)


def variance_pct(estimate: float, billed: float | None) -> float | None:
    """(estimate - billed) / billed x 100; None when billed is 0 or unknown."""
    if billed is None or billed <= 0:
        return None
    return round((estimate - billed) * 100 / billed, _PCT_DP)


def build_days(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    start: date,
    end: date,
    today: date,
    billed: BilledInfo,
    estimate: Estimate,
    covered_from: date | None,
) -> list[BilledDay]:
    comparable = not estimate.mismatch
    days: list[BilledDay] = []
    day = start
    while day <= end:
        status = day_status(day, today, billed.billed_through, covered_from)
        value = billed.by_day.get(day)
        if value is None and status in ("final", "estimated"):
            value = 0.0  # Azure reported the period: no charge that day
        est = _money(estimate.by_day.get(day, 0.0))
        show_variance = comparable and status in ("final", "estimated")
        days.append(
            BilledDay(
                date=day,
                billed=None if value is None else _money(value),
                estimate=est,
                estimate_usd=_money(estimate.usd_by_day.get(day, 0.0)),
                variance=_money(est - value)
                if show_variance and value is not None
                else None,
                variance_pct=variance_pct(est, value) if show_variance else None,
                status=status,
                closed=status in ("final", "estimated")
                and day <= today - timedelta(days=CLOSED_AFTER_DAYS),
            )
        )
        day += timedelta(days=1)
    return days


def totals(days: list[BilledDay], comparable: bool) -> CostTotals:
    billed_days = [d for d in days if d.status in ("final", "estimated")]
    billed = sum(d.billed or 0.0 for d in days if d.billed is not None)
    any_billed = any(d.billed is not None for d in days)
    est_billed = sum(d.estimate for d in billed_days)
    total_billed_days = sum(d.billed or 0.0 for d in billed_days)
    return CostTotals(
        billed=_money(billed) if any_billed else None,
        estimate=_money(sum(d.estimate for d in days)),
        estimate_billed_days=_money(est_billed),
        variance=_money(est_billed - total_billed_days)
        if comparable and billed_days
        else None,
        variance_pct=variance_pct(est_billed, total_billed_days)
        if comparable and billed_days
        else None,
        billed_days=len(billed_days),
        final=bool(days) and all(d.status == "final" for d in days),
    )


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------


def _covered_from(row: Any) -> date | None:
    raw = (row.detail or {}).get("covered_from") if row is not None else None
    try:
        return date.fromisoformat(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _state(settings: Settings, row: Any) -> tuple[str, str | None]:
    if not settings.azure_insights_configured:
        return "not_connected", None
    if row is None or row.state == sync_status.STATE_NEVER_RUN:
        return "never_run", None
    if row.state == sync_status.STATE_OK:
        return "ok", None
    return row.state, row.last_error_message


async def build_costs(  # pylint: disable=too-many-locals
    db: AsyncSession,
    settings: Settings,
    params: dict[str, Any],
    now: datetime | None = None,
) -> AzureCostsRead:
    now = now or utcnow()
    today = now.astimezone(UTC).date()
    preset, start, end = resolve_params(params, today)
    mtd_start = month_start(today)
    lo, hi = min(start, mtd_start), max(end, today)
    row = (await sync_status.read_all(db)).get(COSTS_SOURCE)
    covered = _covered_from(row)
    billed = await billed_info(db, lo, hi)
    estimate = await estimate_for(db, settings, lo, hi, billed.currency)
    comparable = not estimate.mismatch
    days = build_days(lo, hi, today, billed, estimate, covered)
    in_range = [d for d in days if start <= d.date <= end]
    in_month = [d for d in days if mtd_start <= d.date <= today]
    state, message = _state(settings, row)
    return AzureCostsRead(
        state=state,  # type: ignore[arg-type]
        message=message,
        range=CostsRange(preset=preset, from_date=start, to_date=end),  # type: ignore[arg-type]
        currency=billed.currency,
        estimate_currency=estimate.currency,
        repriced=estimate.repriced,
        currency_mismatch=estimate.mismatch,
        estimate_note=estimate.note,
        days=in_range,
        totals=totals(in_range, comparable),
        month_to_date=MonthToDate(
            month=f"{today:%Y-%m}", **totals(in_month, comparable).model_dump()
        ),
        billed_through=billed.billed_through,
        drift_threshold_pct=settings.alert_azure_drift_pct,
        drift_min=settings.alert_azure_drift_min,
        lag_note=LAG_NOTE,
        last_success_at=row.last_success_at if row else None,
        next_run_at=row.next_run_at
        if row and settings.azure_insights_configured
        else None,
        as_of=now,
    )


async def _costs_snapshot(gather: Gather, params: dict[str, Any]) -> dict:
    settings = get_settings()
    (payload,) = await gather(lambda db: build_costs(db, settings, params))
    return payload.model_dump(mode="json")


register(COSTS_KIND, _costs_snapshot, [{"range": p} for p in PRESETS])
