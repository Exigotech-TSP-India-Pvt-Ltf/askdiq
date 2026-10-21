"""Knowledge source URLs: the one shared validator, host allow-list and labels.

Every URL stored on a document passes :func:`validate_source_url` (https
only, a real host, no userinfo, no whitespace or control characters, at most
2048 characters, IDNA-encoded host, then the Settings > Knowledge links
allow-list). The server **never fetches** these URLs (no previews,
reachability checks or favicons), so there is no SSRF surface.

Parsing uses :func:`urllib.parse.urlsplit` and plain string operations; no
regular expression ever runs on user input here.
"""

from __future__ import annotations

import ipaddress
import unicodedata
from collections.abc import Iterable
from enum import StrEnum
from pathlib import PurePosixPath
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
MAX_HOSTS = 50
MAX_HOST_LENGTH = 253
MAX_LABEL_LENGTH = 63
MAX_ANCHOR_LENGTH = 80
MAX_SHORT_NAME = 16
WILDCARD_PREFIX = "*."
HTTPS = "https"

# Leading host labels that name a sub-site, not the brand.
_SUBSITE_LABELS = frozenset({"www", "docs", "help", "learn"})
# Two-part public suffixes (a small fixed list; no public-suffix download).
_TWO_PART_SUFFIXES = frozenset(
    {
        "com.au",
        "net.au",
        "org.au",
        "edu.au",
        "gov.au",
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "co.nz",
        "org.nz",
        "govt.nz",
        "co.in",
        "org.in",
        "gov.in",
        "co.jp",
        "com.sg",
        "com.br",
        "co.za",
    }
)
_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
_ANCHOR_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")


class LinkErrorCode(StrEnum):
    INVALID = "invalid_source_url"
    NOT_ALLOWED = "source_url_not_allowed"
    REQUIRED = "source_url_required"


class LinkStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    NOT_ALLOWED = "not_allowed"


