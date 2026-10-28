"""Inline citation markers -> server-side source pills (OR-7).

The context numbers each retrieved chunk (``[Source n | Document: ...]``) and
the system prompt asks for ``[n]`` right after each supported claim. After
generation, :func:`apply_citations`:

- keeps only numbers that name a retrieved source (unknown ones are dropped),
- de-duplicates markers of one run (``[1][3]``) that resolve to the same
  document, and writes the run without inner spaces,
- builds ``citations`` from the cited numbers only. Every URL comes from the
  ``documents`` row of a retrieved source, never from the model's text, so a
  URL injected into a document can never become a link.

:func:`strip_markers` removes markers before the groundedness check, word
counts and history replay. The only regular expression is linear-time
(fixed-length, no nesting): ``\\[(\\d{1,2})\\]``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.links import brand_label, chunk_anchor, citation_url, link_host
from app.knowledge.settings_service import current_knowledge_settings
from app.models.db_models import Document
from app.schemas.query import Citation, SourceChunk

MARKER_RE = re.compile(r"\[(\d{1,2})\]")
TOKEN_DETAILS_KEY = "citations"  # noqa: S105  # nosec B105 - a dict key
_RUN_GAP = frozenset(" \t")


def citation_instruction() -> str:
    """The system-prompt sentence asking for markers (kept in one place)."""
    return (
        "After each sentence or claim supported by the facts, add the "
        "marker of its source, e.g. [1] or [1][3], using only the Source "
        "numbers given in the facts. Never write URLs or invent numbers."
    )


def source_header(number: int) -> str:
    """The context header part naming a source's citation number."""
    return f"Source {number}"


_FREE_BRACKET_RE = re.compile(r"\[([^\[\]\n]{1,200})\](?!\()")
_NUMBERISH_RE = re.compile(r"[\d\s,;-]{1,20}")
_SOURCE_REF_RE = re.compile(r"(?:sources?|docs?|documents?|ref|references?)\b", re.I)
_FENCE = "```"


def _unwrap_bracket(match: re.Match[str]) -> str:
    """Free-text ``[...]``: drop source-like/number-like ones, else unwrap."""
    if MARKER_RE.fullmatch(match.group(0)):
        return match.group(0)
    inner = match.group(1)
    if _NUMBERISH_RE.fullmatch(inner) or _SOURCE_REF_RE.match(inner.strip()):
        return ""
    return inner


_TICK = "`"


def _tick_runs(text: str) -> list[tuple[int, int]]:
    """``(start, length)`` of each run of backticks (one linear pass)."""
    runs: list[tuple[int, int]] = []
    index = text.find(_TICK)
    while index >= 0:
        end = index
        while end < len(text) and text[end] == _TICK:
            end += 1
        runs.append((index, end - index))
        index = text.find(_TICK, end)
    return runs


