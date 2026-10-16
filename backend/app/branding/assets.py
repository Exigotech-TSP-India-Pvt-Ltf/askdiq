"""Brand asset validation (OWASP A05): magic-byte sniffing and SVG sanitizing.

Only three formats are accepted, identified from the bytes (never from the
file name or the declared type):

- PNG: the chunk structure is walked (CRC checked) up to ``IEND`` and any
  trailing bytes are rejected, so a PNG/HTML polyglot cannot pass.
- WebP: the RIFF size must match the payload exactly; dimensions come from
  the ``VP8X`` / ``VP8`` / ``VP8L`` header.
- SVG: parsed with ``defusedxml`` (no DTDs or entities) and rebuilt from an
  element and attribute allow-list. ``script``, ``foreignObject``, ``style``
  elements, ``on*`` handlers, external ``href`` / ``xlink:href`` and
  ``javascript:`` / ``data:`` URLs are dropped. The rebuilt document is
  serialized by this module, so nothing outside the allow-list survives.
"""

from __future__ import annotations

import hashlib
import html
import re
import struct
import zlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring


class AssetMime(StrEnum):
    PNG = "image/png"
    WEBP = "image/webp"
    SVG = "image/svg+xml"


class AssetError(ValueError):
    """Rejected upload; ``status_code`` is the HTTP status to answer with."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


HTTP_UNSUPPORTED = 415
HTTP_TOO_LARGE = 413
HTTP_INVALID = 422


@dataclass(frozen=True, slots=True)
class ValidatedAsset:
    sha256: str
    mime: AssetMime
    data: bytes
    width: int | None
    height: int | None


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_IHDR_LEN = 13
_PNG_MAX_CHUNKS = 10_000
_RIFF_HEADER = 12
_WEBP_CHUNK_HEADER = 8
_VP8X_MIN = 10
_VP8_MIN = 10
_VP8L_MIN = 5
_VP8_START_CODE = b"\x9d\x01\x2a"
_VP8L_SIGNATURE = 0x2F
_14_BITS = 0x3FFF

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_MAX_SVG_ELEMENTS = 5_000
_MAX_SVG_DEPTH = 32

DECLARED_ALIASES = {
    "image/png": AssetMime.PNG,
    "image/webp": AssetMime.WEBP,
    "image/svg+xml": AssetMime.SVG,
}
# Declared types that carry no claim (browsers send these for unknown files).
NEUTRAL_DECLARED = frozenset({"", "application/octet-stream"})

ALLOWED_ELEMENTS = frozenset(
    {
        "svg",
        "g",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "defs",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "mask",
        "symbol",
        "use",
        "title",
        "desc",
        "text",
        "tspan",
    }
)
TEXT_ELEMENTS = frozenset({"title", "desc", "text", "tspan"})
ALLOWED_ATTRIBUTES = frozenset(
    {
        "id",
        "class",
        "style",
        "version",
        "viewBox",
        "preserveAspectRatio",
        "width",
        "height",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "fx",
        "fy",
        "dx",
        "dy",
        "d",
        "points",
        "transform",
        "fill",
        "fill-opacity",
        "fill-rule",
        "clip-rule",
        "clip-path",
        "clipPathUnits",
        "mask",
        "maskUnits",
        "maskContentUnits",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-opacity",
        "opacity",
        "color",
        "display",
        "visibility",
        "overflow",
        "vector-effect",
        "shape-rendering",
        "offset",
        "stop-color",
        "stop-opacity",
        "gradientUnits",
        "gradientTransform",
        "spreadMethod",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-anchor",
        "dominant-baseline",
        "letter-spacing",
        "href",
    }
)
_HREF_ATTRIBUTES = frozenset({"href", f"{{{XLINK_NS}}}href"})
_LOCAL_REF = re.compile(r"^#[A-Za-z_][\w.\-]*$")
_URL_FUNC = re.compile(r"url\(\s*(['\"]?)([^)'\"]*)\1\s*\)", re.IGNORECASE)
_CONTROL_AND_SPACE = re.compile(r"[\s\x00-\x1f\x7f]+")
_FORBIDDEN_VALUE_TOKENS = (
    "javascript:",
    "vbscript:",
    "data:",
    "expression(",
    "@import",
    "behavior:",
    "-moz-binding",
)
_NUMBER = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(px)?\s*$")
_VIEWBOX_PARTS = 4


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Raster formats
# ---------------------------------------------------------------------------


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Walk every chunk; reject bad CRCs, a missing IEND and trailing bytes."""
    pos = len(PNG_SIGNATURE)
    size: tuple[int, int] | None = None
    for index in range(_PNG_MAX_CHUNKS):
        if pos + 8 > len(data):
            break
        length, ctype = struct.unpack(">I4s", data[pos : pos + 8])
        end = pos + 8 + length + 4
        if end > len(data) or not ctype.isalpha():
            break
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[end - 4 : end])
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != crc:
            raise AssetError(HTTP_UNSUPPORTED, "PNG chunk checksum mismatch.")
        if index == 0:
            if ctype != b"IHDR" or length != _PNG_IHDR_LEN:
                break
            size = struct.unpack(">II", body[:8])
        if ctype == b"IEND":
            if end != len(data):
                raise AssetError(
                    HTTP_UNSUPPORTED, "Unexpected data after the PNG image end."
                )
            if size is None:
                break
            return size
        pos = end
    raise AssetError(HTTP_UNSUPPORTED, "Malformed PNG image.")


