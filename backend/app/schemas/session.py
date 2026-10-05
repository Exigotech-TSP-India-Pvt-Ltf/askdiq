"""Request/response schemas for chat session management endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    grounded: bool | None = None
    guardrail_flags: list[str] | None = None


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


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
