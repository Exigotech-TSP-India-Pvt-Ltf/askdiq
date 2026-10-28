"""Conservative post-filter for stock AI openers and closers (OR-8).

Only the very start and the very end of an answer are touched: a known stock
opener is cut from the first sentence, and a closing sentence that is pure
boilerplate is dropped. Middle sentences are never rewritten (that would risk
the grounding), and a closer that carries anything fact-like (a digit, an
address, a link or a citation marker) is kept.

The one exception is internal wording ("the documents", "knowledge base",
...): a sentence that names how the answer was built is replaced or dropped,
and a stray square-bracket wrapper around a sentence is removed.

Matching is ``str.startswith`` against fixed literals and ``str.rfind``
scans, so the filter is linear-time on any input (no regex).
"""

from __future__ import annotations

from app.core.logging_config import get_logger
from app.harness.style import (
    INTERNAL_WORDING,
    NOT_COVERED_REPLY,
    NOT_COVERED_SENTENCE,
    ResponseStyle,
)

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


# ---------------------------------------------------------------------------
# Internal wording and stray brackets
# ---------------------------------------------------------------------------

# Words that give away how the answer was built; a customer never sees them.
# "materials" alone is left out on purpose: it is also ordinary product copy.
_INTERNAL_TERMS: tuple[str, ...] = (
    *INTERNAL_WORDING,
    "supplied information",
    "information provided",
    "supplied materials",
    "provided materials",
    "materials provided",
    "provided documents",
    "supplied documents",
    "documents provided",
    "sources provided",
    "provided sources",
)
# "the context of ..." is ordinary English, not a leak.
_BENIGN_AFTER = {"the context": " of "}
_NEGATIONS: tuple[str, ...] = (
    "not ",
    "n't",
    "n\N{RIGHT SINGLE QUOTATION MARK}t",
    " no ",
    "cannot",
    "unable",
    "lack",
    "absent",
    "nothing",
    "none ",
)
_SPECIALIST = "specialist"
_NEXT_STEP_LINE = "\n\nNext step: talk to one of our specialists."
_MAX_MARKER_LEN = 12
_MIN_WRAPPED_LEN = 3
_SENTENCE_PUNCT = ".!?"
_PREFIXES: tuple[str, ...] = ("- ", "* ", "• ")


def _has_internal_wording(lowered: str) -> bool:
    for term in _INTERNAL_TERMS:
        start = lowered.find(term)
        while start >= 0:
            after = lowered[start + len(term) : start + len(term) + 4]
            if after != _BENIGN_AFTER.get(term):
                return True
            start = lowered.find(term, start + 1)
    return False


def _is_negative(lowered: str) -> bool:
    padded = f" {lowered} "
    return any(neg in padded for neg in _NEGATIONS)


def _marker_end(text: str, index: int) -> int:
    """Index after any citation markers (``[1]``, ``[2]``) starting at ``index``."""

    while index < len(text) and text[index] == "[":
        close = text.find("]", index, index + _MAX_MARKER_LEN)
        inner = text[index + 1 : close] if close > 0 else ""
        if not inner or not inner.isdigit():
            break
        index = close + 1
        while index < len(text) and text[index] == " ":
            index += 1
    return index


def _split_sentences(text: str) -> list[str]:
    """Sentences with their trailing spaces and citation markers (linear scan)."""

    pieces: list[str] = []
    start = 0
    length = len(text)
    index = 0
    while index < length:
        if text[index] in _SENTENCE_PUNCT and (
            index + 1 == length or text[index + 1] in " ["
        ):
            end = index + 1
            while end < length and text[end] == " ":
                end += 1
            end = _marker_end(text, end)
            pieces.append(text[start:end])
            start = end
            index = end
            continue
        index += 1
    if start < length:
        pieces.append(text[start:])
    return pieces