def webp_dimensions(data: bytes) -> tuple[int, int]:
    """Canvas size from the first WebP chunk; the RIFF size must be exact."""
    (riff_size,) = struct.unpack("<I", data[4:8])
    if riff_size + 8 != len(data) or len(data) < _RIFF_HEADER + _WEBP_CHUNK_HEADER:
        raise AssetError(HTTP_UNSUPPORTED, "Malformed WebP image.")
    fourcc = data[12:16]
    chunk = data[_RIFF_HEADER + _WEBP_CHUNK_HEADER :]
    if fourcc == b"VP8X" and len(chunk) >= _VP8X_MIN:
        width = int.from_bytes(chunk[4:7], "little") + 1
        height = int.from_bytes(chunk[7:10], "little") + 1
        return width, height
    if fourcc == b"VP8 " and len(chunk) >= _VP8_MIN and chunk[3:6] == _VP8_START_CODE:
        width = int.from_bytes(chunk[6:8], "little") & _14_BITS
        height = int.from_bytes(chunk[8:10], "little") & _14_BITS
        return width, height
    if fourcc == b"VP8L" and len(chunk) >= _VP8L_MIN and chunk[0] == _VP8L_SIGNATURE:
        bits = int.from_bytes(chunk[1:5], "little")
        return (bits & _14_BITS) + 1, ((bits >> 14) & _14_BITS) + 1
    raise AssetError(HTTP_UNSUPPORTED, "Malformed WebP image.")


def sniff(data: bytes) -> AssetMime | None:
    """The format named by the magic bytes, or None (SVG is decided by parsing)."""
    if data.startswith(PNG_SIGNATURE):
        return AssetMime.PNG
    if len(data) >= _RIFF_HEADER and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return AssetMime.WEBP
    return None


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------


def _split_tag(tag: Any) -> tuple[str | None, str]:
    if not isinstance(tag, str):  # comments / processing instructions
        return None, ""
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        return namespace, local
    return None, tag


def safe_attribute_value(value: str) -> bool:
    """False when a value could run script or fetch something external."""
    compact = _CONTROL_AND_SPACE.sub("", html.unescape(value)).lower()
    if any(token in compact for token in _FORBIDDEN_VALUE_TOKENS):
        return False
    if "\\" in compact:  # CSS escapes can smuggle any of the above
        return False
    # Every url(...) must be a local fragment reference such as url(#grad).
    targets = [match.group(2).strip() for match in _URL_FUNC.finditer(value)]
    if compact.count("url(") != len(targets):
        return False
    return all(_LOCAL_REF.match(target) for target in targets)


