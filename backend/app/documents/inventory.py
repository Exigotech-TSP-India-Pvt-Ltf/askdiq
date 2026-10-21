"""Document inventory: full ingestion metadata for the Documents page.

Everything is read from what ingestion stores: the ``documents`` row (hash,
timestamps, ``doc_metadata`` provenance, canonical Markdown), aggregates over
its ``chunks`` and the ``ingest`` audit events. Fields a document never
recorded (older ingests) stay ``None``; nothing is estimated.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.links import link_host, link_status
from app.knowledge.settings_service import current_knowledge_settings
from app.models.db_models import AuditLog, Chunk, Document, User
from app.schemas.document import (
    CanonicalMarkdown,
    ChunkSizeStats,
    DocumentDetail,
    DocumentSummary,
    IngestEvent,
)

SHORT_HASH_CHARS = 12
HISTORY_LIMIT = 50
INGEST_ACTION = "ingest"
MARKDOWN_EXTENSIONS = (".md", ".markdown")

# doc_metadata keys with a dedicated field; anything else scalar is a label.
_RESERVED_KEYS = frozenset(
    {
        "original_filename",
        "original_format",
        "original_sha256",
        "converted_from",
        "converter",
        "converter_version",
        "previous_content_hash",
        "ingest_count",
        "last_ingested_at",
        "last_ingested_by",
        "last_checked_at",
        "warnings",
        "quarantined",
        "content_hash",
        "canonical_format",
        "standardized",
        "original_size_bytes",
        "canonical_size_bytes",
        "canonical_chars",
        "chunking_strategy_requested",
        "chunking_strategy",
        "embedding_model",
        "embedding_dim",
        "doc_hash_version",
        "pipeline_version",
        "pii_redacted",
        "title",
    }
)


def as_utc(value: datetime | None) -> datetime | None:
    """Stored timestamps are naive UTC; return them timezone-aware."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def infer_original_format(meta: dict[str, Any], original_filename: str) -> str | None:
    """``docx`` / ``md`` from provenance, else from the original file name."""
    explicit = meta.get("converted_from") or meta.get("original_format")
    if explicit in ("md", "markdown"):
        return "md"
    if explicit:
        return str(explicit)
    lower = original_filename.lower()
    if lower.endswith(".docx"):
        return "docx"
    if lower.endswith(MARKDOWN_EXTENSIONS):
        return "md"
    return None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_list(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _labels(meta: dict[str, Any]) -> dict[str, str]:
    return {
        str(k): str(v)
        for k, v in meta.items()
        if k not in _RESERVED_KEYS and isinstance(v, str | int | float | bool)
    }


def _meta(doc: Document) -> dict[str, Any]:
    return doc.doc_metadata if isinstance(doc.doc_metadata, dict) else {}


@dataclass(frozen=True, slots=True)
class ChunkAggregate:
    count: int = 0
    total_chars: int = 0
    min_chars: int | None = None
    max_chars: int | None = None
    strategies: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LastIngest:
    """Latest ``ingest`` audit event of a document (fallback provenance)."""

    at: datetime
    actor: str
    events: int


def _status(quarantined: bool, chunk_count: int) -> str:
    if quarantined:
        return "quarantined"
    if chunk_count == 0:
        return "empty"
    return "active"


def build_summary(
    doc: Document,
    chunks: ChunkAggregate,
    *,
    canonical_bytes: int | None,
    last_ingest: LastIngest | None = None,
    actor_names: dict[str, str] | None = None,
    allowed_hosts: list[str] | None = None,
) -> DocumentSummary:
    """Pure mapping of one document row (+ aggregates) to its inventory entry."""
    meta = _meta(doc)
    source_url = doc.source_url
    original_filename = str(meta.get("original_filename") or doc.source_name)
    original_format = infer_original_format(meta, original_filename)
    full_hash = doc.content_hash or meta.get("content_hash")
    quarantined = bool(meta.get("quarantined", False))

    ingested_at = parse_iso(meta.get("last_ingested_at")) or (
        as_utc(last_ingest.at) if last_ingest else None
    )
    actor = meta.get("last_ingested_by") or (last_ingest.actor if last_ingest else None)
    names = actor_names or {}
    ingest_count = _int_or_none(meta.get("ingest_count")) or (
        last_ingest.events if last_ingest else 1
    )
    pii = meta.get("pii_redacted")
    standardized = meta.get("standardized")
    if not isinstance(standardized, bool):
        standardized = original_format not in (None, "md")
    strategies = chunks.strategies
    created = as_utc(doc.created_at) or datetime.now(UTC)
    if canonical_bytes is not None:
        canonical_size = canonical_bytes
    else:
        canonical_size = _int_or_none(meta.get("canonical_size_bytes"))

    return DocumentSummary(
        document_id=str(doc.id),
        source_name=doc.source_name,
        source_type=doc.source_type,
        chunk_count=chunks.count,
        created_at=created,
        updated_at=as_utc(doc.updated_at) or created,
        original_format=original_format or doc.source_type,
        active_format="MARKDOWN",
        converted_from=original_format,
        original_filename=original_filename,
        content_hash=full_hash[:SHORT_HASH_CHARS] if full_hash else None,
        original_sha256=meta.get("original_sha256") or None,
        ingest_count=ingest_count,
        warnings=_str_list(meta.get("warnings")),
        quarantined=quarantined,
        content_sha256=full_hash,
        canonical_format="md",
        standardized=standardized,
        converter=meta.get("converter"),
        converter_version=meta.get("converter_version"),
        original_size_bytes=_int_or_none(meta.get("original_size_bytes")),
        canonical_size_bytes=canonical_size,
        canonical_available=canonical_bytes is not None,
        total_chunk_chars=chunks.total_chars,
        chunk_strategies=strategies,
        chunking_strategy=meta.get("chunking_strategy")
        or (strategies[0] if len(strategies) == 1 else None),
        embedding_model=meta.get("embedding_model"),
        embedding_dim=_int_or_none(meta.get("embedding_dim")),
        ingested_at=ingested_at,
        ingested_by=names.get(str(actor), str(actor)) if actor else None,
        status=_status(quarantined, chunks.count),
        pii_redacted=_str_list(pii) if isinstance(pii, list) else None,
        pipeline_version=meta.get("pipeline_version"),
        labels=_labels(meta),
        source_url=source_url,
        section_links=bool(doc.section_links),
        has_link=source_url is not None,
        link_status=link_status(source_url, allowed_hosts or []).value,
        link_host=link_host(source_url),
    )


def ingest_event(created_at: datetime, actor: str, detail: Any) -> IngestEvent:
    """Pure mapping of one ``ingest`` audit row to a history entry."""
    d: dict[str, Any] = detail if isinstance(detail, dict) else {}
    pii = d.get("pii_redacted")
    quarantined = d.get("quarantined")
    return IngestEvent(
        at=as_utc(created_at) or created_at,
        actor=actor,
        status=d.get("status"),
        original_filename=d.get("original_filename"),
        converted_from=d.get("converted_from"),
        content_hash=d.get("content_hash"),
        chunks_added=_int_or_none(d.get("chunks_added")),
        chunks_kept=_int_or_none(d.get("chunks_kept")),
        chunks_removed=_int_or_none(d.get("chunks_removed")),
        total_chunks=_int_or_none(d.get("total_chunks")),
        embedding_tokens=_int_or_none(d.get("embedding_tokens")),
        warnings=_str_list(d.get("warnings")),
        pii_redacted=_str_list(pii) if isinstance(pii, list) else None,
        quarantined=quarantined if isinstance(quarantined, bool) else None,
    )


# --- queries -----------------------------------------------------------------


def _audit_doc_key() -> Any:
    """Lower-cased document name an ``ingest`` audit row refers to."""
    detail = AuditLog.detail
    return func.lower(
        func.coalesce(
            detail["source_name"].as_string(), detail["source_document"].as_string()
        )
    )


async def _chunk_aggregates(
    db: AsyncSession, document_id: uuid.UUID | None = None
) -> dict[uuid.UUID, ChunkAggregate]:
    length = func.length(Chunk.content)
    stmt = select(
        Chunk.document_id,
        func.count(Chunk.id),
        func.coalesce(func.sum(length), 0),
        func.min(length),
        func.max(length),
        func.array_agg(func.distinct(Chunk.strategy)),
    ).group_by(Chunk.document_id)
    if document_id is not None:
        stmt = stmt.where(Chunk.document_id == document_id)
    rows = (await db.execute(stmt)).all()
    return {
        doc_id: ChunkAggregate(
            count=int(count),
            total_chars=int(total),
            min_chars=low,
            max_chars=high,
            strategies=sorted(s for s in (strategies or []) if s),
        )
        for doc_id, count, total, low, high, strategies in rows
    }


async def _last_ingests(
    db: AsyncSession, names: Iterable[str]
) -> dict[str, LastIngest]:
    keys = sorted({n.lower() for n in names})
    if not keys:
        return {}
    key = _audit_doc_key().label("doc_key")
    ranked = (
        select(
            key,
            AuditLog.created_at,
            AuditLog.actor,
            func.count().over(partition_by=_audit_doc_key()).label("events"),
            func.row_number()
            .over(partition_by=_audit_doc_key(), order_by=AuditLog.created_at.desc())
            .label("rn"),
        )
        .where(AuditLog.action == INGEST_ACTION, _audit_doc_key().in_(keys))
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                ranked.c.doc_key, ranked.c.created_at, ranked.c.actor, ranked.c.events
            ).where(ranked.c.rn == 1)
        )
    ).all()
    return {
        k: LastIngest(at=as_utc(at) or at, actor=actor, events=int(events))
        for k, at, actor, events in rows
    }


