"""Response schemas for the governance/audit-log endpoint."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AuditLogEntry(BaseModel):
    id: str
    request_id: str
    actor: str
    action: str
    detail: dict
    created_at: datetime
    ip_address: str | None = None


# ---------------------------------------------------------------------------
# /admin/governance/* (PR-10)
# ---------------------------------------------------------------------------


class LogLevel(StrEnum):
    CRITICAL = "CRITICAL"
    ALERT = "ALERT"
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class LogService(StrEnum):
    AUTH = "auth"
    QUERY = "query"
    INGEST = "ingest"
    FEEDBACK = "feedback"
    ADMIN = "admin"
    SYSTEM = "system"


class TraceStatusFilter(StrEnum):
    OK = "ok"
    UNGROUNDED = "ungrounded"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNKNOWN = "unknown"


class TraceOutcomeFilter(StrEnum):
    ANSWERED = "answered"
    UNGROUNDED = "ungrounded"
    BLOCKED = "blocked"
    CONVERSATIONAL = "conversational"
    LLM_FAILED = "llm_failed"
    CLARIFY = "clarify"


class LatencyBucketFilter(StrEnum):
    FAST = "fast"
    MODERATE = "moderate"
    SLOW = "slow"
    VERY_SLOW = "very_slow"


class GovernanceLogItem(BaseModel):
    id: str
    created_at: str = Field(description="ISO-8601 UTC timestamp.")
    level: LogLevel
    service: LogService
    action: str
    summary: str = Field(description="One-line, human-readable message.")
    detail: dict[str, Any]
    message: str = Field(
        "",
        description="**Deprecated**: legacy JSON string of `detail` (300 chars).",
        deprecated=True,
    )
    user_id: str | None = None
    ip_address: str | None = None
    request_id: str | None = None


class GovernanceLogPage(BaseModel):
    items: list[GovernanceLogItem]
    total: int = Field(
        description="Rows matching the filters; a planner estimate when "
        "`total_is_estimate` is true."
    )
    total_estimate: int | None = Field(
        None, description="Set (equal to `total`) only when the total is estimated."
    )
    total_is_estimate: bool = False
    level_counts: dict[str, int] = Field(
        description="Rows per level for the current filters, ignoring `level`."
    )
    level_counts_capped: bool = Field(
        False, description="True when level counts cover only the newest rows."
    )
    limit: int
    next_cursor: str | None = Field(
        None, description="Pass as `cursor` for the next page; null on the last page."
    )
    has_more: bool


class StageTiming(BaseModel):
    stage: str
    ms: float
    unit: str = "ms"


class TraceTokens(BaseModel):
    prompt: int | None = None
    completion: int | None = None
    reasoning: int | None = None
    embedding: int | None = None
    total: int | None = None


class GovernanceTraceItem(BaseModel):
    request_id: str
    user_id: str | None = None
    conversation_id: str | None = None
    ip_address: str | None = None
    timestamp: str
    status: TraceStatusFilter
    outcome: str | None = None
    guardrail_flags: list[str] = Field(default_factory=list)
    total_ms: float
    latency_unit: str = "ms"
    latency_bucket: LatencyBucketFilter | None = None
    slowest_stage: StageTiming | None = None
    query: str | None = Field(None, description="Redacted query, truncated.")
    tokens: TraceTokens
    cost_usd: float | None = None


class GovernanceTracePage(BaseModel):
    items: list[GovernanceTraceItem]
    total: int
    total_estimate: int | None = None
    total_is_estimate: bool = False
    limit: int
    next_cursor: str | None = None
    has_more: bool


class WaterfallStage(BaseModel):
    stage: str
    start_ms: float | None = None
    duration_ms: float | None = None


class TraceWaterfall(BaseModel):
    unit: str = "ms"
    total_ms: float
    stages: list[WaterfallStage]
    other_ms: float = Field(description="Time not attributed to a stage.")


class TraceSource(BaseModel):
    chunk_id: str
    document_id: str | None = None
    source_name: str | None = None
    score: float | None = Field(
        None, description="Retrieval score (null for old rows)."
    )


class GovernanceTraceDetail(GovernanceTraceItem):
    grounded: bool | None = None
    stages: TraceWaterfall
    sources: list[TraceSource]
    flags: list[str]


class HealthSignal(BaseModel):
    value: float | None = None
    unit: str
    status: str
    degraded_at: float
    incident_at: float


class HealthStatus(BaseModel):
    status: str = Field(description="Healthy, Degraded or Incident.")
    window_minutes: int
    signals: dict[str, HealthSignal]


class Kpi(BaseModel):
    value: float | int | None = None
    unit: str | None = None
    sparkline: list[float | int | None] = Field(
        description="24 hourly points, oldest first (aligned with `hours`)."
    )
    window: str
    source: str


class GovernanceOverview(BaseModel):
    audience: str
    window_hours: int
    hours: list[str]
    health: HealthStatus
    kpis: dict[str, Kpi]
    incidents: list[GovernanceLogItem]
    online_quality_kpis: dict[str, Any]
