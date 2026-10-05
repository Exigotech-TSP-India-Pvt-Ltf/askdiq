"""SQLAlchemy ORM models backing users, documents, chunks, latency, and audit data."""
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index,Integer, String, Text
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
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True) 
    doc_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (                                                           # <-- NEW
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
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    total_ms: Mapped[float] = mapped_column(Float)
    embedding_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    retrieval_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    guardrail_pre_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    guardrail_post_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


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
    created_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

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

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
