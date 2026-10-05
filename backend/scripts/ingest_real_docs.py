r"""Incrementally ingest the authoritative Deploy IQ Markdown documents.

Run from backend/:
    python scripts/ingest_real_docs.py

Re-running is cheap: documents whose content hash is unchanged are skipped,
and for changed documents only chunks whose hash is new are embedded. Chunks
whose content no longer exists in the file are deleted.
"""

import asyncio
import hashlib
import sys
from pathlib import Path

from sqlalchemy import delete, select, update

# Running `python scripts/ingest_real_docs.py` puts scripts/ (not backend/)
# on sys.path[0], so `app` isn't importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.chunking.factory import get_strategy
from app.core.database import AsyncSessionLocal, engine
from app.models.db_models import Chunk, Document
from app.retrieval.embeddings import embed_texts_sync


BASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = BASE_DIR / "data" / "real_docs"

# Bump this whenever the chunker output format or the embedding model
# changes. It is folded into every hash, so a bump forces a full re-embed.
# Must match PIPELINE_VERSION in the alembic backfill migration.
PIPELINE_VERSION = "v1"

DOCUMENTS = [
    "about-us.md",
    "deploy-iq-platform.md",
    "e8-learn-more.md",
    "e8-service-page.md",
    "home-page.md",
    "responsible-ai.md",
    "trust-center.md",
    "zero-trust-learn-more.md",
]


def _sha256(text: str) -> str:
    return hashlib.sha256((PIPELINE_VERSION + text).encode("utf-8")).hexdigest()


async def ingest_one(db, filename: str) -> tuple[str, dict]:
    path = SOURCE_DIR / filename

    if not path.exists():
        raise FileNotFoundError(f"Missing source document: {path}")

    content = path.read_text(encoding="utf-8")
    doc_hash = _sha256(content)

    existing = await db.scalar(select(Document).where(Document.source_name == filename))

    # 1. Whole-document shortcut: nothing changed, nothing to do.
    if existing is not None and existing.content_hash == doc_hash:
        return filename, {"status": "unchanged", "added": 0, "kept": 0, "removed": 0}

    if existing is None:
        document = Document(
            source_name=filename,
            source_type="markdown",
            content_hash=doc_hash,
            doc_metadata={
                "corpus": "deploy_iq_authoritative",
                "ingestion_source": "real_source_document",
            },
        )
        db.add(document)
        await db.flush()
        status = "new"
    else:
        document = existing
        document.content_hash = doc_hash
        status = "updated"

    # 2. Chunk locally (free) and hash each chunk. Identical chunks inside
    #    one document collapse to a single row.
    strategy = get_strategy("auto", source_type="markdown")
    chunk_results = strategy.chunk(
        content,
        source_metadata={
            "document_id": str(document.id),
            "source_name": filename,
            "source_type": "markdown",
        },
    )

    new_chunks: dict[str, object] = {}
    for cr in chunk_results:
        new_chunks.setdefault(_sha256(cr.content), cr)

    # 3. What does the DB already hold for this document?
    rows = (
        await db.execute(
            select(Chunk.id, Chunk.content_hash).where(Chunk.document_id == document.id)
        )
    ).all()

    existing_ids_by_hash: dict[str | None, list] = {}
    for chunk_id, h in rows:
        existing_ids_by_hash.setdefault(h, []).append(chunk_id)

    # 4. Delete rows that are stale (hash gone from the file) or duplicate.
    stale_ids = []
    for h, ids in existing_ids_by_hash.items():
        if h not in new_chunks:
            stale_ids.extend(ids)  # removed/edited content, or NULL hash
        else:
            stale_ids.extend(ids[1:])  # keep one row per hash
    if stale_ids:
        await db.execute(delete(Chunk).where(Chunk.id.in_(stale_ids)))

    # 5. Embed + insert only chunks whose hash is not already stored.
    to_add = [(h, cr) for h, cr in new_chunks.items() if h not in existing_ids_by_hash]
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
                    strategy=cr.metadata.get(
                        "base_strategy", cr.metadata.get("strategy", "auto")
                    ),
                    chunk_index=cr.chunk_index,
                    chunk_metadata=cr.metadata,
                )
            )

    # 6. Kept chunks: refresh position/metadata only (no re-embedding), since
    #    inserting a paragraph above them shifts chunk_index.
    kept = [(h, cr) for h, cr in new_chunks.items() if h in existing_ids_by_hash]
    for h, cr in kept:
        await db.execute(
            update(Chunk)
            .where(Chunk.document_id == document.id, Chunk.content_hash == h)
            .values(chunk_index=cr.chunk_index, chunk_metadata=cr.metadata)
        )

    await db.commit()

    return filename, {
        "status": status,
        "added": len(to_add),
        "kept": len(kept),
        "removed": len(stale_ids),
    }


async def remove_deleted_documents(db) -> list[str]:
    """Drop documents whose source file is no longer in DOCUMENTS."""
    stale = (
        await db.scalars(
            select(Document).where(Document.source_name.notin_(DOCUMENTS))
        )
    ).all()
    names = [d.source_name for d in stale]
    for doc in stale:
        await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        await db.delete(doc)
    if stale:
        await db.commit()
    return names


async def main() -> None:
    async with AsyncSessionLocal() as db:
        totals = {"added": 0, "kept": 0, "removed": 0}

        for filename in DOCUMENTS:
            name, stats = await ingest_one(db, filename)
            for key in totals:
                totals[key] += stats[key]
            print(
                f"{name}: {stats['status']} "
                f"(embedded {stats['added']}, kept {stats['kept']}, removed {stats['removed']})"
            )

        removed_docs = await remove_deleted_documents(db)
        if removed_docs:
            print(f"\nRemoved documents no longer in source list: {removed_docs}")

        print(
            f"\nTotal: embedded {totals['added']} new chunks, "
            f"kept {totals['kept']}, removed {totals['removed']}"
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())