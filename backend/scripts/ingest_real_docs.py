r"""Ingest the eight authoritative Deploy IQ Markdown documents.

Run from:
    C:\Projects\RAG\backend

Expected source directory:
    C:\Projects\RAG\backend\data\real_docs

The script uses the existing chunking factory and embedding pipeline, so the
same ingestion behavior is used by the application.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import delete, select

# Running `python scripts/ingest_real_docs.py` puts scripts/ (not backend/)
# on sys.path[0], so `app` isn't importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.chunking.factory import get_strategy
from app.core.database import AsyncSessionLocal, engine
from app.models.db_models import Chunk, Document
from app.retrieval.embeddings import embed_texts_sync


BASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = BASE_DIR / "data" / "real_docs"

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


async def ingest_one(db, filename: str) -> tuple[str, int, str]:
    path = SOURCE_DIR / filename

    if not path.exists():
        raise FileNotFoundError(f"Missing source document: {path}")

    content = path.read_text(encoding="utf-8")

    # This corpus is Markdown, so "auto" selects structure-aware chunking.
    strategy = get_strategy("auto", source_type="markdown")

    # Make the script safe to rerun: replace an existing copy of the same source.
    existing = await db.scalar(select(Document).where(Document.source_name == filename))

    if existing is not None:
        await db.execute(delete(Chunk).where(Chunk.document_id == existing.id))
        await db.delete(existing)
        await db.flush()

    document = Document(
        source_name=filename,
        source_type="markdown",
        doc_metadata={
            "corpus": "deploy_iq_authoritative",
            "ingestion_source": "real_source_document",
        },
    )
    db.add(document)
    await db.flush()

    chunks = strategy.chunk(
        content,
        source_metadata={
            "document_id": str(document.id),
            "source_name": filename,
            "source_type": "markdown",
        },
    )

    embeddings = (
        await asyncio.to_thread(
            embed_texts_sync,
            [chunk.content for chunk in chunks],
            "RETRIEVAL_DOCUMENT",
        )
        if chunks
        else []
    )

    for chunk_result, embedding in zip(chunks, embeddings):
        db.add(
            Chunk(
                document_id=document.id,
                content=chunk_result.content,
                embedding=embedding,
                strategy=chunk_result.metadata.get(
                    "base_strategy",
                    chunk_result.metadata.get("strategy", "auto"),
                ),
                chunk_index=chunk_result.chunk_index,
                chunk_metadata=chunk_result.metadata,
            )
        )

    await db.commit()

    base_strategy = (
        chunks[0].metadata.get("base_strategy", "structure_aware")
        if chunks
        else "structure_aware"
    )

    return filename, len(chunks), base_strategy


async def main() -> None:
    async with AsyncSessionLocal() as db:
        total = 0

        for filename in DOCUMENTS:
            name, count, strategy = await ingest_one(db, filename)
            total += count
            print(f"{name}: {count} chunks ({strategy})")

        print(f"\nTotal documents: {len(DOCUMENTS)}")
        print(f"Total chunks: {total}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
