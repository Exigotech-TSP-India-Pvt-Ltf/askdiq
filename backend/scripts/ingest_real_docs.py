r"""Incrementally ingest the Deploy IQ website pages (``backend/data/*.md``).

Run from backend/:
    python scripts/ingest_real_docs.py

Each page's front matter is read (``app.ingestion.site_pages``), its body
cleaned (``app.ingestion.cleanup``) and paragraphs repeated across pages are
kept only on the most specific page (``app.ingestion.quality``). Then every
page goes through the single shared ingest_text() service (hashing, PII
redaction, section-tree chunking, atomic locks); a page whose cleaned text is
unchanged is skipped. The page ``route`` is stored on the document, so
citations link to SITE_BASE_URL + route.

Pages are discovered: every ``data/*.md`` except README.md and _global.md.
Only documents this script owns (``OWNED_METADATA``) whose file left data/
are removed; documents uploaded through the admin screen are never touched.
A chunk report is printed and written to reports/kb_ingest_report.json.
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
from sqlalchemy import ColumnElement, Select, and_, delete, func, select

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

# Not knowledge: README.md holds conventions, _global.md the site-wide
# navbar/footer copy. Compared case-insensitively.
EXCLUDED_FILES = frozenset({"readme.md", "_global.md"})

# Written on every document this script creates. Only documents carrying
# both values may be deleted by it; admin uploads are left alone.
CORPUS = "deploy_iq_website"
INGESTION_SOURCE = "site_page_snapshot"
OWNED_METADATA = {"corpus": CORPUS, "ingestion_source": INGESTION_SOURCE}


def discover_pages(source_dir: Path = SOURCE_DIR) -> list[str]:
    """Every page file in ``source_dir``, sorted; a new file needs no code."""
    return sorted(
        path.name
        for path in source_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".md"
        and path.name.lower() not in EXCLUDED_FILES
    )


# The page list, kept for scripts/inventory_kb_docs.py.
DOCUMENTS = discover_pages()


def is_owned(metadata: dict | None) -> bool:
    """True when ``metadata`` marks a document ingested by this script."""
    meta = metadata if isinstance(metadata, dict) else {}
    return all(meta.get(key) == value for key, value in OWNED_METADATA.items())


def owned_by_script() -> ColumnElement[bool]:
    """SQL form of :func:`is_owned`."""
    return and_(
        *(
            Document.doc_metadata[key].as_string() == value
            for key, value in OWNED_METADATA.items()
        )
    )


def stale_owned_query(pages: list[str]) -> Select:
    """Documents this script owns whose page file is not in ``pages``."""
    names = [name.lower() for name in pages]
    return select(Document).where(
        owned_by_script(), func.lower(Document.source_name).notin_(names)
    )


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
        metadata={**OWNED_METADATA, **page_meta},
        original_filename=filename,
        converted_from="md",
        actor=actor,
        link=UploadLink(
            url=site_page_url(get_settings().site_base_url, page.route),
            title=page.title,
        ),
        can_change_link=True,
    )
    await _store_page_meta(db, filename, page_meta, actor)

    return filename, {
        "status": res["status"],
        "added": res["chunks_added"],
        "kept": res["chunks_kept"],
        "removed": res["chunks_removed"],
    }


def metadata_updates(meta: dict | None, page_meta: dict) -> dict:
    """Ownership labels and page fields that differ from ``meta``."""
    current = meta if isinstance(meta, dict) else {}
    wanted = {**OWNED_METADATA, **page_meta}
    return {key: value for key, value in wanted.items() if current.get(key) != value}


async def _store_page_meta(db, filename: str, page_meta: dict, actor: str) -> None:
    """Keep ownership labels and route/title/description current on every run,
    unchanged pages included (ingest_text only applies ``metadata`` to a new
    document). A label change is audited."""
    doc = await db.scalar(select(Document).where(Document.source_name == filename))
    if doc is None:
        return
    meta = doc.doc_metadata or {}
    updates = metadata_updates(meta, page_meta)
    if not updates:
        return
    doc.doc_metadata = {**meta, **updates}
    relabel = {key: updates[key] for key in OWNED_METADATA if key in updates}
    if relabel:
        await record_audit_event(
            db,
            request_id=f"cli-relabel-{doc.id}",
            actor=actor,
            action="document_relabel",
            detail={
                "document_id": str(doc.id),
                "source_name": doc.source_name,
                "before": {key: meta.get(key) for key in relabel},
                "after": relabel,
            },
            commit=False,
        )
    await db.commit()


async def remove_deleted_documents(db, pages: list[str]) -> list[str]:
    """Drop documents this script owns whose page file left data/."""
    rows = (await db.scalars(stale_owned_query(pages))).all()
    # Re-checked in Python so a deletion never rests on the SQL alone.
    stale = [doc for doc in rows if is_owned(doc.doc_metadata)]
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


async def load_deduped_bodies(
    names: list[str],
) -> tuple[dict[str, str], list[dict]]:
    """Every page cleaned, with cross-page repeats kept on one page only."""
    pages = {name: await load_page(name) for name in names}
    cleaned = {name: clean_markdown(page.body) for name, page in pages.items()}
    routes = {name: page.route for name, page in pages.items()}
    return dedupe_across_pages(cleaned, routes)


async def write_report(db, names: list[str], removals: list[dict]) -> int:
    """Print the chunk report; returns the number of warnings."""
    rows = (
        await db.execute(
            select(Document.source_name, Chunk.content)
            .join(Chunk, Chunk.document_id == Document.id)
            .where(Document.source_name.in_(names))
            .order_by(Document.source_name, Chunk.chunk_index)
        )
    ).all()
    by_doc: dict[str, list[str]] = {}
    for name, content in rows:
        by_doc.setdefault(name, []).append(content)
    reports = [chunk_report(name, by_doc.get(name, [])) for name in names]
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


MIN_PAGES = 1


def check_pages(pages: list[str], source_dir: Path = SOURCE_DIR) -> None:
    """No page files found (e.g. missing from a container image): stop before
    anything is removed, or every owned document would be deleted."""
    if len(pages) < MIN_PAGES:
        raise SystemExit(
            f"No page files found in {source_dir}: nothing ingested or removed."
        )


async def main() -> None:
    pages = discover_pages()
    check_pages(pages)
    bodies, removals = await load_deduped_bodies(pages)
    async with AsyncSessionLocal() as db:
        totals = {"added": 0, "kept": 0, "removed": 0}

        for filename in pages:
            name, stats = await ingest_one(db, filename, bodies[filename])
            for key in totals:
                totals[key] += stats[key]
            print(
                f"{name}: {stats['status']} "
                f"(embedded {stats['added']}, kept {stats['kept']}, removed {stats['removed']})"
            )

        removed_docs = await remove_deleted_documents(db, pages)
        print(f"\nRemoved {len(removed_docs)} owned documents no longer in data/")
        for name in removed_docs:
            print(f"  - {name}")

        print(
            f"\nPages: {len(pages)}. Total: embedded {totals['added']} new chunks, "
            f"kept {totals['kept']}, removed {totals['removed']}"
        )
        await write_report(db, pages, removals)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
