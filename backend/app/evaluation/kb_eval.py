"""Knowledge-base and ingestion evaluation.

Chunks every source document with the real chunking strategy (no embeddings,
no DB needed) and scores parsing, structure preservation, duplicates, missing
documents, chunk size/overlap, metadata, split facts, access-control metadata,
and version/freshness. ``evaluate_ingested`` additionally compares what is
actually stored in the DB against the source files (staleness, freshness delay).

The check functions are pure and unit-tested (tests/test_eval_kb.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from statistics import mean

ACCESS_KEYS = ("access_level", "allowed_roles", "tenant_id", "visibility")
VERSION_KEYS = ("version", "last_updated", "updated_at", "effective_date")
MOJIBAKE = ("\u00e2\u20ac", "\u00c3\u00a2", "\ufffd")  # 'â€', 'Ã¢', replacement char
NEAR_DUP_THRESHOLD = 0.85
MIN_CHUNK_CHARS = 120
_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class KBChunk:
    doc: str
    index: int
    content: str
    metadata: dict = field(default_factory=dict)


def _norm(text: str) -> str:
    """Lowercase, drop markdown emphasis/bullets/pipes, collapse whitespace."""

    text = re.sub(r"[*_`#>|]", " ", text.lower())
    text = re.sub(r"^\s*(?:[-+]|\d+\.)\s+", " ", text, flags=re.MULTILINE)
    return " ".join(text.split())


def _strip_section_prefix(chunk: KBChunk) -> str:
    return re.sub(r"^Section:[^\n]*\n+", "", chunk.content)


def _ratio(num: int, den: int) -> float:
    return num / den if den else 1.0


# --------------------------------------------------------------------------
# 1. Parsing accuracy
# --------------------------------------------------------------------------


def parsing_accuracy(source: str, chunks: Sequence[KBChunk]) -> dict:
    """Share of source words that survive into chunks, plus artifact detection."""

    src_words = _WORD.findall(_norm(source))
    chunk_text = _norm(" ".join(_strip_section_prefix(c) for c in chunks))
    chunk_words = _WORD.findall(chunk_text)
    remaining: dict[str, int] = {}
    for w in chunk_words:
        remaining[w] = remaining.get(w, 0) + 1
    kept = 0
    for w in src_words:
        if remaining.get(w, 0) > 0:
            remaining[w] -= 1
            kept += 1
    joined = "\n".join(c.content for c in chunks)
    artifacts = [m for m in MOJIBAKE if m in joined or m in source]
    if "__CODEBLOCK_" in joined:
        artifacts.append("unrestored_codeblock_placeholder")
    return {"word_coverage": _ratio(kept, len(src_words)), "artifacts": artifacts}


# --------------------------------------------------------------------------
# 2. Structure preservation (headings, tables, lists, metadata)
# --------------------------------------------------------------------------

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)
_TABLE_ROW = re.compile(r"^\s*\|.+\|\s*$", re.MULTILINE)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(\S.*)$", re.MULTILINE)


def structure_preservation(source: str, chunks: Sequence[KBChunk]) -> dict:
    corpus = _norm(" ".join(c.content for c in chunks))
    headings = [_norm(h) for h in _HEADING.findall(source)]
    rows = [_norm(r) for r in _TABLE_ROW.findall(source) if not set(r) <= set("|-: \t")]
    items = [_norm(i) for i in _LIST_ITEM.findall(source)]

    def kept(parts: list[str]) -> float | None:
        parts = [p for p in parts if p]
        return (
            _ratio(sum(1 for p in parts if p in corpus), len(parts)) if parts else None
        )

    return {
        "headings": kept(headings),
        "tables": kept(rows),
        "lists": kept(items),
        "counts": {
            "headings": len(headings),
            "table_rows": len(rows),
            "list_items": len(items),
        },
    }


def metadata_correctness(source: str, chunks: Sequence[KBChunk]) -> dict:
    """Required keys present, contiguous indexes, and headings that exist in the source."""

    source_headings = {_norm(h) for h in _HEADING.findall(source)}
    problems: list[str] = []
    for expected, c in enumerate(chunks):
        md = c.metadata
        for key in ("strategy", "document_id"):
            if not md.get(key):
                problems.append(f"{c.doc}#{c.index}: missing {key}")
        if c.index != expected:
            problems.append(f"{c.doc}#{c.index}: non-contiguous chunk_index")
        heading = md.get("section_heading")
        if heading and source_headings and _norm(heading) not in source_headings:
            problems.append(f"{c.doc}#{c.index}: heading {heading!r} not in source")
    return {
        "ok_rate": _ratio(
            len(chunks) - len({p.split(":")[0] for p in problems}), len(chunks)
        ),
        "problems": problems,
    }


# --------------------------------------------------------------------------
# 3. Duplicates, missing documents
# --------------------------------------------------------------------------


def _shingles(text: str, n: int = 5) -> set[tuple[str, ...]]:
    words = _WORD.findall(_norm(text))
    return {tuple(words[i : i + n]) for i in range(max(1, len(words) - n + 1))}


def find_duplicates(
    chunks: Sequence[KBChunk], threshold: float = NEAR_DUP_THRESHOLD
) -> dict:
    """Exact (same normalized text) and near-duplicate (shingle Jaccard) chunk pairs."""

    exact: list[tuple[str, str]] = []
    near: list[tuple[str, str, float]] = []
    seen: dict[str, KBChunk] = {}
    sh = [(_shingles(_strip_section_prefix(c)), c) for c in chunks]
    for c in chunks:
        key = hashlib.sha256(_norm(_strip_section_prefix(c)).encode()).hexdigest()
        if key in seen:
            exact.append((f"{seen[key].doc}#{seen[key].index}", f"{c.doc}#{c.index}"))
        else:
            seen[key] = c
    exact_pairs = set(exact)
    for i in range(len(sh)):
        for j in range(i + 1, len(sh)):
            a, b = sh[i][0], sh[j][0]
            if not a or not b:
                continue
            score = len(a & b) / len(a | b)
            pair = (
                f"{sh[i][1].doc}#{sh[i][1].index}",
                f"{sh[j][1].doc}#{sh[j][1].index}",
            )
            if score >= threshold and pair not in exact_pairs:
                near.append((*pair, round(score, 3)))
    return {
        "exact": exact,
        "near": near,
        "duplicate_rate": _ratio(len(exact) + len(near), max(1, len(chunks))),
    }


def missing_documents(expected: Iterable[str], present: Iterable[str]) -> dict:
    expected_set, present_set = set(expected), set(present)
    return {
        "missing": sorted(expected_set - present_set),
        "unexpected": sorted(present_set - expected_set),
        "coverage": _ratio(len(expected_set & present_set), len(expected_set)),
    }


# --------------------------------------------------------------------------
# 4. Chunk size and overlap, split facts
# --------------------------------------------------------------------------


def chunk_size_stats(chunks: Sequence[KBChunk], max_chars: int = 2000) -> dict:
    sizes = [len(c.content) for c in chunks]
    if not sizes:
        return {"n": 0}
    return {
        "n": len(sizes),
        "min_chars": min(sizes),
        "mean_chars": round(mean(sizes), 1),
        "max_chars": max(sizes),
        "oversize_rate": _ratio(sum(1 for s in sizes if s > max_chars), len(sizes)),
        "tiny_rate": _ratio(sum(1 for s in sizes if s < MIN_CHUNK_CHARS), len(sizes)),
    }


def chunk_overlap(chunks: Sequence[KBChunk], max_words: int = 80) -> dict:
    """Mean fraction of a chunk's leading words repeated from the previous chunk's tail."""

    ratios = []
    for prev, cur in pairwise(chunks):
        if prev.doc != cur.doc:
            continue
        a, b = (
            _WORD.findall(_norm(prev.content)),
            _WORD.findall(_norm(_strip_section_prefix(cur))),
        )
        best = 0
        for n in range(1, min(len(a), len(b), max_words) + 1):
            if a[-n:] == b[:n]:
                best = n
        ratios.append(best / max(1, min(len(b), max_words)))
    return {
        "mean_overlap_ratio": round(mean(ratios), 3) if ratios else 0.0,
        "pairs": len(ratios),
    }


def split_facts(source: str, chunks: Sequence[KBChunk]) -> dict:
    """Source paragraphs/list blocks that no single chunk contains in full.

    Such a block was cut across chunks; ``cross_section`` marks the ones whose
    pieces landed in chunks with different section headings (unrelated chunks).
    """

    chunk_norms = [_norm(c.content) for c in chunks]
    blocks = [
        b for b in re.split(r"\n\s*\n", source) if len(_WORD.findall(_norm(b))) >= 8
    ]
    split: list[str] = []
    cross_section = 0
    for block in blocks:
        nb = _norm(block)
        if any(nb in cn for cn in chunk_norms):
            continue
        split.append(nb[:80])
        words = set(_WORD.findall(nb))
        homes = [
            c
            for c, cn in zip(chunks, chunk_norms)
            if len(words & set(_WORD.findall(cn))) >= 0.3 * len(words)
        ]
        if len({c.metadata.get("section_heading") for c in homes}) > 1:
            cross_section += 1
    return {
        "blocks": len(blocks),
        "split": split,
        "split_rate": _ratio(len(split), len(blocks)) if blocks else 0.0,
        "cross_section_splits": cross_section,
    }


# --------------------------------------------------------------------------
# 5. Access control, version and freshness
# --------------------------------------------------------------------------


def access_control_coverage(metadatas: Sequence[dict]) -> dict:
    tagged = sum(
        1
        for md in metadatas
        if any(md.get(k) not in (None, "", []) for k in ACCESS_KEYS)
    )
    return {
        "coverage": _ratio(tagged, len(metadatas)),
        "tagged": tagged,
        "total": len(metadatas),
    }


def version_metadata(
    source: str, metadata: dict, mtime: datetime | None, now: datetime
) -> dict:
    declared = any(metadata.get(k) for k in VERSION_KEYS) or bool(
        re.search(r"(?i)\b(last updated|version|effective)\b[: ]+\S+", source)
    )
    age_days = (now - mtime).days if mtime else None
    return {"declares_version": declared, "age_days": age_days}


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _read_corpus(
    source_dir: Path, filenames: Sequence[str] | None = None
) -> dict[str, str]:
    names = filenames or sorted(p.name for p in source_dir.glob("*.md"))
    return {
        n: (source_dir / n).read_text(encoding="utf-8")
        for n in names
        if (source_dir / n).exists()
    }


async def load_corpus(
    source_dir: Path, filenames: Sequence[str] | None = None
) -> dict[str, str]:
    return await asyncio.to_thread(_read_corpus, source_dir, filenames)


async def chunk_corpus(
    corpus: dict[str, str], chunker_factory: Callable[[], object]
) -> tuple[dict[str, list[KBChunk]], dict[str, float]]:
    """Chunk each document; returns chunks and per-document chunking time in ms."""

    chunker = chunker_factory()
    out: dict[str, list[KBChunk]] = {}
    timings: dict[str, float] = {}
    for name, text in corpus.items():
        t0 = time.perf_counter()
        results = await chunker.chunk(text, source_metadata={"document_id": name})
        timings[name] = (time.perf_counter() - t0) * 1000
        out[name] = [
            KBChunk(name, r.chunk_index, r.content, dict(r.metadata)) for r in results
        ]
    return out, timings


THRESHOLDS = {
    "word_coverage": 0.95,
    "structure": 0.95,
    "duplicate_rate": 0.0,
    "split_rate": 0.1,
    "access_coverage": 1.0,
}


def evaluate_kb(
    corpus: dict[str, str],
    chunks_by_doc: dict[str, list[KBChunk]],
    expected_docs: Iterable[str],
    mtimes: dict[str, datetime] | None = None,
    now: datetime | None = None,
    timings_ms: dict[str, float] | None = None,
    max_chunk_chars: int = 2000,
) -> dict:
    now = now or datetime.now(UTC)
    all_chunks = [c for cs in chunks_by_doc.values() for c in cs]
    per_doc: dict[str, dict] = {}
    for name, text in corpus.items():
        cs = chunks_by_doc.get(name, [])
        per_doc[name] = {
            "parsing": parsing_accuracy(text, cs),
            "structure": structure_preservation(text, cs),
            "metadata": metadata_correctness(text, cs),
            "split_facts": split_facts(text, cs),
            "size": chunk_size_stats(cs, max_chunk_chars),
            "overlap": chunk_overlap(cs),
            "version": version_metadata(
                text, cs[0].metadata if cs else {}, (mtimes or {}).get(name), now
            ),
        }

    def avg(values: list[float | None]) -> float | None:
        kept = [v for v in values if v is not None]
        return round(mean(kept), 4) if kept else None

    dups = find_duplicates(all_chunks)
    access = access_control_coverage([c.metadata for c in all_chunks])
    summary = {
        "documents": len(corpus),
        "chunks": len(all_chunks),
        "missing_documents": missing_documents(expected_docs, corpus),
        "parsing_word_coverage": avg(
            [d["parsing"]["word_coverage"] for d in per_doc.values()]
        ),
        "parsing_artifacts": {
            n: d["parsing"]["artifacts"]
            for n, d in per_doc.items()
            if d["parsing"]["artifacts"]
        },
        "heading_preservation": avg(
            [d["structure"]["headings"] for d in per_doc.values()]
        ),
        "table_preservation": avg([d["structure"]["tables"] for d in per_doc.values()]),
        "list_preservation": avg([d["structure"]["lists"] for d in per_doc.values()]),
        "metadata_ok_rate": avg([d["metadata"]["ok_rate"] for d in per_doc.values()]),
        "metadata_problems": [
            p for d in per_doc.values() for p in d["metadata"]["problems"]
        ],
        "duplicates": dups,
        "chunk_size": chunk_size_stats(all_chunks, max_chunk_chars),
        "mean_overlap_ratio": avg(
            [d["overlap"]["mean_overlap_ratio"] for d in per_doc.values()]
        ),
        "split_fact_rate": avg(
            [d["split_facts"]["split_rate"] for d in per_doc.values()]
        ),
        "cross_section_splits": sum(
            d["split_facts"]["cross_section_splits"] for d in per_doc.values()
        ),
        "access_control": access,
        "docs_without_version": sorted(
            n for n, d in per_doc.items() if not d["version"]["declares_version"]
        ),
        "indexing_delay_ms": {
            "total": round(sum((timings_ms or {}).values()), 1),
            "max_doc": round(max((timings_ms or {"": 0}).values()), 1),
        },
    }
    findings = []
    if summary["missing_documents"]["missing"]:
        findings.append(f"missing documents: {summary['missing_documents']['missing']}")
    if (summary["parsing_word_coverage"] or 1) < THRESHOLDS["word_coverage"]:
        findings.append("parsing word coverage below threshold")
    for label, key in (
        ("headings", "heading_preservation"),
        ("tables", "table_preservation"),
        ("lists", "list_preservation"),
    ):
        if (summary[key] if summary[key] is not None else 1) < THRESHOLDS["structure"]:
            findings.append(f"{label} not preserved")
    if dups["exact"] or dups["near"]:
        findings.append(
            f"{len(dups['exact'])} exact / {len(dups['near'])} near-duplicate chunk pairs"
        )
    if summary["metadata_problems"]:
        findings.append(f"{len(summary['metadata_problems'])} metadata problems")
    if (summary["split_fact_rate"] or 0) > THRESHOLDS["split_rate"]:
        findings.append("facts split across chunks above threshold")
    if access["coverage"] < THRESHOLDS["access_coverage"]:
        findings.append(
            f"access-control metadata on {access['tagged']}/{access['total']} chunks"
        )
    if summary["docs_without_version"]:
        findings.append(
            f"{len(summary['docs_without_version'])} documents declare no version/last-updated"
        )
    return {"summary": summary, "findings": findings, "per_document": per_doc}


async def evaluate_ingested(
    db,
    corpus: dict[str, str],
    mtimes: dict[str, datetime],
    hash_fn: Callable[[str], str],
) -> dict:
    """Compare stored documents to the source files: staleness, duplicate live
    versions, empty documents, access metadata, and freshness delay."""

    from sqlalchemy import func, select

    from app.models.db_models import Chunk, Document

    docs = (await db.execute(select(Document))).scalars().all()
    counts = dict(
        (
            await db.execute(
                select(Chunk.document_id, func.count()).group_by(Chunk.document_id)
            )
        ).all()
    )
    by_name: dict[str, list] = {}
    for d in docs:
        by_name.setdefault(d.source_name, []).append(d)

    stale, empty, freshness = [], [], {}
    for name, text in corpus.items():
        versions = by_name.get(name, [])
        if not versions:
            continue
        newest = max(versions, key=lambda d: d.created_at)
        if newest.content_hash != hash_fn(text):
            stale.append(name)
        if mtimes.get(name) and mtimes[name] > newest.created_at:
            freshness[name] = round(
                (mtimes[name] - newest.created_at).total_seconds() / 3600, 2
            )
    empty = [d.source_name for d in docs if not counts.get(d.id)]
    return {
        "stored_documents": len(docs),
        "missing_from_db": sorted(set(corpus) - set(by_name)),
        "stale_documents": stale,
        "multiple_live_versions": sorted(n for n, v in by_name.items() if len(v) > 1),
        "documents_without_chunks": empty,
        "freshness_delay_hours": freshness,
        "access_control": access_control_coverage([d.doc_metadata or {} for d in docs]),
    }
