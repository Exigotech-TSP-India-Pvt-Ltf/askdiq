r"""Incrementally ingest the authoritative Deploy IQ Markdown documents.

Run from backend/:
    python scripts/ingest_real_docs.py

Uses the single shared ingest_text() service, ensuring identical hashing,
PII redaction, canonical naming, and atomic transaction locks.
"""

import asyncio
import getpass
import sys
from pathlib import Path

# Running `python scripts/ingest_real_docs.py` puts scripts/ (not backend/)
# on sys.path[0], so `app` isn't importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, engine
from app.governance.audit import record_audit_event
from app.ingestion.service import ingest_text
from app.models.db_models import Chunk, Document

BASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = BASE_DIR / "data" / "real_docs"

DOCUMENTS = [
    "about-us.md",
    "cis-benchmark-assessment.md",
    "deploy-iq-platform.md",
    "e8-learn-more.md",
    "e8-service-page.md",
    "home-page.md",
    "iso-27001-technical-controls.md",
    "responsible-ai.md",
    "security-baseline-assessment.md",
    "trust-center.md",
    "zero-trust-learn-more.md",
]


async def ingest_one(db, filename: str) -> tuple[str, dict]:
    if not filename.lower().endswith(".md"):
        raise ValueError(
            f"CLI ingestion refuses non-.md file: '{filename}'. All documents must be canonical Markdown (.md)."
        )

    path = SOURCE_DIR / filename
    if not await asyncio.to_thread(path.exists):
        raise FileNotFoundError(f"Missing source document: {path}")

    content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    actor = f"cli:{getpass.getuser()}"

    res = await ingest_text(
        db,
        source_name=filename,
        source_type="markdown",
        content=content,
        chunking_strategy="auto",
        metadata={
            "corpus": "deploy_iq_authoritative",
            "ingestion_source": "real_source_document",
        },
        original_filename=filename,
        converted_from="md",
        actor=actor,
    )

    return filename, {
        "status": res["status"],
        "added": res["chunks_added"],
        "kept": res["chunks_kept"],
        "removed": res["chunks_removed"],
    }


async def remove_deleted_documents(db) -> list[str]:
    """Drop documents whose source file is no longer in DOCUMENTS."""
    stale = (
        await db.scalars(select(Document).where(Document.source_name.notin_(DOCUMENTS)))
    ).all()
    names = [d.source_name for d in stale]
    actor = f"cli:{getpass.getuser()}"

    for doc in stale:
        await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        await db.delete(doc)
        await record_audit_event(
            db,
            request_id=f"cli-del-{doc.id}",
            actor=actor,
            action="document_delete",
            detail={"document_id": str(doc.id), "source_name": doc.source_name},
            commit=False,
        )

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
