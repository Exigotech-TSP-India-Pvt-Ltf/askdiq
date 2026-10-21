"""Conservative post-filter for stock AI openers and closers (OR-8).

Only the very start and the very end of an answer are touched: a known stock
opener is cut from the first sentence, and a closing sentence that is pure
boilerplate is dropped. Middle sentences are never rewritten (that would risk
the grounding), and a closer that carries anything fact-like (a digit, an
address, a link or a citation marker) is kept.

Matching is ``str.startswith`` against fixed literals and ``str.rfind``
scans, so the filter is linear-time on any input (no regex).
"""

from __future__ import annotations

from app.core.logging_config import get_logger
from app.harness.style import ResponseStyle

logger = get_logger(__name__)

# Stock openers cut from the start; the rest of the sentence is kept.
_OPENERS: tuple[str, ...] = (
    "great question! ",
    "great question. ",
    "great question, ",
    "certainly! ",
    "certainly, ",
    "certainly. ",
    "absolutely! ",
    "sure! ",
    "as an ai, ",
    "as an ai assistant, ",
    "based on the information provided, ",
    "based on the provided information, ",
    "based on the provided context, ",
    "based on the context provided, ",
    "based on the context, ",
    "according to the documents, ",
    "according to the provided documents, ",
)

# Closing sentences dropped when they are only boilerplate.
_CLOSERS: tuple[str, ...] = (
    "i hope this helps",
    "hope this helps",
    "feel free to reach out",
    "feel free to ask",
    "let me know if you have any",
    "let me know if you need",
    "if you have any other questions",
    "if you have any more questions",
    "if you have any further questions",
)
# A closer with any of these may carry a fact or a citation: keep it.
_FACT_MARKS: tuple[str, ...] = ("@", "http", "www.", "[", "(", ":")
_MAX_CLOSER_WORDS = 20
_SENTENCE_ENDS: tuple[str, ...] = (". ", "! ", "? ", "\n")
_MAX_PROSE_SENTENCES = 2
_LIST_STARTS: tuple[str, ...] = ("- ", "* ", "• ", "#", "|")
# A "Next step:" call to action is its own block, not prose.
_NEXT_STEP = "next step"


def _cut_opener(text: str) -> str:
    lowered = text.lower()
    for opener in _OPENERS:
        if lowered.startswith(opener):
            rest = text[len(opener) :].lstrip()
            return rest[:1].upper() + rest[1:] if rest else text
    return text


def _closer_start(text: str) -> int:
    """Index where the last sentence starts (0 for a single sentence)."""

    # Ignore the final punctuation mark itself when looking for the boundary.
    search = text.rstrip()[:-1]
    start = 0
    for end in _SENTENCE_ENDS:
        index = search.rfind(end)
        if index >= 0:
            start = max(start, index + len(end))
    return start


def _is_boilerplate(sentence: str) -> bool:
    lowered = sentence.strip().lower()
    if not lowered.startswith(_CLOSERS):
        return False
    if any(mark in lowered for mark in _FACT_MARKS):
        return False
    if any(ch.isdigit() for ch in lowered):
        return False
    return len(lowered.split()) <= _MAX_CLOSER_WORDS


def _cut_closer(text: str) -> str:
    start = _closer_start(text)
    if start == 0:
        return text
    if not _is_boilerplate(text[start:]):
        return text
    return text[:start].rstrip()


def strip_stock_phrases(answer: str) -> str:
    """Remove one stock opener and one boilerplate closer, nothing else."""

    text = answer.strip()
    if not text:
        return answer
    cleaned = _cut_closer(_cut_opener(text))
    return cleaned or text


def _sentences(line: str) -> int:
    count = sum(line.count(end) for end in _SENTENCE_ENDS[:3])
    return count + (1 if line.rstrip()[-1:] in ".!?" else 0)


def longest_prose_run(answer: str) -> int:
    """Most sentences in one unbroken prose block (lists/headings/tables excluded)."""

    longest = run = 0
    for raw in answer.split("\n"):
        line = raw.strip()
        if (
            not line
            or line.startswith(_LIST_STARTS)
            or line[:1].isdigit()
            or line.lower().startswith(_NEXT_STEP)
        ):
            run = 0
            continue
        run += max(_sentences(line), 1)
        longest = max(longest, run)
    return longest


def paragraph_guard(answer: str) -> bool:
    """True when the answer has more than 2 consecutive prose sentences."""

    return longest_prose_run(answer) > _MAX_PROSE_SENTENCES


def polish_answer(answer: str, style: ResponseStyle | str | None) -> str:
    """Apply the filter under ``adaptive`` only (``current`` stays the baseline).

    The paragraph guard only logs, for the eval; it never rewrites.
    """

    if style != ResponseStyle.ADAPTIVE:
        return answer
    polished = strip_stock_phrases(answer)
    if paragraph_guard(polished):
        logger.info("answer_paragraph_guard", prose_run=longest_prose_run(polished))
    return polished
