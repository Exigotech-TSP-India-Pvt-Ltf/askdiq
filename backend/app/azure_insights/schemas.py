"""Read schemas of the Azure Insights API."""

from __future__ import annotations

from datetime import datetime
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