class LinkError(ValueError):
    """A rejected URL or host pattern; ``code`` is the problem+json code."""

    def __init__(self, code: LinkErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _invalid(message: str) -> LinkError:
    return LinkError(LinkErrorCode.INVALID, message)


def _has_unsafe_chars(value: str) -> bool:
    """Whitespace, control, format or other non-printing characters."""
    return any(ch.isspace() or unicodedata.category(ch)[0] == "C" for ch in value)


def _ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def ascii_host(host: str) -> str:
    """Lower-cased IDNA (punycode) form of ``host``; ``LinkError`` if invalid."""
    host = host.lower()
    if _ip_literal(host):
        return host
    try:
        encoded = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _invalid("The link's host name is not valid.") from exc
    labels = encoded.split(".")
    if len(encoded) > MAX_HOST_LENGTH or any(
        not label
        or len(label) > MAX_LABEL_LENGTH
        or label.startswith("-")
        or label.endswith("-")
        or not set(label) <= _HOST_CHARS
        for label in labels
    ):
        raise _invalid("The link's host name is not valid.")
    return encoded


def normalize_host_pattern(value: str) -> str:
    """An allow-list entry: a host name, or ``*.`` + host (one leading wildcard)."""
    pattern = value.strip().lower()
    if not pattern:
        raise _invalid("Enter a host name.")
    if len(pattern) > MAX_HOST_LENGTH or _has_unsafe_chars(pattern):
        raise _invalid(f"'{pattern[:MAX_HOST_LENGTH]}' is not a valid host name.")
    wildcard = pattern.startswith(WILDCARD_PREFIX)
    host = pattern[len(WILDCARD_PREFIX) :] if wildcard else pattern
    if "*" in host or "/" in host or ":" in host or "@" in host:
        raise _invalid(
            f"'{pattern}' is not a valid host name. Use a host such as "
            "docs.example.com, or *.example.com for every sub-domain."
        )
    if wildcard and "." not in host:
        raise _invalid(f"'{pattern}' is too broad: use *.example.com.")
    try:
        host = ascii_host(host)
    except LinkError as exc:
        raise _invalid(f"'{pattern}' is not a valid host name.") from exc
    return f"{WILDCARD_PREFIX}{host}" if wildcard else host


def normalize_host_patterns(values: Iterable[str]) -> list[str]:
    """Normalized, de-duplicated (first wins) allow-list entries."""
    out: list[str] = []
    for value in values:
        pattern = normalize_host_pattern(value)
        if pattern not in out:
            out.append(pattern)
    if len(out) > MAX_HOSTS:
        raise _invalid(f"At most {MAX_HOSTS} hosts can be allowed.")
    return out


def host_allowed(host: str, allowed_hosts: Iterable[str]) -> bool:
    """Exact host, or a sub-domain of a ``*.`` entry. Empty list = any host."""
    patterns = list(allowed_hosts)
    if not patterns:
        return True
    host = host.lower()
    for pattern in patterns:
        if pattern.startswith(WILDCARD_PREFIX):
            suffix = pattern[1:]  # ".example.com"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == pattern:
            return True
    return False


def validate_source_url(raw: str, allowed_hosts: Iterable[str] = ()) -> str:
    """The normalized https URL, or ``LinkError`` saying what is wrong."""
    value = raw.strip()
    if not value:
        raise _invalid("Enter a source URL.")
    if len(value) > MAX_URL_LENGTH:
        raise _invalid(f"The link is longer than {MAX_URL_LENGTH} characters.")
    if _has_unsafe_chars(value) or "\\" in value:
        raise _invalid("The link contains spaces or characters that are not allowed.")
    if value.startswith("//"):
        raise _invalid("The link must start with https://.")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise _invalid("The link is not a valid URL.") from exc
    if parts.scheme.lower() != HTTPS:
        raise _invalid("The link must start with https://.")
    if "@" in parts.netloc:
        raise _invalid("The link must not contain a user name or password.")
    if not parts.hostname:
        raise _invalid("The link has no host name.")
    host = ascii_host(parts.hostname)
    if not host_allowed(host, allowed_hosts):
        raise LinkError(
            LinkErrorCode.NOT_ALLOWED,
            f"{host} is not on the allowed domain list (Settings > Knowledge links).",
        )
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    url = urlunsplit((HTTPS, netloc, parts.path, parts.query, parts.fragment))
    if len(url) > MAX_URL_LENGTH:
        raise _invalid(f"The link is longer than {MAX_URL_LENGTH} characters.")
    return url


def link_host(url: str | None) -> str | None:
    """The host of a stored URL (``None`` without one)."""
    if not url:
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def link_status(url: str | None, allowed_hosts: Iterable[str]) -> LinkStatus:
    """``missing`` without a URL, ``not_allowed`` when its host left the list."""
    host = link_host(url)
    if host is None:
        return LinkStatus.MISSING
    return (
        LinkStatus.OK if host_allowed(host, allowed_hosts) else LinkStatus.NOT_ALLOWED
    )


def usable_url(url: str | None, allowed_hosts: Iterable[str]) -> str | None:
    """``url`` when it may be linked right now (stored and still allowed)."""
    return url if link_status(url, allowed_hosts) is LinkStatus.OK else None


# --- section anchors ----------------------------------------------------------


def _explicit_anchor(heading: str) -> str | None:
    """``{#id}`` at the end of a heading (Pandoc / kramdown attribute)."""
    text = heading.rstrip()
    if not text.endswith("}"):
        return None
    start = text.rfind("{#")
    if start < 0:
        return None
    anchor = text[start + 2 : -1].strip().lower()
    if anchor and len(anchor) <= MAX_ANCHOR_LENGTH and set(anchor) <= _ANCHOR_CHARS:
        return anchor
    return None


def heading_text(heading: str) -> str:
    """The heading without a trailing ``{#id}`` attribute."""
    if _explicit_anchor(heading) is None:
        return heading
    return heading[: heading.rstrip().rfind("{#")].rstrip()


def slugify_anchor(heading: str | None) -> str | None:
    """GitHub-style anchor of a section heading (or its explicit ``{#id}``)."""
    if not heading:
        return None
    explicit = _explicit_anchor(heading)
    if explicit:
        return explicit
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD", heading_text(heading)).lower():
        if ch.isascii() and (ch.isalnum() or ch in "-_"):
            out.append(ch)
        elif ch.isspace():
            out.append("-")
    slug = "".join(out).strip("-")[:MAX_ANCHOR_LENGTH]
    return slug or None


def chunk_anchor(metadata: dict | None) -> str | None:
    """A chunk's anchor: stored at ingest, else derived from its heading."""
    meta = metadata or {}
    stored = meta.get("anchor")
    if isinstance(stored, str) and stored and set(stored) <= _ANCHOR_CHARS:
        return stored
    heading = meta.get("section_heading") or meta.get("heading")
    return slugify_anchor(heading) if isinstance(heading, str) else None


def section_url(url: str, anchor: str | None) -> str:
    """``url`` with its fragment replaced by ``anchor`` (unchanged without one)."""
    if not anchor:
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, anchor))


def citation_url(
    url: str | None,
    *,
    section_links: bool,
    metadata: dict | None,
    allowed_hosts: Iterable[str],
) -> str | None:
    """The link a chunk cites: the document URL (+ section anchor when enabled)."""
    usable = usable_url(url, allowed_hosts)
    if usable is None:
        return None
    return section_url(usable, chunk_anchor(metadata)) if section_links else usable


# --- labels -------------------------------------------------------------------


def short_document_name(source_name: str) -> str:
    """The document name without its extension, at most 16 characters."""
    stem = PurePosixPath(source_name.replace("\\", "/")).stem or source_name
    stem = stem.replace("_", " ").replace("-", " ").strip() or source_name
    if len(stem) <= MAX_SHORT_NAME:
        return stem
    return stem[: MAX_SHORT_NAME - 1].rstrip() + "…"


def _display_label(label: str) -> str:
    if label.startswith("xn--"):
        try:
            return label.encode("ascii").decode("idna")
        except UnicodeError:
            return label
    return label


def brand_label(host: str | None, source_name: str) -> str:
    """Short source label for a citation pill.

    The host's brand (``docs.deployiq.com.au`` -> ``deployiq``,
    ``github.com`` -> ``github``), else the short document name (no host,
    or an IP-literal host).
    """
    if not host or _ip_literal(host.strip("[]")):
        return short_document_name(source_name)
    labels = [label for label in host.lower().split(".") if label]
    while len(labels) > 2 and labels[0] in _SUBSITE_LABELS:  # noqa: PLR2004
        labels.pop(0)
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_PART_SUFFIXES:  # noqa: PLR2004
        brand = labels[-3]
    elif len(labels) >= 2:  # noqa: PLR2004
        brand = labels[-2]
    else:
        brand = labels[0] if labels else ""
    return _display_label(brand)[:MAX_SHORT_NAME] or short_document_name(source_name)
