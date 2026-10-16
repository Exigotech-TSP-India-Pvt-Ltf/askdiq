"""Response models for the dashboard, usage and evaluation chart additions (B7.1, B7.4).

Mirrors ``frontend/lib/dashboard-api.ts`` and ``frontend/lib/usage-insights-api.ts``
(contract: ``frontend/docs/contracts/dashboard-aggregations.md``). Units:
``*_ms`` milliseconds, ``*_usd`` US dollars, ratios 0..1; the legacy
``error_rate`` is a percentage 0..100.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SeriesBucket = Literal["hour", "day"]


class ActivityType(StrEnum):
    QUERY = "query"
    FEEDBACK = "feedback"
    ERROR = "error"
    GUARDRAIL = "guardrail"
    DOCUMENT = "document"
    ADMIN = "admin"


# ---------------------------------------------------------------------------
# GET /admin/dashboard
# ---------------------------------------------------------------------------


class DashboardWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(
        alias="from", description="Window start, ISO 8601 UTC (inclusive)."
    )
    to: str = Field(description="Window end, ISO 8601 UTC (exclusive).")
    bucket: SeriesBucket
    previous_from: str
    previous_to: str


class SeriesPoint(BaseModel):
    t: str = Field(description="Bucket start: YYYY-MM-DD (day) or ISO UTC (hour).")
    value: float | None = Field(description="null = no data in the bucket.")


class OutcomePoint(BaseModel):
    t: str
    answered: int
    ungrounded: int
    blocked: int
    conversational: int
    llm_failed: int


class LatencyPoint(BaseModel):
    t: str
    p50_ms: float | None
    p95_ms: float | None
    count: int


class DashboardCost(BaseModel):
    currency: Literal["USD"] = "USD"
    month: str = Field(description="YYYY-MM (UTC), always the current month.")
    mtd_usd: float
    forecast_month_end_usd: float | None
    budget_usd: float | None
    days_elapsed: int
    days_in_month: int


class DashboardSatisfaction(BaseModel):
    thumbs_up: int
    thumbs_down: int
    ratio: float | None


class TopIntent(BaseModel):
    intent: str
    label: str
    count: int
    share: float


class ActiveVisitorsPeriod(BaseModel):
    value: int = Field(ge=0, description="Active visitors in the last `days` days.")
    previous: int = Field(
        ge=0, description="Active visitors in the equal window before."
    )
    delta: float | None = Field(
        description="(value - previous) / previous; null when previous is 0."
    )
    days: int = Field(ge=1)


class ActiveVisitors(BaseModel):
    """Active visitors (``app.dashboard.active_visitors``): counts only, no IPs."""

    definition: str
    active_now: int = Field(
        ge=0, description="Active visitors in the last `active_now_minutes`."
    )
    active_now_minutes: int = Field(ge=1)
    dau: ActiveVisitorsPeriod
    wau: ActiveVisitorsPeriod
    mau: ActiveVisitorsPeriod


class DashboardData(BaseModel):
    """``GET /admin/dashboard``. With ``window`` the KPI totals are for that window."""

    users: int
    active_users: int
    sessions: int
    conversations: int
    queries: int
    feedback: int
    avg_latency_ms: float
    error_rate: float = Field(description="Percent 0..100.")
    pipeline: dict[str, float | bool | None] | None = None
    governance: dict[str, str] | None = None
    query_trend: list[dict[str, str | int]] | None = None
    latency_breakdown: list[dict[str, str | float]] | None = None
    window: DashboardWindow | None = None
    cost_usd: float | None = None
    sparklines: dict[str, list[SeriesPoint]] | None = None
    deltas: dict[str, float | None] | None = None
    queries_by_outcome: list[OutcomePoint] | None = None
    latency_series: list[LatencyPoint] | None = None
    cost: DashboardCost | None = None
    satisfaction: DashboardSatisfaction | None = None
    top_intents: list[TopIntent] | None = None
    active_visitors: ActiveVisitors | None = Field(
        default=None, description="DAU / WAU / MAU and active-now, as of the request."
    )


# ---------------------------------------------------------------------------
# GET /admin/dashboard/activity
# ---------------------------------------------------------------------------


class ActivityEvent(BaseModel):
    id: str
    type: ActivityType
    at: str = Field(description="ISO 8601 UTC; newest first.")
    summary: str = Field(description="One line, PII-free.")
    request_id: str | None
    conversation_id: str | None
    message_id: str | None
    document_id: str | None


class ActivityFeed(BaseModel):
    items: list[ActivityEvent]
    generated_at: str


# ---------------------------------------------------------------------------
# GET /admin/usage/summary additions
# ---------------------------------------------------------------------------


class CostStagePoint(BaseModel):
    t: str
    query_embed: float
    guardrail_embed: float
    prompt: float
    cached_prompt: float
    reasoning: float
    visible: float
    ingestion: float


class CostByStageSeries(BaseModel):
    bucket: SeriesBucket
    points: list[CostStagePoint]


class CostForecastPoint(BaseModel):
    t: str
    actual_usd: float | None
    forecast_usd: float | None


class CostForecast(BaseModel):
    currency: Literal["USD"] = "USD"
    month: str
    method: Literal["linear"] = "linear"
    mtd_usd: float
    forecast_month_end_usd: float | None
    budget_usd: float | None
    daily: list[CostForecastPoint]


class CacheStats(BaseModel):
    cached_prompt_tokens: int
    prompt_tokens: int
    hit_ratio: float | None


# ---------------------------------------------------------------------------
# GET /metrics/evaluation/live additions
# ---------------------------------------------------------------------------


class EvalRunRef(BaseModel):
    run_id: str
    started_at: str
    n_cases: int


class QualityDimension(BaseModel):
    key: str
    label: str
    current: float | None
    previous: float | None


class QualityDimensions(BaseModel):
    current_run: EvalRunRef | None
    previous_run: EvalRunRef | None
    dimensions: list[QualityDimension]


class PassRatePoint(BaseModel):
    run_id: str
    started_at: str
    pass_rate: float
    n_cases: int
