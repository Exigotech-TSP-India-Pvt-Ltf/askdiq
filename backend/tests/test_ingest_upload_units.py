"""Upload link resolution (front-matter route), link keys and upload dedupe."""

from app.ingestion.quality import dedupe_upload, duplicate_warning
from app.knowledge.file_meta import FileLinkMeta, docx_link_meta, split_front_matter
from app.knowledge.ingest_links import (
    WARN_ROUTE_IGNORED,
    WARN_ROUTE_OVERRIDDEN,
    LinkSource,
    link_key,
    resolve_upload_link,
)

BASE = "https://deployiq.co"
SHARED = (
    "Our consultants review every Microsoft 365 control with your team in a workshop."
)


def test_front_matter_route_is_read() -> None:
    parsed = split_front_matter(
        "---\nurl: https://deployiq.co/privacy\nroute: /privacy\n"
        "description: How we protect data.\nsource:\n  - a.tsx\n---\n# P\nBody"
    )
    assert parsed.meta.route == "/privacy"
    assert parsed.meta.description == "How we protect data."
    assert parsed.body == "# P\nBody"


def test_route_resolves_to_site_page_and_wins_over_url() -> None:
    meta = FileLinkMeta(
        url="https://deployiq.co/old", route="/privacy", origin="front_matter"
    )
    link = resolve_upload_link(None, meta, [], site_base_url=BASE)
    assert link.url == "https://deployiq.co/privacy"
    assert link.source is LinkSource.FRONT_MATTER_ROUTE
    assert link.route == "/privacy"
    assert link.detected_url == link.url


def test_route_without_base_url_keeps_old_behaviour() -> None:
    meta = FileLinkMeta(url="https://a.com/x", route="/privacy", origin="front_matter")
    link = resolve_upload_link(None, meta, [])
    assert link.url == "https://a.com/x"
    assert link.source is LinkSource.FRONT_MATTER_URL
    assert link.route is None


def test_bad_route_is_a_warning() -> None:
    link = resolve_upload_link(
        None, FileLinkMeta(route="//evil.com/x"), [], site_base_url=BASE
    )
    assert link.url is None
    assert WARN_ROUTE_IGNORED in link.warnings


def test_form_overrides_route_and_drops_it() -> None:
    meta = FileLinkMeta(route="/privacy", origin="front_matter")
    link = resolve_upload_link("https://other.com/p", meta, [], site_base_url=BASE)
    assert (link.url, link.source, link.route) == (
        "https://other.com/p",
        LinkSource.FORM,
        None,
    )
    assert WARN_ROUTE_OVERRIDDEN in link.warnings
    same = resolve_upload_link(
        "https://deployiq.co/privacy/", meta, [], site_base_url=BASE
    )
    assert same.route == "/privacy"
    assert not same.warnings


def test_docx_link_source() -> None:
    assert docx_link_meta(b"not a zip").origin is None
    link = resolve_upload_link(
        None, FileLinkMeta(url="https://a.com/d", origin="docx"), []
    )
    assert link.source is LinkSource.DOCX


def test_link_key_ignores_case_slash_and_fragment() -> None:
    assert link_key("https://DeployIQ.co/privacy/#top") == link_key(
        "https://deployiq.co/privacy"
    )
    assert link_key("https://deployiq.co/") == link_key("https://deployiq.co")
    assert link_key("https://deployiq.co/a") != link_key("https://deployiq.co/b")
    assert link_key(None) is None


def test_dedupe_upload_drops_lines_a_more_specific_page_has() -> None:
    others = {"zero-trust.md": (f"# Zero trust\n\n{SHARED}\n", "/zero-trust")}
    text, removals = dedupe_upload(
        "home.md", f"# Home\n\n{SHARED}\n\nOwn.", "/", others
    )
    assert SHARED not in text
    assert "# Home" in text
    assert removals == [
        {"page": "home.md", "kept_on": "zero-trust.md", "text": SHARED.lower()[:120]}
    ]
    assert "zero-trust.md" in duplicate_warning(removals[0])


def test_dedupe_upload_keeps_lines_when_upload_is_more_specific() -> None:
    others = {"home.md": (f"# Home\n\n- {SHARED}\n", "/")}
    upload = f"# ZT\n\n- {SHARED}\n"
    text, removals = dedupe_upload("zero-trust.md", upload, "/zero-trust", others)
    assert text == upload
    assert removals == []


def test_dedupe_upload_ignores_its_own_stored_copy() -> None:
    others = {"home.md": (f"{SHARED}\n", "/zero-trust-long")}
    text, removals = dedupe_upload("home.md", SHARED, "/", others)
    assert (text, removals) == (SHARED, [])
