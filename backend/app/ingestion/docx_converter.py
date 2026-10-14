"""Server-side DOCX to Markdown conversion with enterprise security controls (Tasks 1.4.3 & 1.4.4).

Security controls implemented:
- ZIP magic verification (PK\x03\x04)
- ZIP bomb protection (max 1000 entries, max 100MB uncompressed, ratio <= 100:1)
- Rejection of macro-enabled content (vbaProject.bin) and encrypted containers
- Safe XML entity parsing with defusedxml (XXE prevention)
- GFM table, headings, lists, bold/italic, and link preservation
- Stripping of javascript:/data: links and raw HTML
- Drop images with warnings (images_dropped)
- Prompt injection detection via UNSAFE_PATTERNS (quarantining)
- Bounded concurrency with asyncio.Semaphore
"""

import asyncio
import io
import re
import urllib.parse
import zipfile
from html.parser import HTMLParser
from typing import NamedTuple

import defusedxml.minidom
import mammoth

from app.core.config import get_settings
from app.guardrails.pre_checks import UNSAFE_PATTERNS

settings = get_settings()
_convert_semaphore: asyncio.Semaphore | None = None


def get_convert_semaphore() -> asyncio.Semaphore:
    global _convert_semaphore
    if _convert_semaphore is None:
        _convert_semaphore = asyncio.Semaphore(settings.ingest_convert_concurrency)
    return _convert_semaphore


SAFE_SCHEMES = {"http", "https", "mailto"}


class ConversionResult(NamedTuple):
    markdown: str
    warnings: list[str]
    quarantined: bool
    images_dropped: int


class _HTMLToMarkdown(HTMLParser):
    """Deterministic, streaming HTML to GitHub-Flavored Markdown converter."""

    def __init__(self):
        super().__init__()
        self.output: list[str] = []
        self.tag_stack: list[str] = []
        self.list_stack: list[str] = []  # "ul" or "ol"
        self.ol_counters: list[int] = []
        self.images_dropped = 0
        self.warnings: list[str] = []

        # Table state
        self.in_table = False
        self.table_rows: list[list[str]] = []
        self.current_row: list[str] = []
        self.current_cell: list[str] = []
        self.is_header_row = False

        # Link state
        self.current_link_url: str | None = None
        self.current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_dict = {k.lower(): v for k, v in attrs if v is not None}
        self.tag_stack.append(tag)

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._ensure_newline(2)
            self.output.append("#" * level + " ")
        elif tag == "p":
            self._ensure_newline(2)
        elif tag in ("strong", "b"):
            self.output.append("**")
        elif tag in ("em", "i"):
            self.output.append("*")
        elif tag == "code":
            self.output.append("`")
        elif tag == "pre":
            self._ensure_newline(2)
            self.output.append("```\n")
        elif tag == "ul":
            self.list_stack.append("ul")
            self._ensure_newline()
        elif tag == "ol":
            self.list_stack.append("ol")
            self.ol_counters.append(1)
            self._ensure_newline()
        elif tag == "li":
            self._ensure_newline()
            indent = "  " * max(len(self.list_stack) - 1, 0)
            if self.list_stack and self.list_stack[-1] == "ol":
                count = self.ol_counters[-1] if self.ol_counters else 1
                self.output.append(f"{indent}{count}. ")
                if self.ol_counters:
                    self.ol_counters[-1] += 1
            else:
                self.output.append(f"{indent}- ")
        elif tag == "a":
            href = attr_dict.get("href", "").strip()
            parsed = urllib.parse.urlparse(href)
            if parsed.scheme.lower() in SAFE_SCHEMES:
                self.current_link_url = href
            else:
                self.current_link_url = None
                if href:
                    self.warnings.append(f"Sanitized unsafe link destination: {href}")
            self.current_link_text = []
        elif tag == "table":
            self._ensure_newline(2)
            self.in_table = True
            self.table_rows = []
        elif tag == "tr":
            if self.in_table:
                self.current_row = []
                self.is_header_row = False
        elif tag in ("th", "td"):
            if self.in_table:
                self.current_cell = []
                if tag == "th":
                    self.is_header_row = True
        elif tag == "img":
            self.images_dropped += 1
        elif tag == "br":
            self.output.append("\n")

    def handle_endtag(self, tag: str):
        if self.tag_stack and self.tag_stack[-1] == tag:
            self.tag_stack.pop()

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") or tag == "p":
            self.output.append("\n\n")
        elif tag in ("strong", "b"):
            self.output.append("**")
        elif tag in ("em", "i"):
            self.output.append("*")
        elif tag == "code":
            self.output.append("`")
        elif tag == "pre":
            self.output.append("\n```\n\n")
        elif tag == "ul":
            if self.list_stack:
                self.list_stack.pop()
            self._ensure_newline()
        elif tag == "ol":
            if self.list_stack:
                self.list_stack.pop()
            if self.ol_counters:
                self.ol_counters.pop()
            self._ensure_newline()
        elif tag == "li":
            self._ensure_newline()
        elif tag == "a":
            text = "".join(self.current_link_text).strip()
            if self.current_link_url:
                self.output.append(f"[{text}]({self.current_link_url})")
            else:
                self.output.append(text)
            self.current_link_url = None
            self.current_link_text = []
        elif tag in ("th", "td"):
            if self.in_table:
                cell_text = "".join(self.current_cell).replace("|", "\\|").strip()
                self.current_row.append(cell_text)
                self.current_cell = []
        elif tag == "tr":
            if self.in_table:
                if self.current_row:
                    self.table_rows.append(self.current_row)
                self.current_row = []
        elif tag == "table" and self.in_table:
            self._render_gfm_table()
            self.in_table = False
            self.table_rows = []

    def handle_data(self, data: str):
        if self.in_table and (self.tag_stack and self.tag_stack[-1] in ("th", "td")):
            self.current_cell.append(data)
        elif self.current_link_url is not None or (
            self.tag_stack and self.tag_stack[-1] == "a"
        ):
            self.current_link_text.append(data)
        else:
            self.output.append(data)

    def _ensure_newline(self, count: int = 1):
        trailing_newlines = 0
        for item in reversed(self.output):
            for ch in reversed(item):
                if ch == "\n":
                    trailing_newlines += 1
                else:
                    needed = max(0, count - trailing_newlines)
                    if needed > 0:
                        self.output.append("\n" * needed)
                    return
        if not self.output:
            return

    def _render_gfm_table(self):
        if not self.table_rows:
            return
        num_cols = max(len(r) for r in self.table_rows)
        if num_cols == 0:
            return

        # Header row
        header = self.table_rows[0]
        padded_header = [header[i] if i < len(header) else "" for i in range(num_cols)]
        self.output.append("| " + " | ".join(padded_header) + " |\n")

        # Delimiter row
        self.output.append("| " + " | ".join(["---"] * num_cols) + " |\n")

        # Body rows
        for row in self.table_rows[1:]:
            padded_row = [row[i] if i < len(row) else "" for i in range(num_cols)]
            self.output.append("| " + " | ".join(padded_row) + " |\n")
        self.output.append("\n")

    def get_markdown(self) -> str:
        raw_md = "".join(self.output)
        # Clean up excessive blank lines
        return re.sub(r"\n{3,}", "\n\n", raw_md).strip() + "\n"


