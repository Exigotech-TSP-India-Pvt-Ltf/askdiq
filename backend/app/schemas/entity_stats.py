"""Response models for the Visitors and Conversations entity pages (B7.2).

Mirrors ``frontend/lib/entity-stats-api.ts`` (contract:
``frontend/docs/contracts/entity-stats.md``). Timestamps are ISO 8601 UTC with
``Z``; days are ``YYYY-MM-DD`` (UTC); money is USD; durations milliseconds.
``null`` means "not measured"; lists are never null.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class OutcomeName(StrEnum):
    ANSWERED = "answered"
    UNGROUNDED = "ungrounded"
    BLOCKED = "blocked"
    CONVERSATIONAL = "conversational"
    LLM_FAILED = "llm_failed"


# ---------------------------------------------------------------------------
# Visitors
# ---------------------------------------------------------------------------


class VisitorStatsKpis(BaseModel):
    visitors: int
    new_visitors: int
    returning_visitors: int
    active_now: int = Field(description="Visitors active in the last 30 minutes.")
    sessions: int
    queries: int
    avg_sessions_per_visitor: float | None


class NewVsReturningDay(BaseModel):
    day: str
    new: int
    returning: int


class VisitorHeatCell(BaseModel):
    weekday: int = Field(ge=0, le=6, description="0 = Monday ... 6 = Sunday.")
    hour: int = Field(ge=0, le=23)
    sessions: int


class VisitorStats(BaseModel):
    from_: str = Field(alias="from")
    to: str
    tz: str = "UTC"
    kpis: VisitorStatsKpis
    new_vs_returning: list[NewVsReturningDay]
    heatmap: list[VisitorHeatCell]


class TimelineSession(BaseModel):
    session_id: str
    title: str | None
    started_at: str
    ended_at: str
    turns: int
    cost_usd: float


class TimelineQuery(BaseModel):
    request_id: str | None
    session_id: str
    turn_index: int
    query: str
    outcome: str | None
    grounded: bool | None
    created_at: str
    latency_ms: float | None
    cost_usd: float | None


class CostDay(BaseModel):
    day: str
    cost_usd: float
    queries: int


class TimelineFeedback(BaseModel):
    id: str
    message_id: str
    session_id: str
    turn_index: int | None
    request_id: str | None
    rating: int
    comment: str | None
    reason_tags: list[str]
    created_at: str


class TimelineTruncated(BaseModel):
    sessions: bool
    queries: bool
    feedback: bool


class VisitorTimeline(BaseModel):
    visitor_id: str
    ip_address: str | None
    sessions: list[TimelineSession]
    queries: list[TimelineQuery]
    cost_series: list[CostDay]
    feedback: list[TimelineFeedback]
    truncated: TimelineTruncated


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class ConversationTotals(BaseModel):
    conversations: int
    turns: int
    avg_turns: float | None


class TurnsBin(BaseModel):
    min_turns: int
    max_turns: int | None = Field(description="null = open-ended last bin.")
    conversations: int


class OutcomeDay(BaseModel):
    day: str
    counts: dict[str, int]


class ConversationStats(BaseModel):
    from_: str = Field(alias="from")
    to: str
    totals: ConversationTotals
    turns_histogram: list[TurnsBin]
    outcome_by_day: list[OutcomeDay]


class TurnTokens(BaseModel):
    prompt: int = Field(description="Includes cached_prompt.")
    cached_prompt: int
    completion: int = Field(description="Includes reasoning.")
    reasoning: int
    embedding: int
    guardrail: int
    total: int


class TurnSource(BaseModel):
    document_id: str | None
    chunk_id: str | None
    chunk_index: int | None
    source_name: str | None
    section_heading: str | None
    score: float | None


class TurnFeedback(BaseModel):
    id: str
    rating: int
    comment: str | None
    reason_tags: list[str]
    triage_status: str | None
    created_at: str


class TurnLabels(BaseModel):
    intent: str | None
    quality: str | None
    issue: str | None


class TurnDetail(BaseModel):
    turn_index: int
    request_id: str | None
    user_message_id: str | None
    assistant_message_id: str | None
    created_at: str | None
    outcome: str | None
    grounded: bool | None
    stages_ms: dict[str, float | None]
    total_ms: float | None
    tokens: TurnTokens | None
    cost_usd: float | None
    sources: list[TurnSource]
    feedback: list[TurnFeedback]
    labels: TurnLabels | None
    promoted_case_id: str | None
