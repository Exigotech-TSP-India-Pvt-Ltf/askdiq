"""Source-link metadata carried by an uploaded file (pre-fills the upload).

- ``.md``: a leading ``---`` YAML block (front matter), parsed with
  ``yaml.safe_load`` only, capped at 4 KB. Scalar keys ``url`` /
  ``source_url``, ``title`` and ``section_links`` are read; the block is
  removed from the text **before** normalisation, PII redaction and chunking.
  A block that is not a mapping is left in place (it is content, e.g. a
  horizontal rule); malformed YAML is ignored with a warning.
- ``.docx``: ``docProps/core.xml`` ``dc:identifier`` (and ``dc:title``),
  falling back to the custom property ``SourceURL`` in
  ``docProps/custom.xml``, parsed with ``defusedxml``.

Values found here are suggestions: the route validates them like any other
URL and the upload form field wins over them.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from typing import Any

import defusedxml.ElementTree as SafeET
import yaml
from defusedxml import DefusedXmlException

FRONT_MATTER_MAX_BYTES = 4096
DOCX_PART_MAX_BYTES = 256 * 1024
MAX_TITLE_CHARS = 300
_DELIMITER = "---"
_END_MARKERS = ("---", "...")
_URL_KEYS = ("url", "source_url")
_DC = "{http://purl.org/dc/elements/1.1/}"
_CUSTOM = "{http://schemas.openxmlformats.org/officeDocument/2006/custom-properties}"
_CUSTOM_URL_NAMES = frozenset({"sourceurl", "source_url", "source url"})

WARN_FRONT_MATTER_INVALID = "front_matter_invalid: the YAML header was ignored"
WARN_FRONT_MATTER_TOO_LARGE = (
    f"front_matter_too_large: the YAML header is over {FRONT_MATTER_MAX_BYTES} "
    "bytes and was ignored"
)


@dataclass(frozen=True, slots=True)
class FileLinkMeta:
    """Link metadata found in a file (all optional)."""

    url: str | None = None
    title: str | None = None
    section_links: bool | None = None


@dataclass(frozen=True, slots=True)
class FrontMatter:
    body: str
    meta: FileLinkMeta = field(default_factory=FileLinkMeta)
    warnings: tuple[str, ...] = ()


def _scalar_str(value: Any, limit: int) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:limit]
    return None


def _meta_from_mapping(data: dict[Any, Any]) -> FileLinkMeta:
    url = next(
        (u for key in _URL_KEYS if (u := _scalar_str(data.get(key), 4096))), None
    )
    links = data.get("section_links")
    return FileLinkMeta(
        url=url,
        title=_scalar_str(data.get("title"), MAX_TITLE_CHARS),
        section_links=links if isinstance(links, bool) else None,
    )


def _find_block(text: str) -> tuple[str, str] | str | None:
    """``(yaml_block, body)``, ``None`` (no front matter) or a warning."""
    first_end = text.find("\n")
    if first_end < 0 or text[:first_end].rstrip() != _DELIMITER:
        return None
    pos = first_end + 1
    while pos < len(text):
        if pos - first_end > FRONT_MATTER_MAX_BYTES:
            return WARN_FRONT_MATTER_TOO_LARGE
        end = text.find("\n", pos)
        line_end = len(text) if end < 0 else end
        if text[pos:line_end].rstrip() in _END_MARKERS:
            body = "" if end < 0 else text[end + 1 :]
            return text[first_end + 1 : pos], body
        if end < 0:
            return None
        pos = end + 1
    return None


def _parse_block(text: str, block: str, body: str) -> FrontMatter:
    if len(block.encode("utf-8")) > FRONT_MATTER_MAX_BYTES:
        return FrontMatter(body=text, warnings=(WARN_FRONT_MATTER_TOO_LARGE,))
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return FrontMatter(body=text, warnings=(WARN_FRONT_MATTER_INVALID,))
    if data is None:
        return FrontMatter(body=body)
    if not isinstance(data, dict):
        return FrontMatter(body=text)  # not front matter: keep it as content
    return FrontMatter(body=body, meta=_meta_from_mapping(data))


def split_front_matter(text: str) -> FrontMatter:
    """Remove a leading YAML front-matter block and read its link keys."""
    found = _find_block(text)
    if found is None:
        return FrontMatter(body=text)
    if isinstance(found, str):
        return FrontMatter(body=text, warnings=(found,))
    return _parse_block(text, *found)


def _read_part(zf: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > DOCX_PART_MAX_BYTES:
        return None
    return zf.read(info)


def _parse(xml: bytes | None) -> Any:
    if not xml:
        return None
    try:
        return SafeET.fromstring(xml)
    except (SafeET.ParseError, DefusedXmlException, ValueError):
        return None


def _custom_url(root: Any) -> str | None:
    if root is None:
        return None
    for prop in root.iter(f"{_CUSTOM}property"):
        if str(prop.get("name", "")).strip().lower() in _CUSTOM_URL_NAMES:
            for child in prop:
                if value := _scalar_str(child.text, 4096):
                    return value
    return None


def docx_link_meta(data: bytes) -> FileLinkMeta:
    """``dc:identifier`` / ``dc:title``, else custom ``SourceURL`` (never raises)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            core = _parse(_read_part(zf, "docProps/core.xml"))
            custom_xml = _read_part(zf, "docProps/custom.xml")
    except (zipfile.BadZipFile, OSError, ValueError):
        return FileLinkMeta()
    identifier = title = None
    if core is not None:
        ident = core.find(f"{_DC}identifier")
        identifier = _scalar_str(ident.text if ident is not None else None, 4096)
        title_el = core.find(f"{_DC}title")
        title = _scalar_str(
            title_el.text if title_el is not None else None, MAX_TITLE_CHARS
        )
    if identifier is None or not identifier.lower().startswith("http"):
        identifier = _custom_url(_parse(custom_xml)) or identifier
    return FileLinkMeta(url=identifier, title=title)
