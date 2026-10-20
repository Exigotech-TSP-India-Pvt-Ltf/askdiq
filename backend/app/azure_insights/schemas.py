"""Read schemas of the Azure Insights API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceState = Literal[
    "ok", "error", "throttled", "budget_exhausted", "never_run", "not_configured"
]


class AzureSourceStatus(BaseModel):
    source: str = Field(description="Source id, e.g. `retail`, `metrics`.")
    label: str
    scheduled: bool = Field(description="Polled in the background.")
    needs_credentials: bool
    interval_seconds: int
    state: SourceState
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_code: str | None = Field(default=None, description="Sanitised.")
    last_error_message: str | None = Field(default=None, description="Sanitised.")
    consecutive_failures: int = 0
    next_run_at: datetime | None = None
    calls_this_month: int = 0
    call_cap: int | None = Field(
        default=None, description="Monthly call cap (metrics only)."
    )


class AzureStatusRead(BaseModel):
    """The Governance "Azure connection" card."""

    connected: bool = Field(
        description="AZURE_INSIGHTS_ENABLED=true with every id configured."
    )
    reason: Literal["not_configured"] | None = None
    region: str | None = None
    deployment_type: str
    metrics_calls_this_month: int = 0
    metrics_call_cap: int
    sources: list[AzureSourceStatus]
    generated_at: datetime


class OfficialPriceRead(BaseModel):
    role: Literal["prompt", "cached", "output", "embed"]
    field: str = Field(description="The model_settings price field it fills.")
    model_key: str
    meter_name: str | None = Field(
        default=None, description="Mapped retail meter; null = no mapping."
    )
    found: bool = Field(description="An official price is stored for the meter.")
    usd_per_million: float | None = None
    local_per_million: float | None = Field(
        default=None, description="In `local_currency` (for reference only)."
    )
    effective_date: date | None = None


class OfficialPricesRead(BaseModel):
    """Latest stored Azure retail prices for the configured models."""

    source: Literal["azure_retail"] = "azure_retail"
    region: str
    deployment_type: str
    local_currency: str
    synced_at: datetime | None = Field(
        default=None, description="When the prices were last fetched from Azure."
    )
    effective_date: date | None = Field(
        default=None, description="Newest effective date among the prices."
    )
    prices: list[OfficialPriceRead]
