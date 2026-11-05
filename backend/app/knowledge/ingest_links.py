"""Which source link an upload stores (OR-7 ingestion rules).

Precedence: the form / JSON field > the file's own metadata (.md front
matter ``route`` = SITE_BASE_URL + route, as for the website pages, then
``url`` / ``source_url``; .docx properties) > the stored value. A new
document needs a link while ``require_source_url`` is on. An existing link
is changed by a re-upload only with ``knowledge:links`` (superadmin);
otherwise it is kept and the response says so. Filling a missing link is not
a change and needs no extra permission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from app.knowledge.file_meta import ORIGIN_DOCX, FileLinkMeta
from app.knowledge.links import (
    LinkError,
    LinkErrorCode,
    site_page_url,
    validate_source_url,
)

WARN_LINK_KEPT = (
    "source_url_kept: this document already has a different source link; "
    "changing it needs the knowledge:links permission (Edit link on the "
    "Knowledge page)"
)
WARN_ROUTE_IGNORED = (
    "route_ignored: the file's route is not a valid site path and was ignored"
)
WARN_ROUTE_OVERRIDDEN = (
    "route_overridden: the source URL field replaces the file's route link"
)
DETAIL_REQUIRED = (
    "A source URL is required for new documents (Settings > Knowledge links)."
)


class SourceUrlRequiredError(LinkError):
    def __init__(self) -> None:
        super().__init__(LinkErrorCode.REQUIRED, DETAIL_REQUIRED)


class LinkSource(StrEnum):
    """Where the link an upload stores comes from (shown in the preview)."""

    FORM = "form"
    FRONT_MATTER_URL = "front_matter_url"
    FRONT_MATTER_ROUTE = "front_matter_route"
    DOCX = "docx"
    STORED = "stored"


@dataclass(frozen=True, slots=True)
class UploadLink:  # pylint: disable=too-many-instance-attributes  # one value object
    """The validated link an upload asks for (``url=None``: keep / none).

    ``route`` is set only when ``url`` is the route's site page, so it can be
    stored as page metadata (citations prefer it) without contradicting
    ``url``.
    """

    url: str | None = None
    section_links: bool | None = None
    title: str | None = None
    detected_url: str | None = None
    warnings: tuple[str, ...] = ()
    source: LinkSource | None = None
    route: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class LinkDecision:
    url: str | None
    section_links: bool
    changed: bool
    warnings: list[str] = field(default_factory=list)


def link_key(url: str | None) -> str | None:
    """Comparison key of a link: one page = one key (case of scheme/host,
    a trailing ``/`` and the ``#fragment`` do not matter)."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    path = parts.path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


def _file_link(
    file_meta: FileLinkMeta, allowed_hosts: list[str], warnings: list[str]
) -> tuple[str | None, LinkSource | None]:
    """The file's own ``url`` (validated; a bad one is a warning)."""
    if not file_meta.url:
        return None, None
    try:
        url = validate_source_url(file_meta.url, allowed_hosts)
    except LinkError as exc:
        warnings.append(f"source_url_ignored: the file's link was ignored ({exc})")
        return None, None
    source = (
        LinkSource.DOCX
        if file_meta.origin == ORIGIN_DOCX
        else LinkSource.FRONT_MATTER_URL
    )
    return url, source


def resolve_upload_link(
    field_url: str | None,
    file_meta: FileLinkMeta,
    allowed_hosts: list[str],
    site_base_url: str | None = None,
) -> UploadLink:
    """Validate the field (``LinkError`` = 422) and the file's own link (warning).

    With ``site_base_url``, a front-matter ``route`` resolves to the site page
    (SITE_BASE_URL + route, like ``scripts/ingest_real_docs.py``) and wins
    over the file's ``url``; the configured site is trusted (no allow-list),
    as for the website pages.
    """
    explicit = validate_source_url(field_url, allowed_hosts) if field_url else None
    warnings: list[str] = []
    detected, source = _file_link(file_meta, allowed_hosts, warnings)
    route_url = None
    if file_meta.route and site_base_url:
        route_url = site_page_url(site_base_url, file_meta.route)
        if route_url is None:
            warnings.append(WARN_ROUTE_IGNORED)
        else:
            detected, source = route_url, LinkSource.FRONT_MATTER_ROUTE
    route = file_meta.route if route_url else None
    if explicit:
        if route and link_key(explicit) != link_key(route_url):
            warnings.append(WARN_ROUTE_OVERRIDDEN)
            route = None
        source = LinkSource.FORM
    return UploadLink(
        url=explicit or detected,
        section_links=file_meta.section_links,
        title=file_meta.title,
        detected_url=detected,
        warnings=tuple(warnings),
        source=source if (explicit or detected) else None,
        route=route,
        description=file_meta.description,
    )


def decide_link(
    *,
    stored_url: str | None,
    stored_section_links: bool,
    requested: UploadLink,
    can_change: bool,
) -> LinkDecision:
    """The link to store on an existing document after a re-upload."""
    url, section_links, warnings = stored_url, stored_section_links, []
    if requested.url and requested.url != stored_url:
        if stored_url is None or can_change:
            url = requested.url
        else:
            warnings.append(WARN_LINK_KEPT)
    if (
        requested.section_links is not None
        and requested.section_links != stored_section_links
        and (stored_url is None or can_change)
    ):
        section_links = requested.section_links
    changed = (url, section_links) != (stored_url, stored_section_links)
    return LinkDecision(url, section_links, changed, warnings)
