import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
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


class ChatSessionSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    is_expired: bool
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
        description="Optional user comment (max 1000 chars)",
    )
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
    created_at: datetime
    updated_at: datetime
