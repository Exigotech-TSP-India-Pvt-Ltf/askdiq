"""Knowledge-base quality: cross-page de-duplication and a chunk report.

``dedupe_across_pages`` keeps a repeated paragraph or list item only on the
most specific page (the longest route: ``/zero-trust`` over ``/services``
over ``/``), so one fact is retrieved from one place.

``chunk_report`` lists what a reviewer must look at after an ingest: chunk
counts and sizes, chunks that are too small or too large, leftover markup or
UI noise, and redaction placeholders.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

MIN_SHARED_CHARS = 60
MIN_CHUNK_CHARS = 120
MAX_CHUNK_CHARS = 1600
LIST_MARKERS = ("- ", "* ", "+ ")
NOISE_MARKERS = (
    "**",
    "[CTA]",
    "[Link]",
    "_(",
    "_[",
    "<br",
    "placeholder:",
    "button text",
    "src/",
    ".tsx",
    "](",
    "|---",
)
REDACTION_MARKER = "[REDACTED_"


def _key(line: str) -> str:
    text = line.strip()
    for marker in LIST_MARKERS:
        text = text.removeprefix(marker)
    return " ".join(text.lower().split())


def _is_heading(line: str) -> bool:
    return line.lstrip().startswith("#")


def dedupe_across_pages(
    pages: Mapping[str, str], routes: Mapping[str, str | None]
) -> tuple[dict[str, str], list[dict]]:
    """``(deduped pages, removals)``; ``pages`` maps name -> cleaned Markdown."""
    owners: dict[str, str] = {}
    rank = {
        name: (len(routes.get(name) or ""), name) for name in pages
    }  # longer route = more specific page
    for name, text in pages.items():
        for line in text.split("\n"):
            key = _key(line)
            if len(key) < MIN_SHARED_CHARS or _is_heading(line):
                continue
            owner = owners.get(key)
            if owner is None or rank[name] > rank[owner]:
                owners[key] = name
    removals: list[dict] = []
    deduped: dict[str, str] = {}
    for name, text in pages.items():
        kept = []
        for line in text.split("\n"):
            key = _key(line)
            owner = owners.get(key)
            if owner is not None and owner != name and not _is_heading(line):
                removals.append({"page": name, "kept_on": owner, "text": key[:120]})
                continue
            kept.append(line)
        deduped[name] = "\n".join(kept)
    return deduped, removals


@dataclass
class DocReport:
    name: str
    chunks: int = 0
    min_chars: int = 0
    max_chars: int = 0
    warnings: list[str] = field(default_factory=list)


def chunk_report(name: str, contents: Iterable[str]) -> DocReport:
    """Sizes plus every chunk that needs a reviewer's attention."""
    texts = list(contents)
    sizes = [len(t) for t in texts]
    report = DocReport(
        name=name,
        chunks=len(texts),
        min_chars=min(sizes, default=0),
        max_chars=max(sizes, default=0),
    )
    for index, text in enumerate(texts):
        if len(text) < MIN_CHUNK_CHARS:
            report.warnings.append(f"chunk {index}: small ({len(text)} chars)")
        if len(text) > MAX_CHUNK_CHARS:
            report.warnings.append(f"chunk {index}: large ({len(text)} chars)")
        if REDACTION_MARKER in text:
            report.warnings.append(f"chunk {index}: redacted value")
        if found := [m for m in NOISE_MARKERS if m in text]:
            report.warnings.append(f"chunk {index}: markup {found}")
    return report
