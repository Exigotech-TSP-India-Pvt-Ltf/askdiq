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
import re
from collections.abc import Iterable, Sequence
from functools import lru_cache


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
    for doc, heading in zip(ranked_docs, ranked_headings, strict=False):
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
    """True recall@k over distinct expected documents.

    ``distinct expected docs found in the top-k chunks / distinct expected
    docs``. ``min_hits`` is accepted for call compatibility but no longer
    inflates the number: ``expected_docs`` is an "any of" list, so a case that
    needs one of three docs scores 1/3 here even though it passed. Use
    :func:`doc_hits_met_at_k` for "did the case get what it needs".
    """

    del min_hits  # recall is defined by the expected set, not the pass bar
    wanted = set(expected_docs)
    if not wanted:
        return 1.0
    return len(set(ranked_docs[:k]) & wanted) / len(wanted)


def doc_hits_met_at_k(
    ranked_docs: Sequence[str | None],
    expected_docs: Sequence[str],
    k: int,
    min_hits: int = 1,
) -> float:
    """Graded "requirement met" score: ``found / min_hits`` capped at 1.0.

    This is the former ``doc_recall_at_k`` semantics. It is not recall (it can
    reach 1.0 while most expected docs are missing), so it has its own name.
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


def hit_rate_at_k(flags: Sequence[bool], k: int) -> float:
    """1.0 if at least one relevant chunk is in the top-k, else 0.0."""

    return 1.0 if any(flags[:k]) else 0.0


def context_coverage(
    context_chunks: Iterable[str], key_facts: Iterable[str]
) -> float | None:
    """Share of expected key facts present anywhere in the retrieved context.

    None when the case lists no key facts. Facts use the same ``a|b``
    alternative syntax as ``expected_keywords``.
    """

    facts = list(key_facts)
    if not facts:
        return None
    context = "\n".join(context_chunks)
    return sum(1 for fact in facts if _matches(context, fact)) / len(facts)


def noise_ratio(flags: Sequence[bool], k: int) -> float:
    """Share of the top-k retrieved chunks that are irrelevant (1 - precision)."""

    top = list(flags[:k])
    if not top:
        return 0.0
    return 1.0 - sum(top) / len(top)


# ---------------------------------------------------------------------------
# Answer-text checks
# ---------------------------------------------------------------------------


_NORMALIZE = str.maketrans(
    {
        "\u2019": "'",
        "\u2018": "'",
        "\u02bc": "'",
        "\u2011": "-",
        "\u2010": "-",
        "\u2013": "-",
    }
)
_REGEX_PREFIX = "re:"
_CLAIM_PREFIX = "claim:"
_MAX_REGEX_LEN = 200
_NEGATORS = frozenset(
    {"not", "no", "never", "cannot", "isn't", "aren't", "doesn't", "don't", "won't"}
)
_NEGATION_WINDOW = 3
_NEGATION_LOOKBACK = 64
_CONTRAST = frozenset({"only", "just", "but"})
_CLAUSE_BREAK = ".,;:!?()\n"


def _norm(text: str) -> str:
    return text.lower().translate(_NORMALIZE)


@lru_cache(maxsize=512)
def _alt_pattern(alt: str) -> re.Pattern[str]:
    """Whole-word pattern for one (already normalised) alternative.

    A boundary is enforced on each side whose edge character is a word
    character, so ``free`` does not match ``freedom``. A trailing ``*`` makes
    it a prefix match (``accountab*`` matches ``accountability``); otherwise a
    plural ``s``/``es`` is accepted (``backup`` matches ``backups``). Built
    from ``re.escape`` plus fixed lookarounds, so matching is linear-time.
    """

    prefix = alt.endswith("*")
    body = alt[:-1] if prefix else alt
    left = r"(?<!\w)" if body[:1].isalnum() else ""
    right = "" if prefix or not body[-1:].isalnum() else r"(?:s|es)?(?!\w)"
    return re.compile(f"{left}{re.escape(body)}{right}")


@lru_cache(maxsize=128)
def _regex_spec(spec: str) -> re.Pattern[str] | None:
    body = spec[len(_REGEX_PREFIX) :]
    if not body or len(body) > _MAX_REGEX_LEN:
        return None
    try:
        return re.compile(body, re.IGNORECASE)
    except re.error:
        return None


def _is_negated(lowered: str, start: int) -> bool:
    """True when the match at ``start`` is directly preceded by a negator.

    Looks at the three words before it within the same clause, so ``not
    free``, ``is not currently free`` and ``not certified or guaranteed`` are
    negated, but ``not only free`` and a negator in an earlier sentence are
    not.
    """

    before = lowered[max(0, start - _NEGATION_LOOKBACK) : start]
    for ch in _CLAUSE_BREAK:
        before = before.rsplit(ch, 1)[-1]
    words = before.split()[-_NEGATION_WINDOW:]
    negators = [i for i, w in enumerate(words) if w in _NEGATORS or w.endswith("n't")]
    return bool(negators) and not _CONTRAST & set(words[negators[-1] + 1 :])


def _matches(text: str, spec: str, *, negation_aware: bool = False) -> bool:
    """Whole-word match of ``spec`` in ``text``.

    ``spec`` may hold alternatives separated by ``|`` (any one matches); an
    alternative ending in ``*`` is a prefix match. A spec starting with
    ``re:`` is a raw case-insensitive regex (not split on ``|``); one starting
    with ``claim:`` is always negation-aware. With ``negation_aware`` an
    occurrence preceded by a negator (``not free``) does not count.
    """

    if spec.startswith(_REGEX_PREFIX):
        pattern = _regex_spec(spec)
        return bool(pattern and pattern.search(text))
    if spec.startswith(_CLAIM_PREFIX):
        spec, negation_aware = spec[len(_CLAIM_PREFIX) :], True
    lowered = _norm(text)
    for raw in spec.split("|"):
        alt = _norm(raw.strip())
        if not alt:
            continue
        for found in _alt_pattern(alt).finditer(lowered):
            if not negation_aware or not _is_negated(lowered, found.start()):
                return True
    return False


def missing_keywords(text: str, specs: Iterable[str]) -> list[str]:
    """Specs that do NOT appear in ``text`` as a whole word (case-insensitive).

    Example spec: ``"audit-ready|audit ready"`` matches either spelling. An
    occurrence directly negated (``not free``) does not satisfy the spec.
    """

    return [spec for spec in specs if not _matches(text, spec, negation_aware=True)]


def forbidden_found(text: str, specs: Iterable[str]) -> list[str]:
    """Specs that DO appear in ``text`` (used for hallucination guards).

    Matching is literal and whole-word. Prefix a spec with ``claim:`` when the
    word is only a violation as an assertion: ``claim:guaranteed`` ignores
    ``not guaranteed`` (a refusal, not a claim).
    """

    return [spec for spec in specs if _matches(text, spec)]


def word_count(text: str) -> int:
    return len(text.split())


def mean_or_none(values: Iterable[float | None]) -> float | None:
    """Mean of the non-None values, or None if there are none."""

    kept = [v for v in values if v is not None]
    if not kept:
        return None
    return sum(kept) / len(kept)