def validate_docx_bytes(data: bytes) -> None:
    """Validate DOCX format and enforce zip bomb/macro/encryption security controls."""
    if len(data) > settings.ingest_max_file_bytes:
        raise ValueError(
            f"File size ({len(data)} bytes) exceeds limit of {settings.ingest_max_file_bytes} bytes"
        )

    # 1. ZIP magic check
    if not data.startswith(b"PK\x03\x04"):
        raise ValueError("Invalid Word document: missing ZIP archive signature")

    # 2. Inspect ZIP entries
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            entries = zf.infolist()
            if len(entries) > 1000:
                raise ValueError(
                    "Potential ZIP bomb: archive contains more than 1000 entries"
                )

            total_uncompressed = sum(info.file_size for info in entries)
            if total_uncompressed > 100 * 1024 * 1024:
                raise ValueError(
                    "Potential ZIP bomb: uncompressed content exceeds 100MB limit"
                )

            if len(data) > 0 and (total_uncompressed / len(data)) > 100:
                raise ValueError(
                    "Potential ZIP bomb: compression ratio exceeds 100:1 safety limit"
                )

            names = set(zf.namelist())
            # Mandatory DOCX components
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ValueError(
                    "Invalid Word document structure: missing document.xml"
                )

            # Check macro/executable payload
            for name in names:
                name_lower = name.lower()
                if "vbaproject.bin" in name_lower or name_lower.endswith(
                    (".exe", ".vba", ".bat", ".cmd")
                ):
                    raise ValueError(
                        "Macro-enabled documents (.docm/VBA/executables) are strictly prohibited"
                    )

            # Check encryption
            for info in entries:
                if info.flag_bits & 0x1:
                    raise ValueError(
                        "Encrypted or password-protected Word documents are not supported"
                    )

            # Validate XML structure with defusedxml to block XXE
            try:
                xml_content = zf.read("word/document.xml")
                defusedxml.minidom.parseString(xml_content)
            except Exception as exc:
                raise ValueError(
                    f"Unsafe or malformed XML document structure: {exc}"
                ) from exc

    except zipfile.BadZipFile as exc:
        raise ValueError("Corrupted or unreadable Word document archive") from exc


def _convert_docx_sync(file_bytes: bytes) -> ConversionResult:
    """Synchronous CPU-bound conversion and security analysis."""
    validate_docx_bytes(file_bytes)

    dropped_images = 0

    def _ignore_image(image):
        nonlocal dropped_images
        dropped_images += 1
        return []

    # Run mammoth conversion to HTML
    result = mammoth.convert_to_html(
        io.BytesIO(file_bytes),
        convert_image=mammoth.images.inline(_ignore_image),
        ignore_empty_paragraphs=True,
    )

    warnings: list[str] = [msg.message for msg in result.messages]
    if dropped_images > 0:
        warnings.append(f"images_dropped={dropped_images}")

    # Parse HTML to Markdown
    parser = _HTMLToMarkdown()
    parser.feed(result.value)
    markdown = parser.get_markdown()
    warnings.extend(parser.warnings)

    if parser.images_dropped > 0:
        warnings.append(f"images_dropped={parser.images_dropped}")

    # Cap character length
    if len(markdown) > settings.ingest_max_chars:
        markdown = markdown[: settings.ingest_max_chars]
        warnings.append(f"Content capped at {settings.ingest_max_chars} characters")

    # Prompt injection check
    quarantined = False
    for pat in UNSAFE_PATTERNS:
        if re.search(pat, markdown, re.IGNORECASE):
            quarantined = True
            warnings.append(
                "Indirect prompt injection pattern detected in document content"
            )
            break

    return ConversionResult(
        markdown=markdown,
        warnings=warnings,
        quarantined=quarantined,
        images_dropped=dropped_images + parser.images_dropped,
    )


async def convert_docx(file_bytes: bytes) -> ConversionResult:
    """Asynchronously convert DOCX to Markdown under bounded concurrency semaphore."""
    async with get_convert_semaphore():
        return await asyncio.to_thread(_convert_docx_sync, file_bytes)
