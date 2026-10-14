"""Table-driven unit tests for canonical_source_name (Subtask 1.4.1)."""

import unicodedata

import pytest

from app.ingestion.naming import canonical_source_name


@pytest.mark.parametrize(
    "raw_input, expected_canonical",
    [
        ("A.DOCX", "A.md"),
        ("a.Md", "a.md"),
        ("document.markdown", "document.md"),
        ("DOCUMENT.MARKDOWN", "DOCUMENT.md"),
        ("dir/a.docx", "a.md"),
        ("deep/nested/folder/policy.md", "policy.md"),
        ("nested\\windows\\path\\report.docx", "report.md"),
        ("trailing_space.md  ", "trailing_space.md"),
        ("trailing_dots.docx....", "trailing_dots.md"),
        ("spaced stem name.docx", "spaced stem name.md"),
        ("accent_nfc_é.md", "accent_nfc_é.md"),
        (unicodedata.normalize("NFD", "accent_nfd_é.md"), "accent_nfd_é.md"),
        ("unicode_café_aust.docx", "unicode_café_aust.md"),
        ("hyphen-and_underscore.123.md", "hyphen-and_underscore.123.md"),
        ("version_1.0_final.docx", "version_1.0_final.md"),
    ],
)
def test_canonical_source_name_valid(raw_input, expected_canonical):
    result = canonical_source_name(raw_input)
    assert result == expected_canonical


@pytest.mark.parametrize(
    "invalid_input, error_match",
    [
        ("..\\a.md", "path traversal"),
        ("../a.md", "path traversal"),
        ("dir/../../secret.docx", "path traversal"),
        ("C:\\documents\\file.docx", "drive letter"),
        ("d:/documents/file.md", "drive letter"),
        ("unsupported.doc", "Unsupported file extension"),
        ("unsupported.docm", "Unsupported file extension"),
        ("script.sh", "Unsupported file extension"),
        ("executable.exe", "Unsupported file extension"),
        ("no_extension", "lacks a file extension"),
        (".md", "Filename stem cannot be empty"),
        (".docx", "Filename stem cannot be empty"),
        ("   .markdown", "Filename stem cannot be empty"),
        ("a.md.docx", "Prohibited double extension"),
        ("policy.docx.md", "Prohibited double extension"),
        ("contract.doc.md", "Prohibited double extension"),
        ("payload\x00.docx", "control characters"),
        ("test\x1b[31m.md", "control characters"),
        ("a" * 256 + ".docx", "exceeds maximum allowed length"),
        ("", "non-empty string"),
        (None, "non-empty string"),
    ],
)
def test_canonical_source_name_invalid(invalid_input, error_match):
    with pytest.raises(ValueError, match=error_match):
        canonical_source_name(invalid_input)
