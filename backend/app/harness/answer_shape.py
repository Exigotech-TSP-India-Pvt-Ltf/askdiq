"""Rule-based question-type detection for the answer structure (OR-8).

Pure, no I/O and no LLM call: the question is lower-cased, whitespace
normalised and bounded, then matched against fixed literal prefix tables
(``str.startswith`` / ``in``, no regex), so detection is linear-time and
well under a millisecond.

Comparison reuses app.harness.ambiguity: an ambiguous comparison found by
``assess()``, or its own comparison pattern with two named subjects. After
retrieval, the shape is refined from signals that already exist: no usable
context means out of scope; context that covers only some of the named
subjects means partial coverage.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from app.harness.ambiguity import _COMPARISON_RE, AmbiguityKind, Assessment, _subjects
from app.schemas.query import SourceChunk

# Only the start of a question decides its type; bound before any work.
_MAX_SCAN_CHARS = 400
# Per-source text scanned for subject coverage.
_MAX_SOURCE_CHARS = 4000
_MIN_SUBJECTS = 2
# Folded clarification answers: "(Framework: ISO 27001; Maturity: Partly ...)".
_MAX_ANSWERS_CHARS = 600
_MAX_LABEL_CHARS = 30
# Generic label/value words that are never subjects the documents must name.
_LABEL_WORDS = frozenset(
    {"framework", "maturity", "deadline", "environment", "partly", "mostly"}
)


class AnswerShape(StrEnum):
    DEFINITION = "definition"
    HOW_TO = "how_to"
    COMPARISON = "comparison"
    QUICK_FACT = "quick_fact"
    PARTIAL = "partial"
    OUT_OF_SCOPE = "out_of_scope"
    GENERAL = "general"


# Polite lead-ins that carry no type ("can you tell me what is X").
_LEAD_INS: tuple[str, ...] = (
    "can you please tell me ",
    "could you please tell me ",
    "can you tell me ",
    "could you tell me ",
    "please tell me ",
    "i want to know ",
    "i'd like to know ",
    "please ",
    "so ",
    "ok ",
    "okay ",
)

# Anywhere in the question: an explicit comparison.
_COMPARISON_MARKERS: tuple[str, ...] = (
    " vs ",
    " vs. ",
    " versus ",
    "difference between",
    "differences between",
    "different from",
    "compare ",
    "comparison",
    "compared to",
    "compared with",
    "better than",
    "which is better",
    "pros and cons",
)

# Checked in order; how-to first so "what are the steps" isn't a definition.
_HOW_TO_PREFIXES: tuple[str, ...] = (
    "how do i ",
    "how do we ",
    "how do you ",
    "how can i ",
    "how can we ",
    "how should i ",
    "how should we ",
    "how to ",
    "how does ",
    "what are the steps",
    "what steps ",
    "steps to ",
    "set up ",
    "setup ",
    "configure ",
    "walk me through ",
    "guide me ",
)

_DEFINITION_PREFIXES: tuple[str, ...] = (
    "what is ",
    "what's ",
    "whats ",
    "what are ",
    "define ",
    "definition of ",
    "meaning of ",
    "explain ",
    "describe ",
    "tell me about ",
    "who is ",
    "who are ",
)
# "what does X mean / stand for" is a definition; "what does X cost" isn't.
_WHAT_DOES = "what does "
_DEFINITION_ENDINGS: tuple[str, ...] = ("mean", "stand for", "do")

_QUICK_FACT_PREFIXES: tuple[str, ...] = (
    "how much ",
    "how many ",
    "how long ",
    "is ",
    "are ",
    "can ",
    "does ",
    "do ",
    "will ",
    "should ",
    "did ",
    "was ",
    "has ",
    "have ",
    "when ",
    "where ",
)


# Prefix tables in priority order ("what does" is handled after these).
_PREFIX_TABLE: tuple[tuple[tuple[str, ...], AnswerShape], ...] = (
    (_HOW_TO_PREFIXES, AnswerShape.HOW_TO),
    (_DEFINITION_PREFIXES, AnswerShape.DEFINITION),
    (_QUICK_FACT_PREFIXES, AnswerShape.QUICK_FACT),
)


def _normalise(query: str) -> str:
    text = " ".join(query[:_MAX_SCAN_CHARS].split()).lower()
    for lead in _LEAD_INS:
        if text.startswith(lead):
            text = text[len(lead) :]
            break
    return text


def _is_comparison(query: str, text: str, assessment: Assessment | None) -> bool:
    if assessment is not None and assessment.kind is AmbiguityKind.COMPARISON:
        return True
    padded = f" {text.rstrip('?.! ')} "
    if any(marker in padded for marker in _COMPARISON_MARKERS):
        return True
    bounded = query[:_MAX_SCAN_CHARS]
    return (
        _COMPARISON_RE.search(bounded) is not None
        and len(_subjects(bounded, brand=False)) >= _MIN_SUBJECTS
    )


def detect_shape(query: str, assessment: Assessment | None = None) -> AnswerShape:
    """Pre-retrieval answer shape for ``query`` (pure, < 1 ms)."""

    text = _normalise(query)
    if not text:
        return AnswerShape.GENERAL
    if _is_comparison(query, text, assessment):
        return AnswerShape.COMPARISON
    return _prefix_shape(text)


def _prefix_shape(text: str) -> AnswerShape:
    for prefixes, shape in _PREFIX_TABLE:
        if text.startswith(prefixes):
            return shape
    if text.startswith(_WHAT_DOES):
        stem = text.rstrip("?.! ")
        definition = stem.endswith(_DEFINITION_ENDINGS)
        return AnswerShape.DEFINITION if definition else AnswerShape.QUICK_FACT
    return AnswerShape.GENERAL


def _source_text(source: SourceChunk) -> str:
    metadata = source.metadata or {}
    names = " ".join(
        str(metadata.get(key) or "")
        for key in ("source_name", "file_name", "title", "section_heading")
    )
    return f"{names} {source.content[:_MAX_SOURCE_CHARS]}".lower()


def strip_answers(query: str) -> str:
    """Drop a trailing "(Label: value; Label: value)" clarification suffix.

    A folded reply is "original question (Framework: ISO 27001; Maturity:
    Partly implemented)": the labels and chosen values are not subjects the
    documents must name. Linear: one ``rfind``, no backtracking pattern.
    """
    text = query.rstrip()
    if not text.endswith(")"):
        return query
    start = text.rfind("(")
    if start < 0 or len(text) - start > _MAX_ANSWERS_CHARS:
        return query
    inner = text[start + 1 : -1]
    parts = [p for p in inner.split(";") if p.strip()]
    labelled = bool(parts) and all(0 < p.find(":") <= _MAX_LABEL_CHARS for p in parts)
    return text[:start].rstrip() if labelled else query


def _covered(subject: str, texts: Sequence[str]) -> bool:
    needle = subject.lower()
    return any(needle in text for text in texts)


def refine_shape(
    shape: AnswerShape,
    query: str,
    sources: Sequence[SourceChunk],
    *,
    min_score: float | None = None,
) -> AnswerShape:
    """Post-retrieval override: out of scope, or partial coverage."""

    usable = [s for s in sources if min_score is None or float(s.score) >= min_score]
    if not usable:
        return AnswerShape.OUT_OF_SCOPE
    asked = _subjects(strip_answers(query[:_MAX_SCAN_CHARS]), brand=False)
    asked -= _LABEL_WORDS
    if len(asked) < _MIN_SUBJECTS:
        return shape
    texts = [_source_text(s) for s in usable]
    covered = sum(1 for subject in asked if _covered(subject, texts))
    if 0 < covered < len(asked):
        return AnswerShape.PARTIAL
    return shape
