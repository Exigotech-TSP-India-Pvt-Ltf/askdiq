"""Which source link an upload stores (OR-7 ingestion rules).

Precedence: the form / JSON field > the file's own metadata > the stored
value. A new document needs a link while ``require_source_url`` is on. An
existing link is changed by a re-upload only with ``knowledge:links``
(superadmin); otherwise it is kept and the response says so. Filling a
missing link is not a change and needs no extra permission.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.knowledge.file_meta import FileLinkMeta
from app.knowledge.links import LinkError, LinkErrorCode, validate_source_url

WARN_LINK_KEPT = (
    "source_url_kept: this document already has a different source link; "
    "changing it needs the knowledge:links permission (Edit link on the "
    "Knowledge page)"
)
DETAIL_REQUIRED = (
    "A source URL is required for new documents (Settings > Knowledge links)."
)


class SourceUrlRequiredError(LinkError):
    def __init__(self) -> None:
        super().__init__(LinkErrorCode.REQUIRED, DETAIL_REQUIRED)


@dataclass(frozen=True, slots=True)
class UploadLink:
    """The validated link an upload asks for (``url=None``: keep / none)."""

    url: str | None = None
    section_links: bool | None = None
    title: str | None = None
    detected_url: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LinkDecision:
    url: str | None
    section_links: bool
    changed: bool
    warnings: list[str] = field(default_factory=list)


def resolve_upload_link(
    field_url: str | None,
    file_meta: FileLinkMeta,
    allowed_hosts: list[str],
) -> UploadLink:
    """Validate the field (``LinkError`` = 422) and the file's own link (warning)."""
    explicit = validate_source_url(field_url, allowed_hosts) if field_url else None
    detected: str | None = None
    warnings: list[str] = []
    if file_meta.url:
        try:
            detected = validate_source_url(file_meta.url, allowed_hosts)
        except LinkError as exc:
            warnings.append(f"source_url_ignored: the file's link was ignored ({exc})")
    return UploadLink(
        url=explicit or detected,
        section_links=file_meta.section_links,
        title=file_meta.title,
        detected_url=detected,
        warnings=tuple(warnings),
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
