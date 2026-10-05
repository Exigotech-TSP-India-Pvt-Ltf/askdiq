"""Schemas for the /metrics/latency endpoint.

Settings.latency_budget_ms is measured against the retrieval + guardrail
pipeline only (`pipeline_ex_llm`) — a real LLM call regularly takes
single-digit seconds, so folding it into a 200ms budget would make the
number meaningless. `end_to_end` and `llm` are reported for visibility
but carry no budget verdict.
"""

from pydantic import BaseModel


class LatencyPercentiles(BaseModel):
    sample_size: int
    p50_ms: float
    p70_ms: float
    p100_ms: float


class BudgetedLatencyPercentiles(LatencyPercentiles):
    within_budget_pct: float  # % of requests under Settings.latency_budget_ms


class LatencyStageBreakdown(BaseModel):
    stage: str
    p50_ms: float
    p70_ms: float
    p100_ms: float


class LatencyReport(BaseModel):
    pipeline_ex_llm: (
        BudgetedLatencyPercentiles  # guardrail_pre + retrieval + guardrail_post
    )
    end_to_end: LatencyPercentiles  # full /query request, informational only
    llm: LatencyPercentiles  # generation stage alone, informational only
    by_stage: list[LatencyStageBreakdown]