def _code_span_bounds(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each inline code span, backticks included.

    A span opens with a run of N backticks and closes at the next run of
    exactly N; an unmatched run is literal text (CommonMark). A length that
    found no closer is remembered, so the scan stays near-linear.
    """
    runs = _tick_runs(text)
    spans: list[tuple[int, int]] = []
    unmatched: set[int] = set()
    index = 0
    while index < len(runs):
        start, length = runs[index]
        close = -1
        if length not in unmatched:
            for later in range(index + 1, len(runs)):
                if runs[later][1] == length:
                    close = later
                    break
            if close < 0:
                unmatched.add(length)
        if close < 0:
            index += 1
            continue
        spans.append((start, runs[close][0] + length))
        index = close + 1
    return spans


def _strip_outside_code(text: str) -> str:
    """Strip free brackets from ``text`` except inside inline code spans."""
    out: list[str] = []
    last = 0
    for start, end in _code_span_bounds(text):
        out.append(_FREE_BRACKET_RE.sub(_unwrap_bracket, text[last:start]))
        out.append(text[start:end])
        last = end
    out.append(_FREE_BRACKET_RE.sub(_unwrap_bracket, text[last:]))
    return "".join(out)


def strip_free_brackets(text: str) -> str:
    """``text`` without free-text ``[...]`` (valid ``[n]`` markers are kept).

    Markdown links ``[label](url)``, fenced code blocks and inline code spans
    are left alone.
    """
    if "[" not in text:
        return text
    parts = text.split(_FENCE)
    for index in range(0, len(parts), 2):  # even parts are outside code fences
        parts[index] = _strip_outside_code(parts[index])
    return _FENCE.join(parts)


def strip_markers(text: str) -> str:
    """``text`` without markers (and the spaces right before them)."""
    if "[" not in text:
        return text
    out: list[str] = []
    last = 0
    for match in MARKER_RE.finditer(text):
        out.append(text[last : match.start()].rstrip(" \t"))
        last = match.end()
    out.append(text[last:])
    return "".join(out)


def _runs(text: str) -> list[tuple[int, int, list[int]]]:
    """``(start, end, numbers)`` of each marker run (markers split by spaces)."""
    runs: list[tuple[int, int, list[int]]] = []
    for match in MARKER_RE.finditer(text):
        number = int(match.group(1))
        if runs:
            start, end, numbers = runs[-1]
            gap = text[end : match.start()]
            if all(ch in _RUN_GAP for ch in gap):
                numbers.append(number)
                runs[-1] = (start, match.end(), numbers)
                continue
        runs.append((match.start(), match.end(), [number]))
    return runs


def build_citation(number: int, source: SourceChunk) -> Citation:
    meta = source.metadata or {}
    name = str(meta.get("source_name") or source.title or source.document_id)
    host = link_host(source.url)
    return Citation(
        n=number,
        document_id=source.document_id,
        title=source.title or name,
        url=source.url,
        label=brand_label(host, name),
        host=host,
        anchor=chunk_anchor(meta),
    )


def apply_citations(
    answer: str, sources: Sequence[SourceChunk]
) -> tuple[str, list[Citation]]:
    """The answer with clean markers and the citations they refer to."""
    if "[" not in answer:
        return answer, []
    answer = strip_free_brackets(answer)
    out: list[str] = []
    cited: dict[int, Citation] = {}
    last = 0
    for start, end, numbers in _runs(answer):
        kept: list[int] = []
        documents: set[str] = set()
        for number in numbers:
            if not 1 <= number <= len(sources) or number in kept:
                continue
            source = sources[number - 1]
            if source.document_id in documents:
                continue
            documents.add(source.document_id)
            kept.append(number)
            cited.setdefault(number, build_citation(number, source))
        before = answer[last:start]
        out.append(before if kept else before.rstrip(" \t"))
        out.append("".join(f"[{n}]" for n in kept))
        last = end
    out.append(answer[last:])
    return "".join(out), [cited[n] for n in sorted(cited)]


def citation_records(citations: Iterable[Citation]) -> list[dict[str, Any]]:
    """What is persisted in ``token_details`` (the anchor included)."""
    return [{**c.model_dump(), "anchor": c.anchor} for c in citations]


def stored_citations(token_details: dict | None) -> list[dict[str, Any]]:
    raw = (token_details or {}).get(TOKEN_DETAILS_KEY)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


_DocLink = tuple[str | None, bool, str, str | None]


async def _document_links(
    db: AsyncSession, stored: Sequence[Sequence[dict[str, Any]]]
) -> dict[str, _DocLink]:
    """``(source_url, section_links, source_name, title)`` per cited document."""
    ids = {
        doc_id
        for records in stored
        for record in records
        if (doc_id := _uuid(record.get("document_id")))
    }
    if not ids:
        return {}
    rows = await db.execute(
        select(
            Document.id,
            Document.source_url,
            Document.section_links,
            Document.source_name,
            Document.doc_metadata["title"].as_string(),
        ).where(Document.id.in_(ids))
    )
    return {
        str(doc_id): (url, bool(sections), name, title)
        for doc_id, url, sections, name, title in rows.all()
    }


def _refreshed(
    record: dict[str, Any], found: _DocLink | None, allowed: list[str]
) -> Citation | None:
    try:
        number = int(record.get("n", 0))
    except (TypeError, ValueError):
        return None
    doc_id = str(record.get("document_id") or "")
    anchor = record.get("anchor")
    anchor = anchor if isinstance(anchor, str) else None
    if found is None:
        # The document is gone: keep the stored title, drop the link.
        name = title = str(record.get("title") or doc_id)
        url = None
    else:
        stored_url, sections, name, doc_title = found
        title = doc_title or name
        url = citation_url(
            stored_url,
            section_links=sections,
            metadata={"anchor": anchor} if anchor else None,
            allowed_hosts=allowed,
        )
    host = link_host(url)
    return Citation(
        n=number,
        document_id=doc_id,
        title=title,
        url=url,
        label=brand_label(host, name),
        host=host,
        anchor=anchor,
    )


async def refresh_citations(
    db: AsyncSession, stored: Sequence[Sequence[dict[str, Any]]]
) -> list[list[Citation]]:
    """Each message's citations with today's document links: one ``IN`` query.

    ``stored`` holds the persisted records per message.
    """
    documents = await _document_links(db, stored)
    allowed = current_knowledge_settings().allowed_hosts
    out: list[list[Citation]] = []
    for records in stored:
        refreshed = (
            _refreshed(r, documents.get(str(r.get("document_id") or "")), allowed)
            for r in records
        )
        out.append([c for c in refreshed if c is not None])
    return out