async def _actor_names(db: AsyncSession, actors: Iterable[str]) -> dict[str, str]:
    """Map stored ingest actors (email, user id or Entra oid) to an email."""
    wanted = sorted({a for a in actors if a})
    if not wanted:
        return {}
    user_id = cast(User.id, Text)
    rows = (
        await db.execute(
            select(user_id, User.email, User.entra_oid).where(
                or_(
                    User.email.in_(wanted),
                    user_id.in_(wanted),
                    User.entra_oid.in_(wanted),
                )
            )
        )
    ).all()
    names: dict[str, str] = {}
    for uid, email, oid in rows:
        for alias in (uid, email, oid):
            if alias:
                names[alias] = email
    return names


def _actor_of(doc: Document, last: LastIngest | None) -> str | None:
    return _meta(doc).get("last_ingested_by") or (last.actor if last else None)


async def _summaries(
    db: AsyncSession, document_id: uuid.UUID | None = None
) -> list[DocumentSummary]:
    stmt = select(Document, func.octet_length(Document.canonical_markdown)).order_by(
        Document.created_at.desc()
    )
    if document_id is not None:
        stmt = stmt.where(Document.id == document_id)
    rows = (await db.execute(stmt)).all()
    if not rows:
        return []
    chunk_aggs = await _chunk_aggregates(db, document_id)
    last = await _last_ingests(db, (doc.source_name for doc, _ in rows))
    actors = [_actor_of(doc, last.get(doc.source_name.lower())) for doc, _ in rows]
    names = await _actor_names(db, (a for a in actors if a))
    allowed = current_knowledge_settings().allowed_hosts
    return [
        build_summary(
            doc,
            chunk_aggs.get(doc.id, ChunkAggregate()),
            canonical_bytes=canonical_bytes,
            last_ingest=last.get(doc.source_name.lower()),
            actor_names=names,
            allowed_hosts=allowed,
        )
        for doc, canonical_bytes in rows
    ]


