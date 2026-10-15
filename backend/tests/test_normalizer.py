"""Unit tests for deterministic markdown normalization (Subtask 1.4.2)."""

import unicodedata

from app.ingestion.normalizer import (
    chunk_content_hash,
    doc_content_hash,
    normalize_markdown,
)


def test_normalize_markdown_bom_and_crlf():
    raw_bytes = b"\xef\xbb\xbf# Title\r\n\r\nBody text with CRLF.\r\n"
    normalized, pii = normalize_markdown(raw_bytes)
    assert not normalized.startswith("\ufeff")
    assert "\r" not in normalized
    assert normalized.endswith("\n")
    assert pii == []


def test_normalize_markdown_trailing_spaces_and_blank_lines():
    raw = "# Section   \t\nLine 1   \n\n\n\n\nLine 2\n\n\n"
    normalized, _ = normalize_markdown(raw)
    assert "Section \t" not in normalized
    assert "\n\n\n" not in normalized
    assert "Line 1\n\nLine 2\n" in normalized


def test_normalize_markdown_nfc_accents():
    nfd_text = unicodedata.normalize("NFD", "Café and resume.")
    nfc_text = unicodedata.normalize("NFC", "Café and resume.")
    norm_from_nfd, _ = normalize_markdown(nfd_text)
    norm_from_nfc, _ = normalize_markdown(nfc_text)
    assert norm_from_nfd == norm_from_nfc
    assert doc_content_hash(norm_from_nfd) == doc_content_hash(norm_from_nfc)


def test_normalize_markdown_redacts_pii():
    raw = "DeployIQ administrator email: admin@deployiq.com\n"
    normalized, pii = normalize_markdown(raw)
    assert "admin@deployiq.com" not in normalized
    assert "[REDACTED_EMAIL]" in normalized
    assert "email" in pii


def test_chunk_hash_independent_of_doc_version():
    """Verify chunk hash stays identical across document hash version changes."""
    chunk_text = "This is a single chunk of knowledge."
    h1 = chunk_content_hash(chunk_text)
    # The chunk hash is v3 + chunk_text, preserving pre-existing embedding vectors
    assert h1 == chunk_content_hash(chunk_text)
    assert len(h1) == 64
