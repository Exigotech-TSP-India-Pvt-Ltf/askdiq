"""Tests for server-side docx conversion and upload security controls (Subtasks 1.4.3 & 1.4.4)."""

import io
import zipfile

import pytest

from app.ingestion.docx_converter import (
    _HTMLToMarkdown,
    convert_docx,
    validate_docx_bytes,
)


def _create_minimal_docx(
    document_xml: str,
    extra_files: dict[str, bytes] | None = None,
    encrypted: bool = False,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Standard content types
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            "</Types>",
        )
        # Document XML
        zf.writestr("word/document.xml", document_xml)

        if extra_files:
            for name, content in extra_files.items():
                zf.writestr(name, content)

    data = buf.getvalue()
    if encrypted:
        # Corrupt flag to simulate encrypted entry
        # Standard ZIP entry flag bit 0 indicates encryption
        pass
    return data


def test_html_to_markdown_converter_headings_and_formatting():
    parser = _HTMLToMarkdown()
    html = (
        "<h1>Main Title</h1>"
        "<p>This is <strong>bold</strong> and <em>italic</em>.</p>"
        "<h2>Subtitle</h2>"
        "<p>Inline <code>code</code> and <a href='https://example.com'>safe link</a>.</p>"
    )
    parser.feed(html)
    md = parser.get_markdown()

    assert "# Main Title" in md
    assert "**bold**" in md
    assert "*italic*" in md
    assert "## Subtitle" in md
    assert "`code`" in md
    assert "[safe link](https://example.com)" in md


def test_html_to_markdown_converter_gfm_tables():
    parser = _HTMLToMarkdown()
    html = (
        "<table>"
        "<tr><th>Framework</th><th>Compliance</th></tr>"
        "<tr><td>Essential Eight</td><td>ML3</td></tr>"
        "<tr><td>SOC 2</td><td>Type II</td></tr>"
        "</table>"
    )
    parser.feed(html)
    md = parser.get_markdown()

    assert "| Framework | Compliance |" in md
    assert "| --- | --- |" in md
    assert "| Essential Eight | ML3 |" in md
    assert "| SOC 2 | Type II |" in md


def test_html_to_markdown_converter_sanitizes_unsafe_links():
    parser = _HTMLToMarkdown()
    html = (
        "<p><a href='javascript:alert(1)'>Click Me</a></p>"
        "<p><a href='data:text/html;base64,PHNjcmlwdD5...'>Payload</a></p>"
    )
    parser.feed(html)
    md = parser.get_markdown()

    # Dangerous links stripped
    assert "javascript:" not in md
    assert "data:" not in md
    assert "Click Me" in md
    assert "Payload" in md
    assert len(parser.warnings) >= 2


def test_docx_security_missing_zip_magic():
    with pytest.raises(ValueError, match="missing ZIP archive signature"):
        validate_docx_bytes(b"NOT_A_ZIP_FILE_AT_ALL")


def test_docx_security_missing_required_files():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("some_file.txt", "hello")
    with pytest.raises(ValueError, match=r"missing document\.xml"):
        validate_docx_bytes(buf.getvalue())


def test_docx_security_rejects_macro_enabled():
    bad_docx = _create_minimal_docx(
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/></w:body></w:document>',
        extra_files={"word/vbaProject.bin": b"\x00\x01\x02"},
    )
    with pytest.raises(ValueError, match="Macro-enabled documents"):
        validate_docx_bytes(bad_docx)


def test_docx_security_zip_bomb_too_many_entries():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<xml/>")
        zf.writestr("word/document.xml", "<xml/>")
        for i in range(1005):
            zf.writestr(f"word/entry_{i}.xml", "<test/>")
    with pytest.raises(ValueError, match="ZIP bomb"):
        validate_docx_bytes(buf.getvalue())


@pytest.mark.asyncio
async def test_convert_docx_valid_content():
    valid_doc_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>DeployIQ Security Architecture</w:t></w:r></w:p>"
        "</w:body>"
        "</w:document>"
    )
    docx_bytes = _create_minimal_docx(valid_doc_xml)
    result = await convert_docx(docx_bytes)

    assert "DeployIQ Security Architecture" in result.markdown
    assert result.quarantined is False
