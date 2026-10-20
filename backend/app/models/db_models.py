"""SQLAlchemy ORM models backing users, documents, chunks, latency, and audit data."""

import uuid
from datetime import UTC, date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.core.database import Base

settings = get_settings()


class User(Base):
    """A person who signs in through Entra ID SSO (newplan §7.5).

    Rows are keyed on the Entra object id (`entra_oid`); `email` is the
    `actor` identity threaded through audit logging and chat ownership.
    `password_hash` is legacy (local accounts are not used for sign-in).
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String(256))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # --- SSO / RBAC (PR-06) ---
    entra_oid: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Bumped on every role/status change (optimistic concurrency, PR-07).
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "role IS NULL OR role IN ('superadmin', 'admin', 'operator')",
            name="ck_users_role",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'rejected', 'suspended')",
            name="ck_users_status",
        ),
    )


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
    # The standardized Markdown every upload (.md or .docx) is normalized to
    # before chunking. Deferred so list queries never load the full text;
    # NULL for documents ingested before migration 254857d10a20.
    canonical_markdown: Mapped[str | None] = mapped_column(
        Text, nullable=True, deferred=True
    )

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

    __table_args__ = (
        # Governance traces keyset pagination (migration 72125f44984d).
        Index("ix_latency_metrics_created_id", "created_at", "id"),
    )


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
        # Governance keyset pagination and joins (migration 72125f44984d).
        Index("ix_audit_log_created_id", "created_at", "id"),
        Index("ix_audit_log_action_created_id", "action", "created_at", "id"),
        Index("ix_audit_log_request_action", "request_id", "action"),
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

    __table_args__ = (
        # Newest session per (actor, IP) on every /query; newest-first list.
        Index(
            "ix_chat_sessions_user_ip_activity",
            "user_email",
            "created_ip",
            "last_activity_at",
        ),
        Index("ix_chat_sessions_user_activity", "user_email", "last_activity_at"),
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
        Index("ix_chat_messages_request_role", "request_id", "role"),
        # Newest user turns first (usage queries page; b2d6e08d82b3).
        Index(
            "ix_chat_messages_user_created_desc",
            text("created_at DESC NULLS LAST"),
            postgresql_where=text("role = 'user'"),
        ),
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
    # --- RLHF triage loop (PR-11, newplan §5.4) ---
    reason_tags: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    corrected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    triage_status: Mapped[str] = mapped_column(
        String(16), default="new", server_default="new"
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    promoted_case_id: Mapped[str | None] = mapped_column(
        ForeignKey("eval_promoted_cases.id", ondelete="SET NULL"), nullable=True
    )
    # Optimistic concurrency for triage (409 on a stale version).
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "rating >= 1 AND rating <= 5", name="ck_message_feedback_rating_range"
        ),
        CheckConstraint(
            "triage_status IN ('new', 'reviewed', 'promoted', 'dismissed')",
            name="ck_message_feedback_triage_status",
        ),
        UniqueConstraint(
            "message_id", "actor", name="uq_message_feedback_message_actor"
        ),
        Index("ix_message_feedback_triage_created", "triage_status", "created_at"),
        Index("ix_message_feedback_created_id", "created_at", "id"),
    )


class EvalPromotedCase(Base):
    """A golden regression case promoted from human feedback (durable, F4/F5)."""

    __tablename__ = "eval_promoted_cases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query: Mapped[str] = mapped_column(Text)
    case_type: Mapped[str] = mapped_column(
        String(32), default="robustness", server_default="robustness"
    )
    expect_grounded: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    expected_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # No FK: imported legacy cases may point at messages that no longer exist.
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    feedback_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reference_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_tags: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    promoted_by: Mapped[str] = mapped_column(String(256))
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now()
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_run_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "source_message_id", name="uq_eval_promoted_cases_source_message_id"
        ),
        Index("ix_eval_promoted_cases_promoted_at", "promoted_at"),
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


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AccessRequest(Base):
    """A signed-in user's request for dashboard access (one pending per user)."""

    __tablename__ = "access_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    requested_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_access_requests_status",
        ),
        CheckConstraint(
            "requested_role IS NULL OR requested_role IN "
            "('superadmin', 'admin', 'operator')",
            name="ck_access_requests_requested_role",
        ),
        Index(
            "uq_access_requests_one_pending",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )


