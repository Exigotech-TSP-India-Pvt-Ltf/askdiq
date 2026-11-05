"""Knowledge-base quality: cross-page de-duplication and a chunk report.

``dedupe_across_pages`` keeps a repeated paragraph or list item only on the
most specific page (the longest route: ``/zero-trust`` over ``/services``
over ``/``), so one fact is retrieved from one place.
``dedupe_upload`` applies the same rule to one uploaded document against the
stored ones, changing only the upload.

``chunk_report`` lists what a reviewer must look at after an ingest: chunk
counts and sizes, chunks that are too small or too large, leftover markup or
UI noise, and redaction placeholders.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.ingestion.cleanup import SAMPLE_SECTION_TEXT

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


def _always_kept(line: str) -> bool:
    """Headings and sample framing stay on every page: the framing tells the
    reader that page's figures are illustrative."""
    return line.lstrip().startswith("#") or SAMPLE_SECTION_TEXT in line


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
            if len(key) < MIN_SHARED_CHARS or _always_kept(line):
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
            if owner is not None and owner != name and not _always_kept(line):
                removals.append({"page": name, "kept_on": owner, "text": key[:120]})
                continue
            kept.append(line)
        deduped[name] = "\n".join(kept)
    return deduped, removals


def dedupe_upload(
    name: str,
    text: str,
    route: str | None,
    others: Mapping[str, tuple[str, str | None]],
) -> tuple[str, list[dict]]:
    """``(upload text, removals)``: the ``dedupe_across_pages`` rule for one page.

    ``others`` maps every other document's name to ``(stored Markdown,
    route)``. A paragraph or list item that another, more specific page
    already carries is dropped from the upload; the other documents are
    never changed (their de-duplicated text is discarded).
    """
    pages = {other: body for other, (body, _) in others.items() if other != name}
    routes = {other: rte for other, (_, rte) in others.items() if other != name}
    pages[name] = text
    routes[name] = route
    deduped, removals = dedupe_across_pages(pages, routes)
    return deduped[name], [r for r in removals if r["page"] == name]


def duplicate_warning(removal: Mapping[str, str]) -> str:
    """The response warning for one line dropped by ``dedupe_upload``."""
    return (
        f'duplicate_removed: "{removal["text"]}" is already on '
        f"{removal['kept_on']} (kept on the most specific page)"
    )


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