async def list_documents(db: AsyncSession) -> list[DocumentSummary]:
    return await _summaries(db)


async def get_document_detail(
    db: AsyncSession, document_id: uuid.UUID
) -> DocumentDetail | None:
    found = await _summaries(db, document_id)
    if not found:
        return None
    summary = found[0]
    rows = (
        await db.execute(
            select(AuditLog.created_at, AuditLog.actor, AuditLog.detail)
            .where(
                AuditLog.action == INGEST_ACTION,
                _audit_doc_key() == summary.source_name.lower(),
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(HISTORY_LIMIT + 1)
        )
    ).all()
    agg = (await _chunk_aggregates(db, document_id)).get(document_id, ChunkAggregate())
    return DocumentDetail(
        **summary.model_dump(),
        history=[ingest_event(at, actor, detail) for at, actor, detail in rows][
            :HISTORY_LIMIT
        ],
        history_truncated=len(rows) > HISTORY_LIMIT,
        chunk_stats=ChunkSizeStats(
            count=agg.count,
            total_chars=agg.total_chars,
            min_chars=agg.min_chars,
            max_chars=agg.max_chars,
            avg_chars=round(agg.total_chars / agg.count, 1) if agg.count else None,
        ),
    )


async def get_canonical_markdown(
    db: AsyncSession, document_id: uuid.UUID
) -> CanonicalMarkdown | None:
    row = (
        await db.execute(
            select(
                Document.id,
                Document.source_name,
                Document.content_hash,
                Document.canonical_markdown,
            ).where(Document.id == document_id)
        )
    ).first()
    if row is None:
        return None
    doc_id, name, content_hash, text = row
    return CanonicalMarkdown(
        document_id=str(doc_id),
        source_name=name,
        available=text is not None,
        content=text,
        size_bytes=len(text.encode("utf-8")) if text is not None else None,
        content_sha256=content_hash,
    )


async def document_exists(db: AsyncSession, document_id: uuid.UUID) -> bool:
    found = await db.scalar(select(Document.id).where(Document.id == document_id))
    return found is not None