class AuthSession(Base):
    """Server-side sign-in session; only an HMAC-SHA256 of the id is stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (Index("ix_auth_sessions_user_revoked", "user_id", "revoked_at"),)


# --- Insights (PR-12, planchanges4 B6.1) ---


class InsightTaxonomy(Base):
    """Editable label lists (intent, bot_quality, issue) with stable codes."""

    __tablename__ = "insight_taxonomy"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Intent group used to derive a conversation's user_type (newplan 6.4).
    group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    substantive: Mapped[bool] = mapped_column(Boolean, server_default=true())
    handled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("kind", "code", name="uq_insight_taxonomy_kind_code"),
    )


class TurnInsight(Base):
    """Classifier labels for one exchange, keyed by its assistant message."""

    __tablename__ = "turn_insights"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), unique=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    intent: Mapped[str] = mapped_column(String(32), index=True)
    bot_quality: Mapped[str] = mapped_column(String(32), index=True)
    issue: Mapped[str | None] = mapped_column(String(32), nullable=True)
    substantive: Mapped[bool] = mapped_column(Boolean)
    handled: Mapped[bool] = mapped_column(Boolean)
    friction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    classified_by: Mapped[str] = mapped_column(String(8))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class InsightDailyRollup(Base):
    """Per-day turn counts by audience, intent, quality and turn outcome."""

    __tablename__ = "insight_daily_rollup"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    audience: Mapped[str] = mapped_column(String(16), primary_key=True)
    intent: Mapped[str] = mapped_column(String(32), primary_key=True)
    quality: Mapped[str] = mapped_column(String(32), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(24), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    handled: Mapped[int] = mapped_column(Integer)
    substantive: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class InsightLlmUsage(Base):
    """One row per classifier LLM call: tokens and cost (daily budget source)."""

    __tablename__ = "insight_llm_usage"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    model: Mapped[str] = mapped_column(String(64))
    turns: Mapped[int] = mapped_column(Integer)
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    token_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class BrandAsset(Base):
    """Content-addressed brand image (PNG, WebP or sanitized SVG)."""

    __tablename__ = "brand_assets"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    mime: Mapped[str] = mapped_column(String(32))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "mime IN ('image/png', 'image/webp', 'image/svg+xml')",
            name="ck_brand_assets_mime",
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_brand_assets_sha256"),
    )


def _asset_fk() -> Mapped[str | None]:
    return mapped_column(
        String(64),
        ForeignKey("brand_assets.sha256", ondelete="SET NULL"),
        nullable=True,
    )


class BrandSettings(Base):
    """The single white-label row (``id = 1``); newplan section 14."""

    __tablename__ = "brand_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    product_name: Mapped[str] = mapped_column(String(80))
    assistant_name: Mapped[str] = mapped_column(String(80))
    company_name: Mapped[str] = mapped_column(String(120))
    support_email: Mapped[str] = mapped_column(String(320))
    access_contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    primary_hex: Mapped[str] = mapped_column(String(7))
    accent_hex: Mapped[str | None] = mapped_column(String(7), nullable=True)
    tint_hex: Mapped[str | None] = mapped_column(String(7), nullable=True)
    logo_light_asset_id: Mapped[str | None] = _asset_fk()
    logo_dark_asset_id: Mapped[str | None] = _asset_fk()
    mark_light_asset_id: Mapped[str | None] = _asset_fk()
    mark_dark_asset_id: Mapped[str | None] = _asset_fk()
    favicon_asset_id: Mapped[str | None] = _asset_fk()
    login_eyebrow: Mapped[str | None] = mapped_column(String(60), nullable=True)
    login_headline: Mapped[str | None] = mapped_column(String(160), nullable=True)
    login_subtext: Mapped[str | None] = mapped_column(String(400), nullable=True)
    login_description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    privacy_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    terms_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    help_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    chat_sample_questions: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    updated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_brand_settings_singleton"),
        CheckConstraint(
            "primary_hex ~ '^#[0-9a-f]{6}$' "
            "AND (accent_hex IS NULL OR accent_hex ~ '^#[0-9a-f]{6}$') "
            "AND (tint_hex IS NULL OR tint_hex ~ '^#[0-9a-f]{6}$')",
            name="ck_brand_settings_hex",
        ),
    )


class ModelSettings(Base):
    """Runtime model and pricing overrides (Settings > Models); singleton ``id = 1``.

    A null column means "use the .env value". Prices are USD per 1M tokens and
    only affect exchanges recorded after the change (each exchange snapshots
    the rates it was billed at).
    """

    __tablename__ = "model_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    llm_deployment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price_prompt_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_cached_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_output_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_embed_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_model_settings_singleton"),
        CheckConstraint(
            "coalesce(price_prompt_per_million, 0) >= 0 "
            "AND coalesce(price_cached_per_million, 0) >= 0 "
            "AND coalesce(price_output_per_million, 0) >= 0 "
            "AND coalesce(price_embed_per_million, 0) >= 0",
            name="ck_model_settings_prices",
        ),
    )


# --- Alerts (app/alerts) ---

ALERT_ACTIVE_STATUSES = ("open", "acknowledged")


class Alert(Base):
    """One alert raised by a rule in ``app.alerts.catalogue``.

    At most one *active* (open / acknowledged) row exists per ``dedupe_key``
    (partial unique index), so every worker and replica can raise the same
    condition safely; repeats bump ``occurrences`` / ``last_seen_at``.
    ``notify_*`` tracks email delivery (``disabled`` when SMTP is not set).
    """

    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default="open", server_default="open"
    )
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    component: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    occurrences: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    notify_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    notify_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    notify_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_alerts_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')",
            name="ck_alerts_status",
        ),
        CheckConstraint(
            "notify_status IN ('pending', 'queued', 'sent', 'retrying', 'failed', "
            "'disabled', 'skipped', 'suppressed')",
            name="ck_alerts_notify_status",
        ),
        Index(
            "uq_alerts_active_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('open', 'acknowledged')"),
        ),
        Index("ix_alerts_status_last_seen", "status", "last_seen_at"),
        Index("ix_alerts_dedupe_key_notified", "dedupe_key", "notified_at"),
        Index("ix_alerts_created_id", "created_at", "id"),
    )


class DashboardSnapshot(Base):
    """Precomputed dashboard aggregate: one API payload per kind + params.

    Written by the snapshot refresher (app.dashboard.snapshots) under an
    advisory lock and read by the dashboard routes, so a page visit reads one
    row instead of aggregating the history. ``requested_at`` marks the
    combinations people actually open; only those are kept fresh.
    """

    __tablename__ = "dashboard_snapshots"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSONB)
    payload: Mapped[dict | list] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    compute_ms: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_dashboard_snapshots_requested_at", "requested_at"),)


class CitationSource(Base):
    """One cited source of a query-audit row or an evaluation record, expanded
    from its JSON once (``app.documents.citations``), so citation stats for any
    date range are an indexed aggregation instead of a JSON scan.

    Insert-only, kept in step by the jobs worker; rows of a deleted parent go
    with it (FK cascade). Reads combine these rows with a live expansion of the
    rows newer than the sync point, so results equal the direct computation.
    """

    __tablename__ = "citation_sources"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_log.id", ondelete="CASCADE")
    )
    eval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluation_records.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(64))
    cited_at: Mapped[datetime] = mapped_column(DateTime)
    chunk_ref: Mapped[str | None] = mapped_column(Text)
    doc_ref: Mapped[str | None] = mapped_column(Text)
    name_ref: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_citation_sources_cited_at", "cited_at"),
        Index(
            "uq_citation_sources_audit",
            "audit_id",
            "ordinal",
            unique=True,
            postgresql_where=text("audit_id IS NOT NULL"),
        ),
        Index(
            "uq_citation_sources_eval",
            "eval_id",
            "ordinal",
            unique=True,
            postgresql_where=text("eval_id IS NOT NULL"),
        ),
    )


class CitationSyncState(Base):
    """How far each source table is expanded into ``citation_sources``."""

    __tablename__ = "citation_sync_state"

    origin: Mapped[str] = mapped_column(String(16), primary_key=True)
    synced_until: Mapped[datetime] = mapped_column(DateTime)


class ResourceSample(Base):
    """One worker's resource sample (``app.governance.resources``).

    Every API worker inserts one row per ``METRICS_SAMPLE_INTERVAL_SECONDS``;
    rows older than ``METRICS_HISTORY_MINUTES`` are pruned. The monitoring
    endpoint, the SSE stream and the resource alert rules aggregate the live
    workers of every replica from here (no Redis).
    """

    __tablename__ = "resource_samples"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(128))
    hostname: Mapped[str] = mapped_column(String(255))
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sample: Mapped[dict] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_resource_samples_sampled_at", "sampled_at"),
        Index("ix_resource_samples_worker_sampled", "worker_id", "sampled_at"),
    )
