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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.guardrails.post_checks import citation_stems, is_decline_sentence
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


# ---------------------------------------------------------------------------
# Citation verification: a marker survives only when the cited chunk really
# contains the claim it sits on. Every regex is bounded / linear-time.
# ---------------------------------------------------------------------------
# A claim ends at ./!/? + whitespace (unless a marker follows: "claim. [1]") or a newline.
_UNIT_SPLIT_RE = re.compile(r"((?<=[.!?])[ \t]+(?![ \t]|\[\d)|\n+)")
_BULLET_RE = re.compile(r"^\s{0,8}(?:[-*•]|\d{1,2}[.)])\s+")
_EMPHASIS_RE = re.compile(r"\*{1,3}|_{2}|`")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3})")
_NUMBER_RE = re.compile(r"\d{1,12}(?:\.\d{1,6})?")
_PLAN_WORD_RE = re.compile(r"\b(?:free|starter|professional)\b", re.IGNORECASE)
_FRAMEWORKS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("essential_eight", r"\bessential\s{1,3}(?:eight|8)\b|\be8\b"),
        ("iso_27001", r"\biso[\s/-]{0,3}(?:iec[\s/-]{0,3})?27001\b"),
        ("cis", r"\bcis\b"),
        ("zero_trust", r"\bzero[\s-]{1,3}trust\b"),
        ("security_baseline", r"\bsecurity\s{1,3}baseline\b"),
    )
)
_NUMBER_WORDS = {
    "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten",
}  # fmt: skip
_MAX_CHUNK_CHARS = 20000


@dataclass
class VerifiedCitations:
    """Outcome of :func:`verify_and_select`."""

    answer: str
    citations: list[Citation] = field(default_factory=list)
    sources: list[SourceChunk] = field(default_factory=list)


def _numbers(text: str) -> list[str]:
    return _NUMBER_RE.findall(_THOUSANDS_RE.sub("", text))


def _normalize_number(number: str) -> str:
    return number.rstrip("0").rstrip(".") if "." in number else number


class _ChunkIndex:
    """Lazily computed lexical facts about one retrieved chunk."""

    def __init__(self, source: SourceChunk) -> None:
        self.source = source
        text = source.content[:_MAX_CHUNK_CHARS].lower()
        self.text = text
        self.stems = citation_stems(text)
        self.numbers = {_normalize_number(n) for n in _numbers(text)}
        self.frameworks = {name for name, rx in _FRAMEWORKS if rx.search(text)}
        self.plan_words = {m.group(0).lower() for m in _PLAN_WORD_RE.finditer(text)}

    def has_number(self, number: str) -> bool:
        word = _NUMBER_WORDS.get(number)
        return number in self.numbers or (
            word is not None and re.search(rf"\b{word}\b", self.text) is not None
        )


