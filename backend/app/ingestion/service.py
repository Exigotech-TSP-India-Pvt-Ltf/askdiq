"""Incremental document ingestion shared by the /ingest API and the CLI script.

Re-ingesting a document whose content hash is unchanged is a no-op; for a
changed document only chunks whose hash is new are embedded, and chunks whose
content no longer exists are deleted.
"""

import asyncio
import hashlib
import time

from sqlalchemy import delete, select, update

from app.chunking.cleaning import strip_boilerplate_lines
from app.chunking.factory import get_strategy
from app.models.db_models import Chunk, Document
from app.retrieval.embeddings import embed_texts_sync

# Bump when the chunker output format or embedding model changes: it is folded
# into every hash, so a bump forces a full re-embed.
PIPELINE_VERSION = "v3"


def sha256(text: str) -> str:
    return hashlib.sha256((PIPELINE_VERSION + text).encode("utf-8")).hexdigest()


async def ingest_text(
    db,
    *,
    source_name: str,
    source_type: str,
    content: str,
    chunking_strategy: str = "auto",
    metadata: dict | None = None,
) -> dict:
    """Upsert a document by source_name. Returns stats plus the chunk results."""

    started = time.perf_counter()
    metadata = metadata or {}
    content = strip_boilerplate_lines(content)
    doc_hash = sha256(content)
    existing = await db.scalar(select(Document).where(Document.source_name == source_name))

    if existing is not None and existing.content_hash == doc_hash:
        total = await db.scalar(
            select(Chunk.id).where(Chunk.document_id == existing.id).limit(1)
        )
        if total is not None:
            return {"status": "unchanged", "document": existing, "added": 0, "kept": 0,
                    "removed": 0, "chunk_results": [], "chunk_ms": 0.0, "embed_ms": 0.0,
                    "started": started}

    if existing is None:
        document = Document(
            source_name=source_name,
            source_type=source_type,
            content_hash=doc_hash,
            doc_metadata=metadata,
        )
        db.add(document)
        await db.flush()
        status = "new"
    else:
        document = existing
        document.content_hash = doc_hash
        document.source_type = source_type
        document.doc_metadata = {**(document.doc_metadata or {}), **metadata}
        status = "updated"

    chunk_started = time.perf_counter()
    strategy = get_strategy(chunking_strategy, source_type=source_type)
    chunk_results = await asyncio.to_thread(
        strategy.chunk,
        content,
        {"document_id": str(document.id), "source_name": source_name, "source_type": source_type},
    )
    chunk_ms = (time.perf_counter() - chunk_started) * 1000

    new_chunks: dict[str, object] = {}
    for cr in chunk_results:
        new_chunks.setdefault(sha256(cr.content), cr)

    rows = (
        await db.execute(
            select(Chunk.id, Chunk.content_hash).where(Chunk.document_id == document.id)
        )
    ).all()
    existing_ids_by_hash: dict[str | None, list] = {}
    for chunk_id, h in rows:
        existing_ids_by_hash.setdefault(h, []).append(chunk_id)

    stale_ids = []
    for h, ids in existing_ids_by_hash.items():
        if h not in new_chunks:
            stale_ids.extend(ids)
        else:
            stale_ids.extend(ids[1:])
    if stale_ids:
        await db.execute(delete(Chunk).where(Chunk.id.in_(stale_ids)))

    to_add = [(h, cr) for h, cr in new_chunks.items() if h not in existing_ids_by_hash]
    embed_started = time.perf_counter()
    if to_add:
        embeddings = await asyncio.to_thread(
            embed_texts_sync, [cr.content for _, cr in to_add], "RETRIEVAL_DOCUMENT"
        )
        for (h, cr), embedding in zip(to_add, embeddings):
            db.add(
                Chunk(
                    document_id=document.id,
                    content=cr.content,
                    content_hash=h,
                    embedding=embedding,
                    strategy=cr.metadata.get("base_strategy", cr.metadata.get("strategy", chunking_strategy)),
                    chunk_index=cr.chunk_index,
                    chunk_metadata=cr.metadata,
                )
            )
    embed_ms = (time.perf_counter() - embed_started) * 1000

    kept = [(h, cr) for h, cr in new_chunks.items() if h in existing_ids_by_hash]
    for h, cr in kept:
        await db.execute(
            update(Chunk)
            .where(Chunk.document_id == document.id, Chunk.content_hash == h)
            .values(chunk_index=cr.chunk_index, chunk_metadata=cr.metadata)
        )

    await db.commit()
    return {
        "status": status,
        "document": document,
        "added": len(to_add),
        "kept": len(kept),
        "removed": len(stale_ids),
        "chunk_results": chunk_results,
        "chunk_ms": chunk_ms,
        "embed_ms": embed_ms,
        "started": started,
    }
