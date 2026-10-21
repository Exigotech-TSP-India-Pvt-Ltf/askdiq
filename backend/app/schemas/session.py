import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.feedback import (
    MAX_CORRECTED_ANSWER_CHARS,
    ReasonTag,
    strip_control_chars,
)
from app.schemas.query import Clarification


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    # Assistant turns: links the answer to its governance trace after reload.
    request_id: str | None = None
    grounded: bool | None = None
    guardrail_flags: list[str] | None = None
    feedback_rating: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    embedding_tokens: int | None = None
    guardrail_tokens: int | None = None
    cost_usd: float | None = None
    token_details: dict | None = None
    # Assistant turns: canonical outcome, and the options of a clarifying
    # question (outcome "clarify") so the client can re-render them on reload.
    outcome: str | None = None
    clarification: Clarification | None = None


class ChatSessionSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    is_expired: bool = Field(
        description=(
            "Idle past the implicit-resume window; informational, does not "
            "block resuming (sending with this session's id continues it)."
        )
    )
    message_count: int


class ChatSessionDetail(ChatSessionSummary):
    messages: list[ChatMessageOut]


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    ip_address: str | None = Field(
        default=None, description="End user's IP address (forwarded by the frontend)."
    )


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)


class UserFeedbackRequest(BaseModel):
    """Subtask 1.3.1: User feedback payload."""

    rating: int = Field(..., ge=1, le=5, description="Satisfaction rating from 1 to 5")
    comment: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional user comment (max 1000 chars, PII-masked before storage)",
    )
    reason_tags: list[ReasonTag] = Field(
        default_factory=list,
        max_length=len(ReasonTag),
        description=(
            "Allow-listed reasons (thumbs-down chips). Omit both this and "
            "corrected_answer to keep the stored details on a negative rating."
        ),
    )
    corrected_answer: str | None = Field(
        default=None,
        max_length=MAX_CORRECTED_ANSWER_CHARS,
        description="What the answer should have said (PII-masked before storage).",
    )

    @field_validator("reason_tags", mode="after")
    @classmethod
    def dedupe_tags(cls, v: list[ReasonTag]) -> list[ReasonTag]:
        return list(dict.fromkeys(v))

    @field_validator("corrected_answer", mode="after")
    @classmethod
    def sanitize_corrected_answer(cls, v: str | None) -> str | None:
        return strip_control_chars(v)

    ip_address: str | None = Field(
        default=None, description="End user's IP address (forwarded by the frontend)."
    )

    @field_validator("comment", mode="after")
    @classmethod
    def sanitize_comment(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # Strip ASCII control characters (keep newlines and tabs)
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v).strip()
        return cleaned or None


class UserFeedbackResponse(BaseModel):
    """Subtask 1.3.3: User feedback result response."""

    message_id: str
    session_id: str
    rating: int
    comment: str | None = None
    reason_tags: list[str] = Field(default_factory=list)
    corrected_answer: str | None = None
    triage_status: str = "new"
    created_at: datetime
    updated_at: datetime
