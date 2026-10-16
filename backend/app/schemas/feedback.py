"""Feedback / RLHF loop request and response models (newplan §5.4)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_CORRECTED_ANSWER_CHARS = 4000
MAX_REVIEW_NOTE_CHARS = 2000
MAX_TRIAGE_CORRECTED_ANSWER_CHARS = 4000
MAX_BULK_IDS = 200
MAX_PAGE_LIMIT = 200

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control_chars(value: str | None) -> str | None:
    """Drop ASCII control characters (keeps newlines and tabs); blank -> None."""
    if value is None:
        return None
    cleaned = _CONTROL_CHARS.sub("", value).strip()
    return cleaned or None


class TriageStatus(StrEnum):
    NEW = "new"
    REVIEWED = "reviewed"
    PROMOTED = "promoted"
    DISMISSED = "dismissed"


class ReasonTag(StrEnum):
    """Allow-listed reasons a user can give for a rating (chat-side chips)."""

    WRONG = "wrong"
    INCOMPLETE = "incomplete"
    OFF_TOPIC = "off_topic"
    UNSAFE = "unsafe"
    TOO_SLOW = "too_slow"
    UNCLEAR = "unclear"


class BulkAction(StrEnum):
    REVIEWED = "reviewed"
    DISMISSED = "dismissed"
    PROMOTED = "promoted"


class FeedbackSort(StrEnum):
    CREATED_AT_DESC = "created_at_desc"
    CREATED_AT_ASC = "created_at_asc"
    RATING_DESC = "rating_desc"
    RATING_ASC = "rating_asc"


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------


class FeedbackSource(BaseModel):
    source_name: str | None = None
    section_heading: str | None = None
    score: float | None = None
    chunk_id: str | None = None


class FeedbackItem(BaseModel):
    """One feedback row with the full exchange context (F1, F9)."""

    id: str
    rating: int
    comment: str
    ip_address: str
    conversation_id: str
    message_id: str
    created_at: str | None
    updated_at: str | None = None
    actor: str
    user_query: str | None = None
    answer: str | None = None
    outcome: str | None = None
    grounded: bool | None = None
    guardrail_flags: list[str] = Field(default_factory=list)
    sources: list[FeedbackSource] = Field(default_factory=list)
    confidence: float | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    reason_tags: list[str] = Field(default_factory=list)
    corrected_answer: str | None = None
    triage_status: TriageStatus
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    promoted_case_id: str | None = None
    version: int


class FeedbackPage(BaseModel):
    items: list[FeedbackItem]
    total: int = Field(description="Rows matching the current filters.")
    limit: int
    offset: int
    next_cursor: str | None = Field(
        default=None, description="Opaque keyset cursor for the next page."
    )


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------


class FeedbackTriageRequest(BaseModel):
    """PATCH body: a status transition, a reviewer note and/or a corrected answer.

    Sending ``review_note`` or ``corrected_answer`` as ``null`` clears it;
    leaving a field out keeps the stored value.
    """

    version: int = Field(..., ge=1, description="Row version you last read.")
    triage_status: TriageStatus | None = None
    review_note: str | None = Field(default=None, max_length=MAX_REVIEW_NOTE_CHARS)
    corrected_answer: str | None = Field(
        default=None,
        max_length=MAX_TRIAGE_CORRECTED_ANSWER_CHARS,
        description="Reviewer-written answer (the preference pair's chosen side); PII-masked.",
    )

    @field_validator("review_note", "corrected_answer", mode="after")
    @classmethod
    def _clean_text(cls, v: str | None) -> str | None:
        return strip_control_chars(v)


class FeedbackBulkRequest(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=MAX_BULK_IDS)
    action: BulkAction
    review_note: str | None = Field(default=None, max_length=MAX_REVIEW_NOTE_CHARS)

    @field_validator("ids", mode="after")
    @classmethod
    def _unique_ids(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(v))

    @field_validator("review_note", mode="after")
    @classmethod
    def _clean_note(cls, v: str | None) -> str | None:
        return strip_control_chars(v)


class FeedbackBulkResponse(BaseModel):
    action: BulkAction
    updated: int
    ids: list[str]
    promoted_case_ids: list[str] = Field(default_factory=list)


class PromoteRequest(BaseModel):
    """Optional body for promote-to-test (older clients send none)."""

    review_note: str | None = Field(default=None, max_length=MAX_REVIEW_NOTE_CHARS)

    @field_validator("review_note", mode="after")
    @classmethod
    def _clean_note(cls, v: str | None) -> str | None:
        return strip_control_chars(v)


class PromoteResponse(BaseModel):
    status: Literal["promoted"]
    case_id: str
    query: str
    created: bool = Field(description="False when the message was already promoted.")
    feedback_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Golden cases
# ---------------------------------------------------------------------------


class GoldenCase(BaseModel):
    id: str
    query: str
    case_type: str
    expect_grounded: bool
    expected_outcome: str | None = None
    source_message_id: str | None = None
    feedback_id: str | None = None
    reference_answer: str | None = None
    reason_tags: list[str] = Field(default_factory=list)
    promoted_by: str
    promoted_at: datetime
    last_run_at: datetime | None = None
    last_run_passed: bool | None = None
    last_run_detail: dict[str, Any] | None = None


class GoldenCasePage(BaseModel):
    items: list[GoldenCase]
    total: int
    limit: int
    offset: int


class GoldenRunResponse(BaseModel):
    case_id: str
    status: Literal["queued", "already_queued"]
    job_id: str | None = None


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


class OutcomeBucket(BaseModel):
    positive: int
    negative: int
    total: int


class TrendPoint(BaseModel):
    day: str
    total: int
    positive: int
    negative: int
    avg_rating: float | None
    answers: int = Field(
        default=0, description="Assistant answers that UTC day (audience-scoped)."
    )
    coverage: float | None = Field(
        default=None,
        description="Ratings / answers that day (0-1); null with no answers.",
    )


class CountItem(BaseModel):
    key: str
    count: int


class DisagreementQuadrant(BaseModel):
    """Human rating vs the system's own groundedness verdict."""

    positive_grounded: int
    positive_ungrounded: int
    negative_grounded: int
    negative_ungrounded: int


class KnowledgeGap(BaseModel):
    query: str
    count: int
    avg_rating: float
    avg_confidence: float | None
    last_seen: str | None
    message_id: str


class FeedbackAnalytics(BaseModel):
    total: int = Field(description="Unfiltered total for the audience (F3).")
    total_feedback: int
    positive_count: int
    negative_count: int
    neutral_count: int
    positive_ratio: float
    negative_ratio: float
    feedback_rate: float
    feedback_by_outcome: dict[str, OutcomeBucket]
    daily_trend: list[TrendPoint]
    rating_distribution: dict[str, int]
    by_intent: list[CountItem] | None = Field(
        default=None, description="Present only once turn insights exist (B6)."
    )
    top_reasons: list[CountItem]
    triage_funnel: dict[str, int]
    disagreement: DisagreementQuadrant
    knowledge_gaps: list[KnowledgeGap]
