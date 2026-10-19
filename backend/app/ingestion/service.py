"""Atomic, concurrency-safe document ingestion service (Tasks 1.4.5 & 1.4.6).

Implements the three-phase ingestion architecture:
- Phase A: Short read transaction (detects document identity, checks hash, fetches existing chunk hashes)
- Phase B: No DB connection held (text normalization, structure chunking, batched embeddings 16-32)
- Phase C: Short write transaction under advisory lock pg_advisory_xact_lock(hashtext(lower(name)))
  with atomic audit logging.
"""

import time
import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.chunking.factory import get_strategy
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.governance.audit import record_audit_event
from app.ingestion.naming import canonical_source_name
from app.ingestion.normalizer import (
    DOC_HASH_VERSION,
    PIPELINE_VERSION,
    chunk_content_hash,
    doc_content_hash,
    normalize_markdown,
)
from app.model_settings.service import current_model_settings
from app.models.db_models import Chunk, Document
from app.retrieval.embeddings import embed_texts

logger = get_logger(__name__)
settings = get_settings()

# Converter recorded per source format (provenance shown on the Documents page).
CONVERTERS: dict[str, str] = {"docx": "mammoth+gfm", "md": "markdown-normalizer"}


def provenance_extras(
    *,
    converted_from: str,
    original_size_bytes: int,
    canonical_markdown: str,
    requested_strategy: str,
    strategy_used: str,
    pii_found: list[str],
) -> dict:
    """Provenance fields stored in ``documents.doc_metadata`` on every write."""
    return {
        "canonical_format": "md",
        "standardized": converted_from != "md",
        "converter": CONVERTERS.get(converted_from, converted_from),
        "original_size_bytes": original_size_bytes,
        "canonical_size_bytes": len(canonical_markdown.encode("utf-8")),
        "canonical_chars": len(canonical_markdown),
        "chunking_strategy_requested": requested_strategy,
        "chunking_strategy": strategy_used,
        "embedding_model": current_model_settings().embedding_model,
        "embedding_dim": settings.embedding_dim,
        "doc_hash_version": DOC_HASH_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "pii_redacted": list(pii_found),
    }


def backfill_provenance(
    meta: dict,
    *,
    converted_from: str,
    original_filename: str,
    original_size_bytes: int,
    canonical_markdown: str,
    pii_found: list[str],
) -> dict:
    """``meta`` plus provenance it never recorded (unchanged re-upload).

    Stored values always win. The origin is the stored one when known, so a
    .md re-upload of a .docx-born document does not flip it to "native"; the
    original size is only filled when this upload has that origin. Nothing
    was re-chunked, so chunking fields are not guessed.
    """
    stored = meta.get("original_format") or meta.get("converted_from")
    origin = "md" if stored in ("md", "markdown") else (stored or converted_from)
    extras = provenance_extras(
        converted_from=origin,
        original_size_bytes=original_size_bytes,
        canonical_markdown=canonical_markdown,
        requested_strategy="",
        strategy_used="",
        pii_found=pii_found,
    )
    for key in ("chunking_strategy", "chunking_strategy_requested"):
        extras.pop(key)
    if origin != converted_from:
        extras.pop("original_size_bytes")
    extras["original_format"] = origin
    if origin == converted_from:
        extras["original_filename"] = original_filename
    return {**extras, **meta}


