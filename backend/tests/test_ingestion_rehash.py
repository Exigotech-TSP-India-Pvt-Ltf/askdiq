"""Re-ingesting an unchanged .docx is stable and states why anything changed.

Regression for a .docx re-upload reported as "3 chunks changed": the stored
document came from a hand-written .md under an older hash scheme, so the
change was real, but the response gave no reason and the audit row recorded
the new hash as the previous one.
"""

import hashlib
import io
import zipfile

import pytest
from sqlalchemy import select, update

from app.chunking.factory import get_strategy
from app.ingestion.docx_converter import convert_docx
from app.ingestion.normalizer import (
    chunk_content_hash,
    doc_content_hash,
    normalize_markdown,
)
from app.ingestion.service import (
    REASON_CHUNKING,
    REASON_CONTENT,
    REASON_HASH_SCHEME,
    REASON_SOURCE_FORMAT,
    ingest_text,
)
from app.models.db_models import AuditLog, Chunk, Document

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _para(text: str, *, bold: bool = False) -> str:
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f"<w:p><w:r>{rpr}<w:t>{text}</w:t></w:r></w:p>"


def _docx(paragraphs: list[str]) -> bytes:
    body = "".join(paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


DOCX = _docx(
    [
        _para("Microsoft 365 Security Baseline Assessment", bold=True),
        _para("42 controls · 5 focus areas · 100% your approval"),
        _para("We evaluate your tenant across five areas that matter most."),
        _para("“Smart quotes” and an en dash – stay stable."),  # noqa: RUF001
    ]
)


async def _canonical_and_chunks(data: bytes) -> tuple[str, list[str]]:
    converted = await convert_docx(data)
    canonical, _ = normalize_markdown(converted.markdown)
    chunks = await get_strategy("auto", "markdown").chunk(canonical, {})
    return canonical, [chunk_content_hash(c.content) for c in chunks]


@pytest.mark.asyncio
async def test_same_docx_bytes_give_same_canonical_and_chunk_hashes():
    first, first_chunks = await _canonical_and_chunks(DOCX)
    second, second_chunks = await _canonical_and_chunks(bytes(DOCX))
    assert first == second
    assert doc_content_hash(first) == doc_content_hash(second)
    assert first_chunks == second_chunks
    # Normalization is idempotent: re-normalizing stored text keeps the hash.
    assert normalize_markdown(first)[0] == first


async def _ingest_docx(db_session, name: str, data: bytes = DOCX) -> dict:
    converted = await convert_docx(data)
    return await ingest_text(
        db_session,
        source_name=name,
        content=converted.markdown,
        original_filename=name,
        converted_from="docx",
        original_sha256=hashlib.sha256(data).hexdigest(),
        original_size_bytes=len(data),
        warnings=converted.warnings,
        actor="rehash@example.com",
    )


@pytest.mark.asyncio
async def test_reingest_unchanged_docx_changes_nothing(db_session, fake_embed):
    r1 = await _ingest_docx(db_session, "rehash.docx")
    assert r1["status"] == "new"
    embeds = len(fake_embed["calls"])
    chunk_hashes = set(
        (
            await db_session.scalars(
                select(Chunk.content_hash).where(Chunk.document_id == r1["document"].id)
            )
        ).all()
    )

    r2 = await _ingest_docx(db_session, "rehash.docx")
    assert r2["status"] == "unchanged"
    assert r2["content_hash"] == r1["content_hash"]
    assert (r2["chunks_added"], r2["chunks_removed"]) == (0, 0)
    assert r2["change_reasons"] == []
    assert len(fake_embed["calls"]) == embeds
    after = set(
        (
            await db_session.scalars(
                select(Chunk.content_hash).where(Chunk.document_id == r1["document"].id)
            )
        ).all()
    )
    assert after == chunk_hashes


async def _make_legacy(db_session, doc: Document, *, keep_text: bool) -> str:
    """Rewrite a row as a pre-pipeline ingest: old hash, no versions."""
    legacy_hash = hashlib.sha256(b"legacy").hexdigest()
    values: dict = {"content_hash": legacy_hash, "doc_metadata": {}}
    if not keep_text:
        values["canonical_markdown"] = None
    await db_session.execute(
        update(Document).where(Document.id == doc.id).values(**values)
    )
    await db_session.flush()
    await db_session.refresh(doc)
    return legacy_hash


@pytest.mark.asyncio
async def test_hash_scheme_upgrade_alone_reembeds_nothing(db_session, fake_embed):
    r1 = await ingest_text(
        db_session, source_name="scheme.md", content="# Scheme\n\nSame text."
    )
    legacy = await _make_legacy(db_session, r1["document"], keep_text=True)
    embeds = len(fake_embed["calls"])

    r2 = await ingest_text(
        db_session, source_name="scheme.md", content="# Scheme\n\nSame text."
    )
    assert r2["status"] == "updated"
    assert r2["change_reasons"] == [REASON_HASH_SCHEME]
    assert (r2["chunks_added"], r2["chunks_removed"]) == (0, 0)
    assert r2["chunks_kept"] == r1["chunk_count"]
    assert r2["previous_content_hash"] == legacy
    assert len(fake_embed["calls"]) == embeds


@pytest.mark.asyncio
async def test_chunk_change_on_same_text_is_reported_as_chunking(
    db_session, fake_embed
):
    content = "# Chunking\n\nSame text, different chunk set."
    r1 = await ingest_text(db_session, source_name="chunking.md", content=content)
    await _make_legacy(db_session, r1["document"], keep_text=True)
    await db_session.execute(
        update(Chunk)
        .where(Chunk.document_id == r1["document"].id)
        .values(content_hash="0" * 64)
    )
    await db_session.flush()

    r2 = await ingest_text(db_session, source_name="chunking.md", content=content)
    assert REASON_CHUNKING in r2["change_reasons"]
    assert REASON_CONTENT not in r2["change_reasons"]


@pytest.mark.asyncio
async def test_legacy_md_then_docx_upload_states_reasons_and_previous_hash(
    db_session, fake_embed
):
    """The owner's case: a hand-written .md, later replaced by the .docx."""
    r1 = await ingest_text(
        db_session,
        source_name="seo.md",
        content="# SEO\n\n## 1. Baseline\n\nHand-written Markdown version.",
    )
    legacy = await _make_legacy(db_session, r1["document"], keep_text=False)

    r2 = await _ingest_docx(db_session, "seo.docx")
    assert r2["status"] == "updated"
    assert r2["change_reasons"] == [
        REASON_SOURCE_FORMAT,
        REASON_HASH_SCHEME,
        REASON_CONTENT,
    ]
    assert r2["previous_content_hash"] == legacy
    assert r2["previous_content_hash"] != r2["content_hash"]

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "ingest", AuditLog.actor == "rehash@example.com"
        )
    )
    assert audit.detail["previous_content_hash"] == legacy[:12]
    assert audit.detail["content_hash"] == r2["content_hash"][:12]
    assert audit.detail["change_reasons"] == r2["change_reasons"]
    doc = await db_session.get(Document, r1["document"].id)
    assert doc.doc_metadata["previous_content_hash"] == legacy
