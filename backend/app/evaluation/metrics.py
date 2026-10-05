"""Pure scoring helpers for the RAG evaluation harness.

No I/O and no pipeline imports, so everything here is unit-testable offline
(see tests/test_eval_metrics.py).

Retrieval metrics work on the *ranked list of retrieved chunks*, in the order
the pipeline returned them. A chunk is "relevant" when its ``source_name`` is
one of the case's ``expected_docs`` (and, optionally, its ``section_heading``
contains one of the ``expected_headings``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def chunk_relevance(
    ranked_docs: Sequence[str | None],
    ranked_headings: Sequence[str | None],
    expected_docs: Sequence[str],
    expected_headings: Sequence[str] | None = None,
) -> list[bool]:
    """One bool per retrieved chunk, in rank order: is this chunk relevant?

    - Doc check: ``source_name`` must be in ``expected_docs`` (skipped when
      ``expected_docs`` is empty).
    - Heading check: ``section_heading`` must contain (case-insensitive) at
      least one of ``expected_headings`` (skipped when none are given).
    """

    wanted_docs = set(expected_docs)
    wanted_headings = [h.lower() for h in (expected_headings or [])]

    flags: list[bool] = []
    for doc, heading in zip(ranked_docs, ranked_headings):
        doc_ok = (doc in wanted_docs) if wanted_docs else True
        heading_ok = (
            any(h in (heading or "").lower() for h in wanted_headings)
            if wanted_headings
            else True
        )
        flags.append(doc_ok and heading_ok)
    return flags


def doc_recall_at_k(
    ranked_docs: Sequence[str | None],
    expected_docs: Sequence[str],
    k: int,
    min_hits: int = 1,
) -> float:
    """Graded recall@k over distinct expected documents.

    ``expected_docs`` in this project is an "any of" list: a case only needs
    ``min_hits`` of them. So recall is ``distinct expected docs found in the
    top-k chunks / min_hits``, capped at 1.0. A single-doc case scores 0 or 1;
    a compound case with ``min_hits=2`` scores 0.5 when only one doc is found.
    """

    if not expected_docs:
        return 1.0
    found = len(set(ranked_docs[:k]) & set(expected_docs))
    return min(1.0, found / max(1, min_hits))


def precision_at_k(flags: Sequence[bool], k: int) -> float:
    """Share of the top-k retrieved chunks that are relevant.

    The denominator is ``min(k, chunks actually retrieved)`` so a pipeline that
    returns fewer than k chunks is not penalised twice. 0.0 if nothing was
    retrieved.
    """

    top = list(flags[:k])
    if not top:
        return 0.0
    return sum(top) / len(top)


def reciprocal_rank(flags: Sequence[bool]) -> float:
    """1 / rank of the first relevant chunk (1-indexed); 0.0 if none."""

    for index, flag in enumerate(flags):
        if flag:
            return 1.0 / (index + 1)
    return 0.0


def ndcg_at_k(flags: Sequence[bool], k: int) -> float:
    """Binary-relevance nDCG@k over the retrieved chunks.

    The ideal ordering is "all relevant chunks that were retrieved in the
    top-k come first", so this measures *ordering quality* (are relevant
    chunks ranked above irrelevant ones?). It is 0.0 when no relevant chunk is
    in the top-k - use recall@k to catch that case.
    """

    top = list(flags[:k])
    relevant = sum(top)
    if relevant == 0:
        return 0.0

    dcg = sum(1.0 / math.log2(i + 2) for i, flag in enumerate(top) if flag)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(relevant))
    return dcg / ideal


# ---------------------------------------------------------------------------
# Answer-text checks
# ---------------------------------------------------------------------------


def _matches(text: str, spec: str) -> bool:
    """``spec`` may hold alternatives separated by ``|`` (any one matches)."""

    lowered = text.lower()
    return any(alt.strip().lower() in lowered for alt in spec.split("|") if alt.strip())


def missing_keywords(text: str, specs: Iterable[str]) -> list[str]:
    """Specs that do NOT appear in ``text`` (case-insensitive substring).

    Example spec: ``"audit-ready|audit ready"`` matches either spelling.
    """

    return [spec for spec in specs if not _matches(text, spec)]


def forbidden_found(text: str, specs: Iterable[str]) -> list[str]:
    """Specs that DO appear in ``text`` (used for hallucination guards)."""

    return [spec for spec in specs if _matches(text, spec)]


def word_count(text: str) -> int:
    return len(text.split())


def mean_or_none(values: Iterable[float | None]) -> float | None:
    """Mean of the non-None values, or None if there are none."""

    kept = [v for v in values if v is not None]
    if not kept:
        return None
    return sum(kept) / len(kept)