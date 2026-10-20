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


# ---------------------------------------------------------------------------
# Monitor metrics + reconciliation (Phase 2)
# ---------------------------------------------------------------------------

DeploymentRole = Literal["llm", "embedding", "other"]
MetricsRange = Literal["1h", "6h", "24h", "7d", "30d", "90d"]
MetricsGrain = Literal["PT1M", "PT1H"]
MetricName = Literal[
    "ProcessedPromptTokens",
    "GeneratedTokens",
    "TokenTransaction",
    "AzureOpenAIRequests",
    "AzureOpenAITTLTInMS",
    "AzureOpenAINormalizedTTFTInMS",
    "AzureOpenAIAvailabilityRate",
    "FoundryModelEstimatedCost",
]


class ReconciliationCell(BaseModel):
    """One measure: Azure-side vs AskDIQ-recorded."""

    azure: float | None = Field(
        default=None, description="Azure Monitor value (null: no Azure data)."
    )
    askdiq: float | None = Field(
        default=None, description="AskDIQ-recorded value (null: not recorded)."
    )
    delta: float | None = Field(default=None, description="`azure - askdiq`.")
    drift_pct: float | None = Field(
        default=None,
        description="`|azure - askdiq| / azure * 100` (100 when Azure is 0 and "
        "AskDIQ is not; null without Azure data).",
    )
    drifted: bool = Field(
        default=False,
        description="drift_pct >= AZURE_RECONCILIATION_DRIFT_PCT on a deployment "
        "AskDIQ uses.",
    )


class ReconciliationRow(BaseModel):
    deployment: str = Field(description="Azure ModelDeploymentName.")
    role: DeploymentRole = Field(
        description="How AskDIQ uses it (Settings > Models); `other` = not used "
        "by AskDIQ (playground, other apps)."
    )
    day: str | None = Field(
        default=None, description="UTC day (YYYY-MM-DD); null for the 24 h row."
    )
    requests: ReconciliationCell
    tokens_in: ReconciliationCell
    tokens_out: ReconciliationCell
    throttled: ReconciliationCell = Field(description="HTTP 429 responses.")
    latency_avg_ms: ReconciliationCell = Field(
        description="Azure time to last byte (avg) vs AskDIQ LLM / embedding "
        "stage latency (avg). Informational: never flagged as drift."
    )
    latency_max_ms: ReconciliationCell
    ttft_avg_ms: float | None = Field(
        default=None, description="Azure normalised time to first byte (avg)."
    )
    availability_pct: float | None = Field(
        default=None, description="Azure availability rate (avg, %)."
    )
    estimated_cost_usd: float | None = Field(
        default=None, description="Preview FoundryModelEstimatedCost (estimate, USD)."
    )
    not_seen_tokens: float | None = Field(
        default=None,
        description="Azure tokens (in + out) not recorded by AskDIQ (>= 0): "
        "other callers, playground use, failed or retried calls.",
    )
    drifted: bool = False


class DeploymentTokens(BaseModel):
    """Usage & Cost "Azure-reported tokens": the summary window per deployment."""

    deployment: str
    role: DeploymentRole
    azure_tokens: float | None
    azure_tokens_in: float | None
    azure_tokens_out: float | None
    askdiq_tokens: float | None
    not_seen_tokens: float | None
    not_seen_pct: float | None


class PreviewCostState(BaseModel):
    enabled: bool = Field(
        description="FoundryModelEstimatedCost is polled (auto-disabled after 3 "
        "days without data; re-probed weekly)."
    )
    empty_since: str | None = None
    last_seen: str | None = None


class AzureMetricsSummaryRead(BaseModel):
    """Azure vs AskDIQ reconciliation (Governance > Monitoring, Usage & Cost)."""

    connected: bool = Field(description="AZURE_INSIGHTS_ENABLED with every id set.")
    state: SourceState = Field(description="State of the metrics source.")
    as_of: datetime | None = Field(
        default=None, description="Last successful metrics poll."
    )
    last_point_at: datetime | None = Field(
        default=None, description="Newest stored data point."
    )
    days: int
    drift_threshold_pct: float
    llm_deployment: str
    embedding_deployment: str
    last_24h: list[ReconciliationRow]
    daily: list[ReconciliationRow] = Field(
        description="Per deployment per UTC day, newest first."
    )
    tokens: list[DeploymentTokens]
    preview_cost: PreviewCostState
    generated_at: datetime


class SeriesPoint(BaseModel):
    t: datetime
    total: float | None = None
    average: float | None = None
    maximum: float | None = None


class MetricSeries(BaseModel):
    deployment: str
    status_code: str = Field(description="HTTP status (requests only), else ''.")
    points: list[SeriesPoint]


class AzureMetricSeriesRead(BaseModel):
    connected: bool
    as_of: datetime | None = None
    metric: MetricName
    deployment: str = Field(description="`*` = every deployment.")
    range: MetricsRange
    grain: MetricsGrain
    since: datetime
    until: datetime
    series: list[MetricSeries]
    generated_at: datetime