async def ingest_text(
    db: AsyncSession,
    *,
    source_name: str,
    source_type: str = "markdown",
    content: str,
    chunking_strategy: str = "auto",
    metadata: dict | None = None,
    original_filename: str | None = None,
    converted_from: str = "md",
    original_sha256: str | None = None,
    original_size_bytes: int | None = None,
    warnings: list[str] | None = None,
    quarantined: bool = False,
    force: bool = False,
    dry_run: bool = False,
    actor: str = "system",
    request_id: str | None = None,
    client_ip: str | None = None,
) -> dict:
    """Upsert document by canonical source name with 3-phase concurrency safety."""
    started = time.perf_counter()
    request_id = request_id or f"ingest-{uuid.uuid4().hex[:12]}"
    warnings_list = list(warnings or [])
    if original_size_bytes is None:
        original_size_bytes = len(content.encode("utf-8"))

    # 1. Canonical naming
    canonical_name = canonical_source_name(source_name)
    logger.info(
        "ingest_started", source=canonical_name, char_len=len(content), force=force
    )

    # 2. Deterministic normalization
    normalized_content, pii_found = normalize_markdown(content)
    new_doc_hash = doc_content_hash(normalized_content)
    if pii_found:
        logger.info("ingest_pii_detected", source=canonical_name, pii_types=pii_found)

    # -------------------------------------------------------------------------
    # PHASE A: Short Read Transaction
    # -------------------------------------------------------------------------
    existing = await db.scalar(
        select(Document).where(
            func.lower(Document.source_name) == func.lower(canonical_name)
        )
    )

    existing_chunk_rows = []
    if existing is not None:
        existing_chunk_rows = (
            await db.execute(
                select(Chunk.id, Chunk.content_hash).where(
                    Chunk.document_id == existing.id
                )
            )
        ).all()

    existing_chunks_by_hash: dict[str, list[uuid.UUID]] = {}
    for cid, chash in existing_chunk_rows:
        if chash:
            existing_chunks_by_hash.setdefault(chash, []).append(cid)

    # Check for unchanged condition
    if (
        existing is not None
        and not force
        and existing.content_hash == new_doc_hash
        and len(existing_chunk_rows) > 0
    ):
        logger.info(
            "ingest_skipped_unchanged", source=canonical_name, hash=new_doc_hash[:12]
        )
        if not dry_run:
            existing.doc_metadata = {
                **backfill_provenance(
                    existing.doc_metadata or {},
                    converted_from=converted_from,
                    original_filename=original_filename or canonical_name,
                    original_size_bytes=original_size_bytes,
                    canonical_markdown=normalized_content,
                    pii_found=pii_found,
                ),
                "last_checked_at": datetime.utcnow().isoformat(),
            }
            # Same hash = same canonical text: store it if it predates the column.
            await db.execute(
                update(Document)
                .where(
                    Document.id == existing.id,
                    Document.canonical_markdown.is_(None),
                )
                .values(canonical_markdown=normalized_content)
            )
            await record_audit_event(
                db,
                request_id=request_id,
                actor=actor,
                action="ingest",
                detail={
                    "source_name": canonical_name,
                    "original_filename": original_filename or canonical_name,
                    "converted_from": converted_from,
                    "status": "unchanged",
                    "content_hash": new_doc_hash[:12],
                    "chunks_added": 0,
                    "chunks_kept": len(existing_chunk_rows),
                    "chunks_removed": 0,
                    "pii_redacted": pii_found,
                    "warnings": warnings_list,
                    "quarantined": quarantined,
                    "embedding_tokens": 0,
                },
                ip_address=client_ip,
                commit=True,
            )

        return {
            "status": "unchanged",
            "document": existing,
            "document_id": str(existing.id),
            "source_name": canonical_name,
            "original_filename": original_filename or canonical_name,
            "converted_from": converted_from,
            "content_hash": new_doc_hash,
            "previous_content_hash": existing.content_hash,
            "chunk_count": len(existing_chunk_rows),
            "chunks_added": 0,
            "chunks_kept": len(existing_chunk_rows),
            "chunks_removed": 0,
            "embedding_tokens": 0,
            "embedding_cost_usd": 0.0,
            "strategy_used": chunking_strategy,
            "warnings": warnings_list,
            "quarantined": quarantined,
            "original_size_bytes": original_size_bytes,
            "canonical_size_bytes": len(normalized_content.encode("utf-8")),
            "dry_run": dry_run,
            "started": started,
            "chunk_ms": 0.0,
            "embed_ms": 0.0,
        }

    # -------------------------------------------------------------------------
    # PHASE B: CPU Chunking & Network Embedding (NO DB Connection held)
    # -------------------------------------------------------------------------
    chunk_start = time.perf_counter()
    strategy = get_strategy(chunking_strategy, source_type=source_type)
    chunk_results = await strategy.chunk(
        normalized_content,
        {
            "source_name": canonical_name,
            "source_type": source_type,
        },
    )
    chunk_ms = (time.perf_counter() - chunk_start) * 1000

    new_chunks: dict[str, object] = {}
    for cr in chunk_results:
        new_chunks.setdefault(chunk_content_hash(cr.content), cr)

    # Determine status & diff
    if existing is None:
        status = "new"
        to_add_hashes = list(new_chunks.keys())
        kept_hashes: list[str] = []
        stale_chunk_ids: list[uuid.UUID] = []
    elif force:
        status = "forced"
        to_add_hashes = list(new_chunks.keys())
        kept_hashes = []
        stale_chunk_ids = [
            cid for ids in existing_chunks_by_hash.values() for cid in ids
        ]
    elif not existing_chunk_rows and existing.content_hash == new_doc_hash:
        status = "repaired"
        to_add_hashes = list(new_chunks.keys())
        kept_hashes = []
        stale_chunk_ids = []
    else:
        status = "updated"
        to_add_hashes = [h for h in new_chunks if h not in existing_chunks_by_hash]
        kept_hashes = [h for h in new_chunks if h in existing_chunks_by_hash]
        stale_chunk_ids = []
        for h, ids in existing_chunks_by_hash.items():
            if h not in new_chunks:
                stale_chunk_ids.extend(ids)
            else:
                stale_chunk_ids.extend(ids[1:])  # deduplicate if extra copies exist

    # Compute estimated tokens
    est_embed_tokens = sum(
        len(getattr(new_chunks[h], "content", "").split()) for h in to_add_hashes
    )
    est_cost = (
        est_embed_tokens / 1_000_000
    ) * current_model_settings().price_embed_per_million

    if dry_run:
        return {
            "status": status,
            "document": existing,
            "document_id": str(existing.id) if existing else "",
            "source_name": canonical_name,
            "original_filename": original_filename or canonical_name,
            "converted_from": converted_from,
            "content_hash": new_doc_hash,
            "previous_content_hash": existing.content_hash if existing else None,
            "chunk_count": len(chunk_results),
            "chunks_added": len(to_add_hashes),
            "chunks_kept": len(kept_hashes),
            "chunks_removed": len(stale_chunk_ids),
            "embedding_tokens": est_embed_tokens,
            "embedding_cost_usd": est_cost,
            "strategy_used": getattr(strategy, "name", chunking_strategy),
            "warnings": warnings_list,
            "quarantined": quarantined,
            "original_size_bytes": original_size_bytes,
            "canonical_size_bytes": len(normalized_content.encode("utf-8")),
            "dry_run": True,
            "started": started,
            "chunk_ms": chunk_ms,
            "embed_ms": 0.0,
        }

    # Embed only new chunk hashes in batches of 32
    embed_start = time.perf_counter()
    embeddings_map: dict[str, list[float]] = {}
    if to_add_hashes:
        texts_to_embed = [getattr(new_chunks[h], "content", "") for h in to_add_hashes]
        batch_size = 32
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts_to_embed), batch_size):
            batch = texts_to_embed[i : i + batch_size]
            batch_vectors = await embed_texts(
                batch, "RETRIEVAL_DOCUMENT", stage="ingest"
            )
            all_embeddings.extend(batch_vectors)
        for h, vec in zip(to_add_hashes, all_embeddings, strict=False):
            embeddings_map[h] = vec
    embed_ms = (time.perf_counter() - embed_start) * 1000

    # -------------------------------------------------------------------------
    # PHASE C: Short Write Transaction Under Advisory Lock
    # -------------------------------------------------------------------------
    # Advisory lock automatically releases when transaction ends
    await db.scalar(
        text("SELECT pg_advisory_xact_lock(hashtext(lower(:name)))"),
        {"name": canonical_name},
    )

    # Re-fetch document inside lock to verify concurrency
    current_doc = await db.scalar(
        select(Document).where(
            func.lower(Document.source_name) == func.lower(canonical_name)
        )
    )

    provenance = {
        **(current_doc.doc_metadata if current_doc else (metadata or {})),
        "original_filename": original_filename or canonical_name,
        "original_format": converted_from,
        "original_sha256": original_sha256 or "",
        "converter_version": "v1",
        "previous_content_hash": existing.content_hash if existing else None,
        "ingest_count": (
            (
                current_doc.doc_metadata.get("ingest_count", 0)
                if current_doc and current_doc.doc_metadata
                else 0
            )
            + 1
        ),
        "last_ingested_at": datetime.utcnow().isoformat(),
        "last_ingested_by": actor,
        "warnings": warnings_list,
        "quarantined": quarantined,
        **provenance_extras(
            converted_from=converted_from,
            original_size_bytes=original_size_bytes,
            canonical_markdown=normalized_content,
            requested_strategy=chunking_strategy,
            strategy_used=getattr(strategy, "name", chunking_strategy),
            pii_found=pii_found,
        ),
    }

    if current_doc is None:
        document = Document(
            source_name=canonical_name,
            source_type=source_type,
            content_hash=new_doc_hash,
            doc_metadata=provenance,
            canonical_markdown=normalized_content,
        )
        db.add(document)
        await db.flush()
    else:
        document = current_doc
        document.content_hash = new_doc_hash
        document.updated_at = datetime.utcnow()
        document.source_type = source_type
        document.doc_metadata = provenance
        document.canonical_markdown = normalized_content

    # Delete stale chunks
    if stale_chunk_ids:
        await db.execute(delete(Chunk).where(Chunk.id.in_(stale_chunk_ids)))

    # Insert new chunks
    for h in to_add_hashes:
        cr = new_chunks[h]
        db.add(
            Chunk(
                document_id=document.id,
                content=getattr(cr, "content", ""),
                content_hash=h,
                embedding=embeddings_map[h],
                strategy=getattr(cr, "metadata", {}).get(
                    "base_strategy",
                    getattr(cr, "metadata", {}).get("strategy", chunking_strategy),
                ),
                chunk_index=getattr(cr, "chunk_index", 0),
                chunk_metadata=getattr(cr, "metadata", {}),
            )
        )

    # Update kept chunks index & metadata
    for h in kept_hashes:
        cr = new_chunks[h]
        await db.execute(
            update(Chunk)
            .where(Chunk.document_id == document.id, Chunk.content_hash == h)
            .values(
                chunk_index=getattr(cr, "chunk_index", 0),
                chunk_metadata=getattr(cr, "metadata", {}),
            )
        )

    # Record mutation audit event in the SAME transaction (fail closed)
    await record_audit_event(
        db,
        request_id=request_id,
        actor=actor,
        action="ingest",
        detail={
            "source_name": canonical_name,
            "original_filename": original_filename or canonical_name,
            "converted_from": converted_from,
            "status": status,
            "previous_content_hash": (
                existing.content_hash[:12]
                if existing and existing.content_hash
                else None
            ),
            "content_hash": new_doc_hash[:12],
            "chunks_added": len(to_add_hashes),
            "chunks_kept": len(kept_hashes),
            "chunks_removed": len(stale_chunk_ids),
            "pii_redacted": pii_found,
            "warnings": warnings_list,
            "quarantined": quarantined,
            "embedding_tokens": est_embed_tokens,
            "embedding_cost_usd": est_cost,
            # Keys the usage/activity readers use (same values, additive).
            "cost_usd": est_cost,
            "document_id": str(document.id),
            "total_chunks": len(new_chunks),
        },
        ip_address=client_ip,
        commit=False,
    )

    await db.commit()

    total_duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "ingest_completed",
        source=canonical_name,
        status=status,
        chunks_added=len(to_add_hashes),
        chunks_kept=len(kept_hashes),
        chunks_removed=len(stale_chunk_ids),
        tokens=est_embed_tokens,
        duration_ms=round(total_duration_ms, 2),
    )

    return {
        "status": status,
        "document": document,
        "document_id": str(document.id),
        "source_name": canonical_name,
        "original_filename": original_filename or canonical_name,
        "converted_from": converted_from,
        "content_hash": new_doc_hash,
        "previous_content_hash": existing.content_hash if existing else None,
        "chunk_count": len(chunk_results),
        "chunks_added": len(to_add_hashes),
        "chunks_kept": len(kept_hashes),
        "chunks_removed": len(stale_chunk_ids),
        "embedding_tokens": est_embed_tokens,
        "embedding_cost_usd": est_cost,
        "strategy_used": getattr(strategy, "name", chunking_strategy),
        "warnings": warnings_list,
        "quarantined": quarantined,
        "original_size_bytes": original_size_bytes,
        "canonical_size_bytes": len(normalized_content.encode("utf-8")),
        "dry_run": False,
        "started": started,
        "chunk_ms": chunk_ms,
        "embed_ms": embed_ms,
    }
