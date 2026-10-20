"""Read schemas of ``GET /admin/azure/costs`` (Billed vs estimated)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

CostsState = Literal[
    "ok",
    "not_connected",
    "never_run",
    "unsupported",
    "error",
    "throttled",
    "budget_exhausted",
]
DayStatus = Literal["final", "estimated", "pending", "no_data"]
CostsPreset = Literal["24h", "7d", "30d", "90d", "mtd", "last_month"]


class CostsRange(BaseModel):
    preset: CostsPreset | None = Field(
        default=None, description="Preset, or null for a custom range."
    )
    from_date: date = Field(description="First UTC day (inclusive).")
    to_date: date = Field(description="Last UTC day (inclusive).")


class BilledDay(BaseModel):
    date: date
    billed: float | None = Field(
        default=None,
        description="Azure billed cost (billing currency); null when not billed yet.",
    )
    estimate: float = Field(
        description="AskDIQ estimate in `estimate_currency` (live, per answer)."
    )
    estimate_usd: float = Field(description="AskDIQ estimate in USD (model prices).")
    variance: float | None = Field(
        default=None, description="estimate - billed (null when not comparable)."
    )
    variance_pct: float | None = Field(
        default=None, description="(estimate - billed) / billed x 100."
    )
    status: DayStatus = Field(
        description=(
            "`final`: month closed + 3 days; `estimated`: billed but can still "
            "be rerated; `pending`: not billed yet (Azure lags 8-72 h); "
            "`no_data`: before the first poll."
        )
    )
    closed: bool = Field(
        description="At least 72 h old and billed: counts for drift alerts."
    )


class CostTotals(BaseModel):
    billed: float | None = Field(default=None, description="Sum of billed days.")
    estimate: float = Field(description="Estimate over every day (incl. pending).")
    estimate_billed_days: float = Field(
        description="Estimate over the billed (comparable) days only."
    )
    variance: float | None = None
    variance_pct: float | None = Field(
        default=None, description="On the billed days only."
    )
    billed_days: int = 0
    final: bool = Field(description="Every day is final.")


class MonthToDate(CostTotals):
    month: str = Field(description="UTC month, `YYYY-MM`.")


class AzureCostsRead(BaseModel):
    """The Usage & Cost "Billed vs estimated" card."""

    state: CostsState
    message: str | None = Field(
        default=None, description="Sanitised reason for a non-ok state."
    )
    range: CostsRange
    currency: str | None = Field(
        default=None, description="Billing currency (null until billed data exists)."
    )
    estimate_currency: str = Field(description="Currency of `estimate` values.")
    repriced: bool = Field(
        description=(
            "The estimate is re-priced with Azure retail prices in the billing "
            "currency (non-USD bill)."
        )
    )
    currency_mismatch: bool = Field(
        description=(
            "Billing currency differs from the estimate's and no retail prices "
            "in it exist: both are shown, no variance (no FX guessing)."
        )
    )
    estimate_note: str | None = Field(
        default=None, description="How the estimate was re-priced, when notable."
    )
    days: list[BilledDay]
    totals: CostTotals
    month_to_date: MonthToDate
    billed_through: date | None = Field(
        default=None, description="Latest UTC day Azure has billed."
    )
    drift_threshold_pct: float
    drift_min: float
    lag_note: str
    last_success_at: datetime | None = None
    next_run_at: datetime | None = None
    as_of: datetime = Field(description="When this payload was computed.")
