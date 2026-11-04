r"""Incrementally ingest the Deploy IQ website pages (``backend/data/*.md``).

Run from backend/:
    python scripts/ingest_real_docs.py

Each page's front matter is read (``app.ingestion.site_pages``), its body
cleaned (``app.ingestion.cleanup``) and paragraphs repeated across pages are
kept only on the most specific page (``app.ingestion.quality``). Then every
page goes through the single shared ingest_text() service (hashing, PII
redaction, section-tree chunking, atomic locks). The page ``route`` is stored
on the document, so citations link to SITE_BASE_URL + route. Documents not in
DOCUMENTS are removed, and a chunk report is printed and written to
reports/kb_ingest_report.json.
"""

import asyncio
import getpass
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Running `python scripts/ingest_real_docs.py` puts scripts/ (not backend/)
# on sys.path[0], so `app` isn't importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position  # must follow the sys.path fix above
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, engine
from app.governance.audit import record_audit_event
from app.ingestion.cleanup import clean_markdown
from app.ingestion.quality import chunk_report, dedupe_across_pages
from app.ingestion.service import ingest_text
from app.ingestion.site_pages import SitePage, parse_site_page
from app.knowledge.ingest_links import UploadLink
from app.knowledge.links import site_page_url
from app.models.db_models import Chunk, Document

BASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = BASE_DIR / "data"
REPORT_PATH = BASE_DIR / "reports" / "kb_ingest_report.json"

# One file per sitemap page (README.md and the site-wide _global.md are not
# knowledge: they hold conventions and navbar/footer copy).
DOCUMENTS = [
    "about-us.md",
    "booking.md",
    "cis-microsoft-365-foundations-benchmark-assessment.md",
    "contact-us.md",
    "essential-eight.md",
    "get-started.md",
    "home.md",
    "iso-27001-microsoft-365-technical-controls.md",
    "iso-42001-ai-management-system.md",
    "microsoft-365-security-baseline-assessment.md",
    "our-vision.md",
    "packages.md",
    "platform.md",
    "privacy.md",
    "responsible-ai.md",
    "secure-ai.md",
    "services.md",
    "terms.md",
    "zero-trust.md",
]


async def load_page(filename: str) -> SitePage:
    if not filename.lower().endswith(".md"):
        raise ValueError(
            f"CLI ingestion refuses non-.md file: '{filename}'. All documents must be canonical Markdown (.md)."
        )

    path = SOURCE_DIR / filename
    if not await asyncio.to_thread(path.exists):
        raise FileNotFoundError(f"Missing source document: {path}")

    raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
    return parse_site_page(raw)


async def ingest_one(db, filename: str, body: str | None = None) -> tuple[str, dict]:
    """Ingest one page; ``body`` replaces the file's body (de-duplicated text)."""
    page = await load_page(filename)
    actor = f"cli:{getpass.getuser()}"
    page_meta = {
        key: value
        for key, value in (
            ("route", page.route),
            ("title", page.title),
            ("description", page.description),
        )
        if value
    }

    res = await ingest_text(
        db,
        source_name=filename,
        source_type="markdown",
        content=body if body is not None else page.body,
        chunking_strategy="auto",
        metadata={
            "corpus": "deploy_iq_website",
            "ingestion_source": "site_page_snapshot",
            **page_meta,
        },
        original_filename=filename,
        converted_from="md",
        actor=actor,
        link=UploadLink(
            url=site_page_url(get_settings().site_base_url, page.route),
            title=page.title,
        ),
        can_change_link=True,
    )
    await _store_page_meta(db, filename, page_meta)

    return filename, {
        "status": res["status"],
        "added": res["chunks_added"],
        "kept": res["chunks_kept"],
        "removed": res["chunks_removed"],
    }


async def _store_page_meta(db, filename: str, page_meta: dict) -> None:
    """Keep route/title/description current on re-ingest (ingest_text only
    applies ``metadata`` to a new document)."""
    doc = await db.scalar(select(Document).where(Document.source_name == filename))
    if doc is None or not page_meta:
        return
    meta = doc.doc_metadata or {}
    if any(meta.get(key) != value for key, value in page_meta.items()):
        doc.doc_metadata = {**meta, **page_meta}
        await db.commit()


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


async def load_deduped_bodies() -> tuple[dict[str, str], list[dict]]:
    """Every page cleaned, with cross-page repeats kept on one page only."""
    pages = {name: await load_page(name) for name in DOCUMENTS}
    cleaned = {name: clean_markdown(page.body) for name, page in pages.items()}
    routes = {name: page.route for name, page in pages.items()}
    return dedupe_across_pages(cleaned, routes)


async def write_report(db, removals: list[dict]) -> int:
    """Print the chunk report; returns the number of warnings."""
    rows = (
        await db.execute(
            select(Document.source_name, Chunk.content)
            .join(Chunk, Chunk.document_id == Document.id)
            .where(Document.source_name.in_(DOCUMENTS))
            .order_by(Document.source_name, Chunk.chunk_index)
        )
    ).all()
    by_doc: dict[str, list[str]] = {}
    for name, content in rows:
        by_doc.setdefault(name, []).append(content)
    reports = [chunk_report(name, by_doc.get(name, [])) for name in DOCUMENTS]
    warnings = 0
    print("\nChunk report:")
    for report in reports:
        warnings += len(report.warnings)
        print(
            f"  {report.name}: {report.chunks} chunks, "
            f"{report.min_chars}-{report.max_chars} chars"
        )
        for warning in report.warnings:
            print(f"    ! {warning}")
    print(f"  cross-page repeats removed: {len(removals)}")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {"documents": [asdict(r) for r in reports], "dedupe": removals},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return warnings


async def main() -> None:
    bodies, removals = await load_deduped_bodies()
    async with AsyncSessionLocal() as db:
        totals = {"added": 0, "kept": 0, "removed": 0}

        for filename in DOCUMENTS:
            name, stats = await ingest_one(db, filename, bodies[filename])
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
        await write_report(db, removals)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