class _Claim:
    """One claim sentence: its content stems and the figures it asserts."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.stems = citation_stems(text)
        lowered = text.lower()
        self.frameworks = {name for name, rx in _FRAMEWORKS if rx.search(lowered)}
        stripped = lowered
        for _, rx in _FRAMEWORKS:
            stripped = rx.sub(" ", stripped)
        self.numbers = {_normalize_number(n) for n in _numbers(stripped)}
        self.plan_words = {m.group(0).lower() for m in _PLAN_WORD_RE.finditer(lowered)}


def _overlap(claim: _Claim, chunk: _ChunkIndex) -> float:
    if not claim.stems:
        return 0.0
    return len(claim.stems & chunk.stems) / len(claim.stems)


def _contains_figures(claim: _Claim, chunk: _ChunkIndex) -> bool:
    """Numbers, plan words and framework names of the claim occur in the chunk.

    A claim naming several frameworks needs at least one of them in the chunk.
    """
    return (
        all(chunk.has_number(n) for n in claim.numbers)
        and claim.plan_words <= chunk.plan_words
        and (not claim.frameworks or bool(claim.frameworks & chunk.frameworks))
    )


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb) / denom if denom else 0.0


_CLAUSE_SPLIT_RE = re.compile(r"\s{0,3}(?:;|\u2014|\u2013|\s-\s)\s{0,3}")


def _claim_text(unit: str) -> str:
    """The claim of ``unit``: markers, emphasis and hand-off clauses removed.

    "We offer X; talk to a specialist." keeps "We offer X". A sentence with an
    absence / decline clause ("pricing isn't published here") or nothing but a
    hand-off yields ``""``: it asserts nothing a source can back, so it never
    carries a marker.
    """
    plain = _EMPHASIS_RE.sub("", strip_markers(unit))
    plain = " ".join(_BULLET_RE.sub("", plain).split())
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(plain) if c]
    if any(is_decline_sentence(c, handoff=False) for c in clauses):
        return ""
    return " ".join(c for c in clauses if not is_decline_sentence(c))


class _Verifier:
    def __init__(
        self,
        sources: Sequence[SourceChunk],
        claim_embeddings: Mapping[str, Sequence[float]] | None,
    ) -> None:
        settings = get_settings()
        self.sources = sources
        self.min_overlap = settings.citation_min_overlap
        self.min_cosine = settings.citation_min_cosine
        self.embeddings = claim_embeddings or {}
        self.index: dict[int, _ChunkIndex] = {}
        self.numbers: dict[str, int] = {}  # document_id -> new number
        self.selected: list[SourceChunk] = []

    def chunk(self, number: int) -> _ChunkIndex:
        if number not in self.index:
            self.index[number] = _ChunkIndex(self.sources[number - 1])
        return self.index[number]

    def passes(
        self, claim: _Claim, number: int, embedding: Sequence[float] | None
    ) -> bool:
        chunk = self.chunk(number)
        if _overlap(claim, chunk) < self.min_overlap:
            return False
        if not _contains_figures(claim, chunk):
            return False
        vector = chunk.source.embedding
        if embedding is not None and vector is not None:
            return _cosine(embedding, vector) >= self.min_cosine
        return True

    def verified_numbers(self, unit: str, cited: set[int]) -> set[int]:
        """The cited numbers of ``unit`` whose chunk really contains its claim."""
        text = _claim_text(unit)
        if not text:
            return set()
        claim = _Claim(text)
        embedding = self.embeddings.get(text)
        valid = {n for n in cited if 1 <= n <= len(self.sources)}
        ok = {n for n in valid if self.passes(claim, n, embedding)}
        if ok or not valid:
            return ok
        return self._reattach(claim, valid, embedding)

    def _reattach(
        self, claim: _Claim, tried: set[int], embedding: Sequence[float] | None
    ) -> set[int]:
        """The best other retrieved chunk that passes, as a one-element set."""
        best, best_overlap = 0, 0.0
        for number in range(1, len(self.sources) + 1):
            if number in tried or not self.passes(claim, number, embedding):
                continue
            overlap = _overlap(claim, self.chunk(number))
            if overlap > best_overlap:
                best, best_overlap = number, overlap
        return {best} if best else set()

    def renumber(self, numbers: list[int]) -> list[int]:
        """New, gap-free numbers (one per document, first appearance order)."""
        out: list[int] = []
        for number in numbers:
            source = self.sources[number - 1]
            new = self.numbers.get(source.document_id)
            if new is None:
                self.selected.append(source)
                new = self.numbers[source.document_id] = len(self.selected)
            if new not in out:
                out.append(new)
        return out

    def rewrite_unit(self, unit: str) -> str:
        runs = _runs(unit)
        if not runs:
            return unit
        original = {n for _, _, numbers in runs for n in numbers}
        ok = self.verified_numbers(unit, original)
        attach = sorted(ok - original)  # a re-attached chunk goes on the first run
        out: list[str] = []
        last = 0
        for start, end, numbers in runs:
            kept = [n for n in dict.fromkeys(numbers) if n in ok]
            if attach and not out:
                kept, attach = attach, []
            before = unit[last:start]
            out.append(before if kept else before.rstrip(" \t"))
            out.append("".join(f"[{n}]" for n in self.renumber(kept)))
            last = end
        out.append(unit[last:])
        return "".join(out)


def verify_and_select(
    answer: str,
    sources: Sequence[SourceChunk],
    claim_embeddings: Mapping[str, Sequence[float]] | None = None,
) -> VerifiedCitations:
    """Keep only markers whose cited chunk contains the claim; select cited sources.

    A claim keeps a marker when (a) the share of its content words found in the
    chunk is >= ``citation_min_overlap``, (b) its numbers, plan words
    (free / Starter / Professional) and framework names occur in the chunk and
    (c) when ``claim_embeddings`` (claim text -> vector) and the chunk's own
    embedding are available, their cosine is >= ``citation_min_cosine``. A claim
    whose markers all fail may be re-attached to the best other retrieved chunk
    that passes; otherwise it keeps the sentence without markers. Decline /
    absence sentences never carry markers.

    Numbers are reassigned from 1 in order of first appearance, one per
    document, so ``citations[i].n == i + 1`` and ``sources[i]`` is the chunk of
    citation ``i + 1``.
    """
    if "[" not in answer:
        return VerifiedCitations(answer)
    answer = strip_free_brackets(answer)
    verifier = _Verifier(sources, claim_embeddings)
    parts = _UNIT_SPLIT_RE.split(
        answer
    )  # claim units at even indexes, separators at odd
    rewritten = [
        verifier.rewrite_unit(part) if index % 2 == 0 else part
        for index, part in enumerate(parts)
    ]
    citations = [
        build_citation(number, source)
        for number, source in enumerate(verifier.selected, start=1)
    ]
    return VerifiedCitations("".join(rewritten), citations, verifier.selected)


def verify_citations(
    answer: str,
    sources: Sequence[SourceChunk],
    claim_embeddings: Mapping[str, Sequence[float]] | None = None,
) -> tuple[str, list[Citation]]:
    """``(clean_answer, verified_citations)``; see :func:`verify_and_select`."""
    result = verify_and_select(answer, sources, claim_embeddings)
    return result.answer, result.citations


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