def _clean_attributes(attrib: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in attrib.items():
        if key in _HREF_ATTRIBUTES:
            if _LOCAL_REF.match(value.strip()):
                cleaned["xlink:href" if key.startswith("{") else "href"] = value.strip()
            continue
        if key.startswith("{") or key.lower().startswith("on"):
            continue
        if key in ALLOWED_ATTRIBUTES and safe_attribute_value(value):
            cleaned[key] = value
    return cleaned


def _serialize(
    element: Any, parts: list[str], depth: int, budget: list[int], *, root: bool
) -> None:
    namespace, local = _split_tag(element.tag)
    if namespace != SVG_NS or local not in ALLOWED_ELEMENTS:
        return
    if depth > _MAX_SVG_DEPTH:
        raise AssetError(HTTP_INVALID, "SVG is nested too deeply.")
    budget[0] -= 1
    if budget[0] < 0:
        raise AssetError(HTTP_INVALID, "SVG has too many elements.")
    attrs = _clean_attributes(dict(element.attrib))
    if root:
        attrs = {"xmlns": SVG_NS, "xmlns:xlink": XLINK_NS, **attrs}
    rendered = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in attrs.items())
    parts.append(f"<{local}{rendered}>")
    if local in TEXT_ELEMENTS and element.text:
        parts.append(html.escape(element.text, quote=False))
    for child in element:
        _serialize(child, parts, depth + 1, budget, root=False)
        if local in TEXT_ELEMENTS and child.tail:
            parts.append(html.escape(child.tail, quote=False))
    parts.append(f"</{local}>")


def sanitize_svg(data: bytes) -> bytes:
    """Rebuild ``data`` from the allow-list; 415 when it is not an SVG document."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AssetError(HTTP_UNSUPPORTED, "Unsupported file type.") from exc
    try:
        root = fromstring(text, forbid_dtd=True, forbid_entities=True)
    except (ParseError, DefusedXmlException, ValueError) as exc:
        raise AssetError(
            HTTP_UNSUPPORTED, "Unsupported file type: only PNG, WebP or SVG."
        ) from exc
    if _split_tag(root.tag) != (SVG_NS, "svg"):
        raise AssetError(
            HTTP_UNSUPPORTED, "Unsupported file type: only PNG, WebP or SVG."
        )
    parts: list[str] = []
    _serialize(root, parts, 0, [_MAX_SVG_ELEMENTS], root=True)
    return "".join(parts).encode("utf-8")


def _svg_length(value: str | None) -> int | None:
    match = _NUMBER.match(value or "")
    return round(float(match.group(1))) if match else None


def svg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """Intrinsic size from width/height, else the viewBox (None if unknown)."""
    root = fromstring(data.decode("utf-8"), forbid_dtd=True)
    width = _svg_length(root.get("width"))
    height = _svg_length(root.get("height"))
    if width is None or height is None:
        parts = (root.get("viewBox") or "").replace(",", " ").split()
        if len(parts) == _VIEWBOX_PARTS:
            try:
                width, height = round(float(parts[2])), round(float(parts[3]))
            except ValueError:
                return None, None
    return width, height


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _declared_mismatch(declared: str | None, actual: AssetMime) -> bool:
    kind = (declared or "").split(";", 1)[0].strip().lower()
    if kind in NEUTRAL_DECLARED:
        return False
    return DECLARED_ALIASES.get(kind) is not actual


def validate_asset(
    data: bytes,
    declared_type: str | None,
    *,
    max_bytes: int,
    max_dimension: int,
) -> ValidatedAsset:
    """Validate (and for SVG, sanitize) an upload; AssetError on rejection."""
    if len(data) > max_bytes:
        raise AssetError(
            HTTP_TOO_LARGE, f"File is larger than the {max_bytes // 1024} KB limit."
        )
    if not data:
        raise AssetError(HTTP_INVALID, "The file is empty.")
    mime = sniff(data)
    width: int | None
    height: int | None
    if mime is AssetMime.PNG:
        width, height = png_dimensions(data)
    elif mime is AssetMime.WEBP:
        width, height = webp_dimensions(data)
    else:
        mime = AssetMime.SVG
        data = sanitize_svg(data)
        width, height = svg_dimensions(data)
    if _declared_mismatch(declared_type, mime):
        raise AssetError(
            HTTP_UNSUPPORTED,
            f"Declared content type {declared_type!r} does not match the file ({mime}).",
        )
    if mime is not AssetMime.SVG:
        if not width or not height:
            raise AssetError(HTTP_INVALID, "Image has no size.")
        if width > max_dimension or height > max_dimension:
            raise AssetError(
                HTTP_INVALID,
                f"Image is {width}x{height}; the maximum is "
                f"{max_dimension}x{max_dimension}.",
            )
    return ValidatedAsset(sha256_hex(data), mime, data, width, height)
