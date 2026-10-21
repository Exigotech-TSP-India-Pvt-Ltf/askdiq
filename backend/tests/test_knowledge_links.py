"""OR-7 knowledge source links: pure tests (no database).

Validator, allow-list, labels, anchors, file link metadata (front matter and
docx), the re-upload link rules and the citation post-processor (including
100 KB adversarial inputs for the linear-time marker regex).
"""

from __future__ import annotations

import io
import time
import zipfile

import pytest
from pydantic import ValidationError

from app.harness.citations import (
    MARKER_RE,
    apply_citations,
    citation_records,
    strip_markers,
)
from app.harness.prompts import _format_context, _format_history, get_system_prompt
from app.knowledge.file_meta import (
    FRONT_MATTER_MAX_BYTES,
    WARN_FRONT_MATTER_INVALID,
    WARN_FRONT_MATTER_TOO_LARGE,
    FileLinkMeta,
    docx_link_meta,
    split_front_matter,
)
from app.knowledge.ingest_links import (
    WARN_LINK_KEPT,
    UploadLink,
    decide_link,
    resolve_upload_link,
)
from app.knowledge.links import (
    LinkError,
    LinkErrorCode,
    LinkStatus,
    brand_label,
    chunk_anchor,
    citation_url,
    host_allowed,
    link_status,
    normalize_host_pattern,
    slugify_anchor,
    validate_source_url,
)
from app.schemas.knowledge import KnowledgeSettingsUpdate
from app.schemas.query import ChatTurn, SourceChunk

ADVERSARIAL = 100_000
FAST_SECONDS = 1.0

# --- validator --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://docs.example.com/a?b=1#c", "https://docs.example.com/a?b=1#c"),
        ("  https://Example.COM/Path  ", "https://example.com/Path"),
        ("HTTPS://example.com", "https://example.com"),
        ("https://example.com:8443/x", "https://example.com:8443/x"),
        ("https://bücher.example/", "https://xn--bcher-kva.example/"),
        ("https://[2001:db8::1]/", "https://[2001:db8::1]/"),
    ],
)
def test_validator_accepts_https(raw: str, expected: str) -> None:
    assert validate_source_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "http://example.com",
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox",
        "file:///etc/passwd",
        "//example.com/x",
        "https://user:pass@example.com/",
        "https://user@example.com/",
        "https://exa mple.com/",
        "https://example.com/a b",
        "https://example.com/\x00",
        "https://example.com/\u200b",
        "https://example.com\\@evil.com/",
        "https:///nohost",
        "https://exa_mple.com/",
        "https://-bad.example.com/",
        "https://example.com:99999/",
        "https://" + "a" * 2048 + ".com",
        "https://example.com/" + "a" * 2040,
    ],
)
def test_validator_rejects(raw: str) -> None:
    with pytest.raises(LinkError) as exc:
        validate_source_url(raw)
    assert exc.value.code is LinkErrorCode.INVALID


def test_allow_list_empty_exact_and_wildcard() -> None:
    assert host_allowed("anything.io", [])
    assert host_allowed("docs.example.com", ["docs.example.com"])
    assert not host_allowed("evil.com", ["docs.example.com"])
    assert host_allowed("a.b.example.com", ["*.example.com"])
    assert not host_allowed("example.com", ["*.example.com"])
    assert not host_allowed("badexample.com", ["*.example.com"])
    with pytest.raises(LinkError) as exc:
        validate_source_url("https://evil.com/", ["*.example.com"])
    assert exc.value.code is LinkErrorCode.NOT_ALLOWED
    assert validate_source_url("https://x.example.com/", ["*.example.com"])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Docs.Example.com", "docs.example.com"),
        ("*.example.com", "*.example.com"),
        (" bücher.example ", "xn--bcher-kva.example"),
    ],
)
def test_host_patterns_normalize(raw: str, expected: str) -> None:
    assert normalize_host_pattern(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "*",
        "*.com",
        "a.*.example.com",
        "example.*",
        "https://x.com",
        "x.com/p",
        "user@x.com",
        "x.com:443",
        "a b.com",
        "x" * 254,
    ],
)
def test_host_patterns_reject(raw: str) -> None:
    with pytest.raises(LinkError):
        normalize_host_pattern(raw)


