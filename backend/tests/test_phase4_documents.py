"""Comprehensive Integration Test Suite for Phase 4 (Canonical .md Documents & Provenance)."""

import pytest
from httpx import AsyncClient

from app.ingestion.naming import canonical_source_name
from app.schemas.document import DocumentSummary
from scripts.ingest_real_docs import ingest_one


def test_canonical_source_name_properties():
    """Verify canonical naming rules for markdown files."""
    assert canonical_source_name("test-doc.md") == "test-doc.md"
    assert canonical_source_name("test-doc.docx") == "test-doc.md"
    assert canonical_source_name("folder/sub/doc.markdown") == "doc.md"


def test_document_summary_defaults_and_normalization():
    """Task 4.1.1: DocumentSummary defaults active_format to MARKDOWN and normalizes legacy formats."""
    doc = DocumentSummary(
        document_id="123e4567-e89b-12d3-a456-426614174000",
        source_name="home-page.md",
        source_type="markdown",
        chunk_count=5,
        created_at="2026-01-01T00:00:00Z",
        original_format="docx",
        converted_from="docx",
        original_filename="home-page.docx",
        content_hash="a1b2c3d4e5f6",
        original_sha256="a1b2c3d4e5f678901234567890abcdef1234567890abcdef1234567890abcdef",
    )
    data = doc.model_dump()
    assert data["active_format"] == "MARKDOWN"
    assert data["converted_from"] == "docx"
    assert data["original_filename"] == "home-page.docx"
    assert data["content_hash"] == "a1b2c3d4e5f6"
    assert data["original_sha256"] is not None


@pytest.mark.asyncio
async def test_get_documents_endpoint_provenance(client: AsyncClient):
    """Task 4.1: GET /documents returns active_format='MARKDOWN' and proper provenance."""
    res = await client.get("/documents")
    assert res.status_code == 200
    docs = res.json()
    assert isinstance(docs, list)

    for d in docs:
        # Every document must present active_format as MARKDOWN
        assert d.get("active_format") == "MARKDOWN"
        assert d.get("converted_from") in ("docx", "md", None)
        assert d.get("original_filename") is not None
        if d.get("content_hash"):
            assert len(d["content_hash"]) <= 12


@pytest.mark.asyncio
async def test_cli_refuses_non_markdown():
    """Task 4.2.2: CLI refuses non-.md names with clear message."""
    with pytest.raises(ValueError, match=r"CLI ingestion refuses non-\.md file"):
        await ingest_one(None, "unsupported.docx")

    with pytest.raises(ValueError, match=r"CLI ingestion refuses non-\.md file"):
        await ingest_one(None, "document.pdf")