def _wrapped_inner(body: str) -> str | None:
    """The text inside ``[...]`` when one pair of brackets wraps all of ``body``."""

    if len(body) < _MIN_WRAPPED_LEN or body[0] != "[" or body[-1] != "]":
        return None
    depth = 0
    for position, char in enumerate(body):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0 and position != len(body) - 1:
                return None
    inner = body[1:-1].strip()
    if not inner or inner.replace(",", "").replace(" ", "").isdigit():
        return None
    return inner


def _unwrap_line(line: str) -> str:
    """Drop square brackets wrapped around a whole line (never a citation marker)."""

    stripped = line.strip()
    prefix = next((p for p in _PREFIXES if stripped.startswith(p)), "")
    body = stripped[len(prefix) :]
    tail = "." if body.endswith("].") else ""
    inner = _wrapped_inner(body[: len(body) - len(tail)] if tail else body)
    if inner is None:
        return line
    indent = line[: len(line) - len(line.lstrip())]
    closing = "" if inner[-1] in _SENTENCE_PUNCT else tail
    return f"{indent}{prefix}{inner}{closing}"


def _rewrite_line(line: str) -> tuple[str, int, int]:
    """(new line, internal sentences replaced, sentences kept) for one line."""

    if line.lstrip().startswith("|"):
        return line, 0, 1
    prefix = next((p for p in _PREFIXES if line.lstrip().startswith(p)), "")
    indent = line[: len(line) - len(line.lstrip())]
    body = line.lstrip()[len(prefix) :]
    out: list[str] = []
    replaced = kept = 0
    for piece in _split_sentences(body):
        content = piece.strip()
        if not content:
            out.append(piece)
        elif not _has_internal_wording(content.lower()):
            out.append(piece)
            kept += 1
        else:
            replaced += 1
            if _is_negative(content.lower()):
                out.append(NOT_COVERED_SENTENCE + piece[len(piece.rstrip()) :])
    text = "".join(out).rstrip()
    return (f"{indent}{prefix}{text}" if text.strip() else ""), replaced, kept


def _drop_repeats(lines: list[str]) -> list[str]:
    """Collapse repeated not-covered sentences into one."""

    seen = False
    result: list[str] = []
    for raw in lines:
        line = raw
        if NOT_COVERED_SENTENCE in raw:
            if seen:
                line = raw.replace(NOT_COVERED_SENTENCE, "").strip()
            seen = True
        if line.strip() or not result or result[-1].strip():
            result.append(line)
    return result


def remove_internal_wording(answer: str) -> str:
    """Hide how the answer was built from the customer.

    A sentence naming the supplied context, documents, knowledge base or
    materials is replaced by a plain not-covered sentence when it states an
    absence, and dropped otherwise; an answer with nothing else left becomes
    the friendly not-covered reply. A whole sentence wrapped in square
    brackets is unwrapped (citation markers are never touched). Linear time:
    ``str`` methods and one scan per line, no regex.
    """

    if not answer.strip():
        return answer
    lines = [_unwrap_line(line) for line in answer.split("\n")]
    rewritten: list[str] = []
    replaced = kept = 0
    for line in lines:
        new_line, line_replaced, line_kept = _rewrite_line(line)
        rewritten.append(new_line)
        replaced += line_replaced
        kept += line_kept
    if not replaced:
        return "\n".join(lines)
    if not kept:
        return NOT_COVERED_REPLY
    text = "\n".join(_drop_repeats(rewritten)).strip()
    if _SPECIALIST not in text.lower():
        text += _NEXT_STEP_LINE
    return text


def polish_answer(answer: str, style: ResponseStyle | str | None) -> str:
    """Apply the filter under ``adaptive`` only (``current`` stays the baseline).

    The paragraph guard only logs, for the eval; it never rewrites.
    """

    if style != ResponseStyle.ADAPTIVE:
        return answer
    polished = remove_internal_wording(strip_stock_phrases(answer))
    if paragraph_guard(polished):
        logger.info("answer_paragraph_guard", prose_run=longest_prose_run(polished))
    return polished
