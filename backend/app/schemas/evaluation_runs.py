"""Schemas for full evaluation runs (``/metrics/evaluation/runs``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_RUNS_PAGE = 100


class EvalRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = frozenset({EvalRunStatus.QUEUED, EvalRunStatus.RUNNING})


class EvalCaseType(StrEnum):
    """The benchmark's case types (``app.evaluation.dataset.CaseType``)."""

    ANSWERABLE = "answerable"
    COMPOUND = "compound"
    NEGATIVE = "negative"
    OFF_TOPIC = "off_topic"
    UNSAFE = "unsafe"
    SPAM = "spam"
    FOLLOWUP = "followup"
    CONVERSATIONAL = "conversational"
    ROBUSTNESS = "robustness"
    ABSTENTION = "abstention"
    SAFETY = "safety"


class EvalRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judge: bool = Field(
        default=False,
        description="Score every answer with the LLM judge (one extra LLM call per case).",
    )
    case_types: list[EvalCaseType] | None = Field(
        default=None,
        max_length=len(EvalCaseType),
        description="Run only these case types (all when omitted).",
    )


class EvalRunRead(BaseModel):
    """A run without its per-case results (history rows, progress)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: EvalRunStatus
    started_by: str
    options: dict[str, Any]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cases_total: int = 0
    cases_done: int = 0
    cases_passed: int = 0
    cancel_requested: bool = False
    pass_rate: float | None = None
    duration_seconds: float | None = None
    cost_usd: float | None = None
    error: str | None = None


class EvalRunDetail(EvalRunRead):
    summary: dict[str, Any] | None = None
    case_results: list[dict[str, Any]] | None = None


class EvalRunPage(BaseModel):
    items: list[EvalRunRead]
    total: int
    limit: int
    offset: int
    active: EvalRunRead | None = Field(
        default=None, description="The run queued or running now, if any."
    )


class EvalRunStarted(BaseModel):
    run_id: uuid.UUID
    status: Literal["queued"] = "queued"
    run: EvalRunRead


class EvalRunEstimate(BaseModel):
    case_count: int
    judge: bool
    avg_input_tokens_per_case: float
    avg_output_tokens_per_case: float
    avg_seconds_per_case: float
    price_prompt_per_million: float
    price_output_per_million: float
    estimated_cost_usd: float
    estimated_duration_seconds: float
    basis: Literal["history", "default"] = Field(
        description="history: averages of recent succeeded runs; default: built-in guesses."
    )
    sample_runs: int = Field(description="Succeeded runs the averages come from.")