def test_settings_update_normalizes_dedupes_and_caps() -> None:
    update = KnowledgeSettingsUpdate(
        allowed_hosts=["Example.com", "example.com", "*.Docs.io"],
        require_source_url=True,
        version=0,
    )
    assert update.allowed_hosts == ["example.com", "*.docs.io"]
    with pytest.raises(ValidationError):
        KnowledgeSettingsUpdate(
            allowed_hosts=["*.com"], require_source_url=False, version=0
        )
    with pytest.raises(ValidationError):
        KnowledgeSettingsUpdate(
            allowed_hosts=[f"h{i}.example.com" for i in range(51)],
            require_source_url=False,
            version=0,
        )


def test_link_status() -> None:
    assert link_status(None, []) is LinkStatus.MISSING
    assert link_status("https://a.com/x", []) is LinkStatus.OK
    assert link_status("https://a.com/x", ["b.com"]) is LinkStatus.NOT_ALLOWED


def test_validator_is_fast_on_long_hostile_input() -> None:
    hostile = "https://" + "a." * (ADVERSARIAL // 2)
    started = time.perf_counter()
    with pytest.raises(LinkError):
        validate_source_url(hostile)
    assert time.perf_counter() - started < FAST_SECONDS


# --- labels and anchors -----------------------------------------------------


@pytest.mark.parametrize(
    ("host", "name", "label"),
    [
        ("docs.deployiq.com.au", "x.md", "deployiq"),
        ("github.com", "x.md", "github"),
        ("www.x.co.uk", "x.md", "x"),
        ("learn.microsoft.com", "x.md", "microsoft"),
        ("10.0.0.1", "Onboarding guide.md", "Onboarding guide"),
        ("2001:db8::1", "a.md", "a"),
        (None, "very-long-document-name-here.docx", "very long docum…"),
        ("localhost", "x.md", "localhost"),
    ],
)
def test_brand_label(host: str | None, name: str, label: str) -> None:
    assert brand_label(host, name) == label


def test_anchors_slug_and_explicit_id() -> None:
    assert slugify_anchor("Key Features & Pricing") == "key-features--pricing"
    assert slugify_anchor("Set up SSO {#sso-setup}") == "sso-setup"
    assert slugify_anchor("") is None
    assert chunk_anchor({"anchor": "stored"}) == "stored"
    assert chunk_anchor({"section_heading": "Getting Started"}) == "getting-started"
    assert chunk_anchor({}) is None


def test_citation_url_document_wide_or_section() -> None:
    meta = {"section_heading": "Getting Started"}
    url = "https://docs.x.com/page#old"
    assert (
        citation_url(url, section_links=False, metadata=meta, allowed_hosts=[]) == url
    )
    assert (
        citation_url(url, section_links=True, metadata=meta, allowed_hosts=[])
        == "https://docs.x.com/page#getting-started"
    )
    assert (
        citation_url(url, section_links=True, metadata=meta, allowed_hosts=["y.com"])
        is None
    )
    assert (
        citation_url(None, section_links=True, metadata=meta, allowed_hosts=[]) is None
    )


# --- front matter -----------------------------------------------------------


def test_front_matter_is_read_and_stripped() -> None:
    text = (
        "---\nurl: https://docs.x.com/a\ntitle: Guide\nsection_links: true\n---\n"
        "# Title\nBody"
    )
    parsed = split_front_matter(text)
    assert parsed.body == "# Title\nBody"
    assert parsed.meta == FileLinkMeta(
        url="https://docs.x.com/a", title="Guide", section_links=True
    )
    assert parsed.warnings == ()


def test_front_matter_source_url_key_and_crlf() -> None:
    parsed = split_front_matter("---\r\nsource_url: https://a.com\r\n---\r\nBody")
    assert parsed.meta.url == "https://a.com"
    assert parsed.body == "Body"


def test_front_matter_non_scalar_keys_are_ignored() -> None:
    parsed = split_front_matter(
        "---\nurl: [https://a.com]\ntitle: {x: 1}\nsection_links: 'yes'\n---\nB"
    )
    assert parsed.meta == FileLinkMeta()
    assert parsed.body == "B"


def test_front_matter_malformed_is_ignored_with_warning() -> None:
    text = "---\nurl: [unclosed\n---\nBody"
    parsed = split_front_matter(text)
    assert parsed.body == text
    assert parsed.warnings == (WARN_FRONT_MATTER_INVALID,)


def test_front_matter_oversize_is_ignored_with_warning() -> None:
    text = "---\n" + "k: v\n" * (FRONT_MATTER_MAX_BYTES // 4) + "---\nBody"
    parsed = split_front_matter(text)
    assert parsed.body == text
    assert parsed.warnings == (WARN_FRONT_MATTER_TOO_LARGE,)


def test_non_mapping_or_absent_front_matter_is_content() -> None:
    for text in ("# Just markdown", "---\nA paragraph between rules\n---\nMore", "---"):
        assert split_front_matter(text).body == text


def test_front_matter_never_constructs_python_objects() -> None:
    parsed = split_front_matter("---\nurl: !!python/object/apply:os.system [x]\n---\nB")
    assert parsed.warnings == (WARN_FRONT_MATTER_INVALID,)
    assert parsed.meta.url is None


def test_front_matter_scan_is_linear() -> None:
    started = time.perf_counter()
    split_front_matter("---\n" + "\n" * ADVERSARIAL)
    split_front_matter("---\n" + "x" * ADVERSARIAL)
    assert time.perf_counter() - started < FAST_SECONDS


# --- docx -------------------------------------------------------------------


def _docx(core: str | None = None, custom: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")
        if core is not None:
            zf.writestr("docProps/core.xml", core)
        if custom is not None:
            zf.writestr("docProps/custom.xml", custom)
    return buffer.getvalue()


_CORE = (
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
    'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:title>Guide</dc:title><dc:identifier>{}</dc:identifier>"
    "</cp:coreProperties>"
)
_CUSTOM = (
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
    'custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/'
    '2006/docPropsVTypes"><property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" '
    'pid="2" name="SourceURL"><vt:lpwstr>{}</vt:lpwstr></property></Properties>'
)


def test_docx_core_identifier() -> None:
    meta = docx_link_meta(_docx(core=_CORE.format("https://docs.x.com/g")))
    assert meta == FileLinkMeta(url="https://docs.x.com/g", title="Guide")


def test_docx_custom_property_fallback() -> None:
    meta = docx_link_meta(
        _docx(core=_CORE.format("ISBN-123"), custom=_CUSTOM.format("https://a.com/p"))
    )
    assert meta.url == "https://a.com/p"


def test_docx_without_metadata_or_broken_is_empty() -> None:
    assert docx_link_meta(_docx()) == FileLinkMeta()
    assert docx_link_meta(b"not a zip") == FileLinkMeta()
    assert docx_link_meta(_docx(core="<broken")).url is None


def test_docx_entity_expansion_is_refused() -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
        '<cp:coreProperties xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:cp="x"><dc:identifier>&a;</dc:identifier></cp:coreProperties>'
    )
    assert docx_link_meta(_docx(core=bomb)).url is None


# --- upload link rules -------------------------------------------------------


def test_resolve_upload_link_precedence_and_warnings() -> None:
    meta = FileLinkMeta(url="https://file.com/a")
    assert resolve_upload_link("https://form.com/b", meta, []).url == (
        "https://form.com/b"
    )
    from_file = resolve_upload_link(None, meta, [])
    assert from_file.url == from_file.detected_url == "https://file.com/a"
    bad_file = resolve_upload_link(None, FileLinkMeta(url="http://x.com"), [])
    assert bad_file.url is None
    assert bad_file.warnings and bad_file.warnings[0].startswith("source_url_ignored")
    with pytest.raises(LinkError):
        resolve_upload_link("javascript:alert(1)", meta, [])
    with pytest.raises(LinkError) as exc:
        resolve_upload_link("https://other.com", FileLinkMeta(), ["a.com"])
    assert exc.value.code is LinkErrorCode.NOT_ALLOWED


def test_reupload_keeps_link_without_permission() -> None:
    requested = UploadLink(url="https://new.com")
    kept = decide_link(
        stored_url="https://old.com",
        stored_section_links=False,
        requested=requested,
        can_change=False,
    )
    assert kept.url == "https://old.com"
    assert not kept.changed
    assert kept.warnings == [WARN_LINK_KEPT]
    changed = decide_link(
        stored_url="https://old.com",
        stored_section_links=False,
        requested=requested,
        can_change=True,
    )
    assert changed.url == "https://new.com"
    assert changed.changed


def test_reupload_fills_a_missing_link_and_keeps_without_one() -> None:
    filled = decide_link(
        stored_url=None,
        stored_section_links=False,
        requested=UploadLink(url="https://new.com", section_links=True),
        can_change=False,
    )
    assert (filled.url, filled.section_links, filled.changed) == (
        "https://new.com",
        True,
        True,
    )
    kept = decide_link(
        stored_url="https://old.com",
        stored_section_links=True,
        requested=UploadLink(),
        can_change=False,
    )
    assert (kept.url, kept.section_links, kept.changed) == (
        "https://old.com",
        True,
        False,
    )


# --- citations --------------------------------------------------------------


def _source(n: int, doc: str, url: str | None = "https://docs.deployiq.com.au/p"):
    return SourceChunk(
        chunk_id=f"c{n}",
        document_id=doc,
        content=f"content {n}",
        score=0.9,
        metadata={"source_name": f"{doc}.md", "section_heading": f"Section {n}"},
        url=url,
    )


def test_apply_citations_maps_numbers_to_retrieved_sources() -> None:
    sources = [_source(1, "a"), _source(2, "b", url=None), _source(3, "a")]
    answer, citations = apply_citations(
        "First claim [1]. Second [2] [3]. Third [1][3]. Bad [7]. Zero [0].", sources
    )
    assert answer == "First claim [1]. Second [2][3]. Third [1]. Bad. Zero."
    assert [c.n for c in citations] == [1, 2, 3]
    first = citations[0]
    assert first.label == "deployiq"
    assert first.url == "https://docs.deployiq.com.au/p"
    assert first.host == "docs.deployiq.com.au"
    assert first.anchor == "section-1"
    assert citations[1].url is None
    assert citations[1].label == "b"
    assert "anchor" not in first.model_dump()
    assert citation_records(citations)[0]["anchor"] == "section-1"


def test_apply_citations_never_links_urls_from_the_answer() -> None:
    answer, citations = apply_citations(
        "See https://evil.example/x [1] and [evil](https://evil.example).",
        [_source(1, "a", url=None)],
    )
    assert citations[0].url is None
    assert "https://evil.example" in answer  # text stays text; never a citation


def test_apply_citations_without_markers_is_unchanged() -> None:
    assert apply_citations("No markers here.", [_source(1, "a")]) == (
        "No markers here.",
        [],
    )


def test_strip_markers_for_groundedness_word_counts_and_history() -> None:
    assert strip_markers("Claim [1]. Other [2][3] text.") == "Claim. Other text."
    assert strip_markers("[1] Lead") == " Lead"
    turn = ChatTurn(role="assistant", content="It is X [1].")
    history = _format_history("what about its price?", [turn])
    assert "[1]" not in history
    assert "It is X." in history


def test_context_headers_are_numbered_and_prompt_asks_for_markers() -> None:
    context = _format_context([_source(1, "a"), "plain", _source(3, "b")])
    assert "[Source 1 | Document: a.md | Section: Section 1]" in context
    assert "[Source 3 | Document: b.md | Section: Section 3]" in context
    assert "Never write URLs" in get_system_prompt()


@pytest.mark.parametrize(
    "hostile",
    [
        "[" * ADVERSARIAL,
        "[1" * (ADVERSARIAL // 2),
        "[12]" * (ADVERSARIAL // 4),
        "[1] " * (ADVERSARIAL // 4),
        " " * ADVERSARIAL + "[1]",
        "[123]" * (ADVERSARIAL // 5),
    ],
    ids=["brackets", "open-digit", "markers", "spaced-markers", "spaces", "3-digit"],
)
def test_marker_processing_is_linear_on_100kb(hostile: str) -> None:
    sources = [_source(1, "a")]
    started = time.perf_counter()
    strip_markers(hostile)
    apply_citations(hostile, sources)
    list(MARKER_RE.finditer(hostile))
    assert time.perf_counter() - started < FAST_SECONDS
