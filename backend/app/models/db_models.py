"""SQLAlchemy ORM models backing users, documents, chunks, latency, and audit data."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.core.database import Base

settings = get_settings()


class User(Base):
    """Account used to sign in to the Deploy IQ frontend and call the API.

    `email` is the JWT `sub` claim issued by /auth/login, so it doubles as
    the `actor` identity already threaded through audit logging.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String(256))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_name: Mapped[str] = mapped_column(String(512))
    source_type: Mapped[str] = mapped_column(String(64))  # pdf, markdown, html, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    doc_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (  # <-- NEW
        Index("ix_chunks_document_hash", "document_id", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim))

    # Which chunking strategy produced this chunk — lets us A/B strategies later.
    strategy: Mapped[str] = mapped_column(String(64))
    chunk_index: Mapped[int] = mapped_column(Integer)
    chunk_metadata: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # section, page, parent_id, etc.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document: Mapped["Document"] = relationship(back_populates="chunks")


class LatencyRecord(Base):
    __tablename__ = "latency_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    total_ms: Mapped[float] = mapped_column(Float)
    embedding_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    retrieval_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    guardrail_pre_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    guardrail_post_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    reranker_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    request_id: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(256))  # user/API key identity
    action: Mapped[str] = mapped_column(
        String(64)
    )  # query, ingest, guardrail_block, etc.
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_audit_log_ip_created", "ip_address", "created_at"),
        Index("ix_audit_log_created_action", "created_at", "action"),
    )


class ChatSession(Base):
    """A user's chat conversation thread.

    History is scoped per session (via ChatMessageRecord.session_id) so
    switching to a different session never leaks context from another one.
    `expires_at` is refreshed on every message (see app.core.sessions) —
    once it's in the past, the session is treated as expired and a new one
    is started transparently on the next query.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_email: Mapped[str] = mapped_column(String(320), index=True)
    title: Mapped[str] = mapped_column(String(256), default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    # IP address that created the session (governance/audit trail).
    created_ip: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    messages: Mapped[list["ChatMessageRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessageRecord.created_at",
    )


class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    # Shared by one user+assistant pair so a whole exchange can be deleted atomically.
    turn_index: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grounded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    guardrail_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # IP address that sent this turn (governance/audit trail).
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    # Actual token usage from the Azure OpenAI response for this exchange's
    # LLM call (see app.harness.prompts.call_llm) — populated on the
    # assistant row only, straight from the API's own `usage` field, never
    # estimated. NULL for user rows, for exchanges with no real LLM call
    # (guardrail-blocked/fallback/conversational replies), and for rows
    # recorded before this column existed.
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Stage-by-stage token and cost accounting (Task 2.4.1)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    guardrail_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    session: Mapped["ChatSession"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_chat_messages_session_turn", "session_id", "turn_index"),
        Index("ix_chat_messages_created_at", "created_at"),
        Index("ix_chat_messages_ip_created", "ip_address", "created_at"),
    )


class MessageFeedback(Base):
    """User feedback ratings and comments on assistant responses (Subtask 1.3.3)."""

    __tablename__ = "message_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    actor: Mapped[str] = mapped_column(String(256), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "rating >= 1 AND rating <= 5", name="ck_message_feedback_rating_range"
        ),
        UniqueConstraint(
            "message_id", "actor", name="uq_message_feedback_message_actor"
        ),
    )


class TestIpConfig(Base):
    """Test and development IP addresses excluded from user metrics and manageable via dashboard."""

    __tablename__ = "test_ip_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ip_address: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EvaluationRecord(Base):
    """Continuous production evaluation record per query turn."""

    __tablename__ = "evaluation_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    audience: Mapped[str] = mapped_column(String(32), default="users", index=True)
    query: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    grounded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    faithfulness: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    completeness: Mapped[float | None] = mapped_column(Float, nullable=True)
    citation_precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    citation_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    retrieval_recall: Mapped[float | None] = mapped_column(Float, nullable=True)
    safety_passed: Mapped[bool] = mapped_column(Boolean, default=True)
    guardrail_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    retrieved_chunks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    judge_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    unsupported_claims: Mapped[list | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )

    __table_args__ = (
        Index("ix_eval_records_audience_created", "audience", "created_at"),
    )
