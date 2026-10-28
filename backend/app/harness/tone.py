"""Conservative post-filter for stock AI openers and closers (OR-8).

Only the very start and the very end of an answer are touched: a known stock
opener is cut from the first sentence, and a closing sentence that is pure
boilerplate is dropped. Middle sentences are never rewritten (that would risk
the grounding), and a closer that carries anything fact-like (a digit, an
address, a link or a citation marker) is kept.

The one exception is internal wording (first-person source references such
as "the supplied context" or "the documents do not ..."; never the bare
words "documents" or "knowledge base", which are product terms): a sentence
that names how the answer was built is replaced or dropped,
and a stray square-bracket wrapper around a sentence is removed.

Matching is ``str.startswith`` against fixed literals and ``str.rfind``
scans, so the filter is linear-time on any input (no regex).
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.harness.style import (
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

# First-person source references that give away how the answer was built; a
# customer never sees them. Bare "the documents", "the context" and
# "knowledge base" are NOT here: they are ordinary product words ("the
# Documents page", "a knowledge base feature").
_SOURCE_NOUNS: tuple[str, ...] = ("documents", "knowledge base", "context")
_LEAK_PHRASES: tuple[str, ...] = (
    "supplied context",
    "provided context",
    "the context provided",
    "supplied information",
    "provided information",
    "information provided",
    "supplied materials",
    "provided materials",
    "materials provided",
    "provided documents",
    "supplied documents",
    "documents provided",
    "sources provided",
    "provided sources",
    "the documents do not",
    "the documents don't",
    *(
        f"the documents {verb}"
        for verb in (
            "state",
            "say",
            "mention",
            "define",
            "include",
            "specify",
            "describe",
            "contain",
        )
    ),
    *(f"based on the {noun}" for noun in _SOURCE_NOUNS),
    *(f"based on the provided {noun}" for noun in _SOURCE_NOUNS[:2]),
    *(f"according to the {noun}" for noun in _SOURCE_NOUNS[:2]),
    *(f"according to the provided {noun}" for noun in _SOURCE_NOUNS[:2]),
)
# Source references that only leak when they report an absence ("the
# materials do not state ..."); a bare "training materials" stays untouched.
_SOURCE_REFS: tuple[str, ...] = (
    "published materials",
    "our materials",
    "the materials",
    "available materials",
    "the information available to me",
    "the information provided",
    "in the material",
    "in my sources",
    "we don't have details showing",
)
# "in the knowledge base" is a leak only when it reports an absence.
_KB_LOCATION = "in the knowledge base"
_ABSENCE_CUES: tuple[str, ...] = (
    "not found",
    "couldn't find",
    "could not find",
    "can't find",
    "cannot find",
    "isn't",
    "is not",
    "aren't",
    "are not",
    "wasn't",
    "was not",
    "not in",
    "not present",
    "not listed",
    "not covered",
    "no information",
    "no mention",
)
_CUE_WINDOW = 40
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
_NEXT_STEP_LINE = "\n\nIf you'd like, one of our specialists can help."
_MAX_MARKER_LEN = 12
_MIN_WRAPPED_LEN = 3
_SENTENCE_PUNCT = ".!?"
_PREFIXES: tuple[str, ...] = ("- ", "* ", "• ")


def _kb_absence(lowered: str) -> bool:
    """True when "in the knowledge base" is preceded by an absence cue."""
    start = lowered.find(_KB_LOCATION)
    while start >= 0:
        window = lowered[max(0, start - _CUE_WINDOW) : start + len(_KB_LOCATION)]
        if any(cue in window for cue in _ABSENCE_CUES):
            return True
        start = lowered.find(_KB_LOCATION, start + 1)
    return False


def _has_internal_wording(lowered: str) -> bool:
    lowered = lowered.replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    if any(phrase in lowered for phrase in _LEAK_PHRASES) or _kb_absence(lowered):
        return True
    return any(ref in lowered for ref in _SOURCE_REFS) and _is_negative(lowered)


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
            if _is_negative(content.lower()) and not any(
                NOT_COVERED_SENTENCE in done for done in out
            ):
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
    text = _single_next_step("\n".join(_drop_repeats(rewritten)).strip())
    if _SPECIALIST not in text.lower():
        text += _NEXT_STEP_LINE
    return text


def _single_next_step(text: str) -> str:
    """Keep only the first "Next step:" line."""

    seen = False
    kept: list[str] = []
    for line in text.split("\n"):
        if line.strip().lower().startswith(_NEXT_STEP):
            if seen:
                continue
            seen = True
        kept.append(line)
    return "\n".join(kept).strip()


# ---------------------------------------------------------------------------
# Unrequested trailing caveat
# ---------------------------------------------------------------------------

_CAVEAT_STARTS: tuple[str, ...] = (
    "we don't have details on ",
    "we don't have details about ",
    "we do not have details on ",
    "we do not have details about ",
)
_TOPIC_STOPS: tuple[str, ...] = (
    " beyond",
    " for ",
    " in ",
    " at ",
    " here",
    " yet",
    " right now",
    ".",
    "[",
)
_TOPIC_FILLER = frozenset(
    {"that", "this", "those", "these", "the", "any", "it", "such"}
)
_STEM_LEN = 4
_MIN_STEM_WORD = 5
_TOPIC_SYNONYMS: dict[str, tuple[str, ...]] = {
    "pric": ("cost", "quote", "fee", "how much", "pay", "budget"),
    "time": ("how long", "weeks", "months", "duration", "when", "schedule"),
}


def _caveat_terms(sentence: str) -> list[str]:
    """Topic words a "We don't have details on ..." sentence talks about."""

    lowered = sentence.strip().lower().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    start = next((c for c in _CAVEAT_STARTS if lowered.startswith(c)), "")
    if not start:
        return []
    topic = lowered[len(start) :]
    cut = len(topic)
    for stop in _TOPIC_STOPS:
        found = topic.find(stop)
        if found >= 0:
            cut = min(cut, found)
    words = topic[:cut].replace(",", " ").replace(" or ", " ").replace(" and ", " ")
    return [w for w in words.split() if w not in _TOPIC_FILLER]


def _term_asked(term: str, question: str) -> bool:
    stem = term[:_STEM_LEN] if len(term) >= _MIN_STEM_WORD else term
    if stem in question:
        return True
    return any(syn in question for syn in _TOPIC_SYNONYMS.get(stem, ()))


_TAIL_ABSENCE_CUES: tuple[str, ...] = (
    "i can't see",
    "i cannot see",
    "i can't find",
    "i cannot find",
    "i couldn't find",
    "i could not find",
    "i don't see",
    "i do not see",
    "isn't mentioned",
    "is not mentioned",
    "aren't mentioned",
    "not mentioned",
    "we don't have",
    "we do not have",
)
_THERE_IS_NO: tuple[str, ...] = ("there's no ", "there is no ", "there are no ")
_THERE_IS_NO_CONTEXT: tuple[str, ...] = (
    " here",
    "specialist",
    "mentioned",
    "detail",
)
_CAVEAT_TOPICS: tuple[str, ...] = (
    "pric",
    "cost",
    "timelin",
    "timefram",
    "time",
    "turnaround",
    "quote",
    "discount",
    "licen",
    "roi",
    "sla",
    "certif",
    "complian",
    "guarant",
    "integrat",
)
_MAX_CAVEAT_WORDS = 40
_EXACT_TOPIC_LEN = 3
_CURLY_APOSTROPHE = "’"  # noqa: RUF001
_TELL_HERE = " here"


def _plain_lower(sentence: str) -> str:
    """Lower-cased sentence without citation markers, straight apostrophes."""

    out: list[str] = []
    last = 0
    while True:
        start = sentence.find("[", last)
        if start < 0:
            break
        end = sentence.find("]", start, start + _MAX_MARKER_LEN)
        out.append(sentence[last:start])
        last = end + 1 if end > 0 else start + 1
        if end <= 0:
            out.append("[")
    out.append(sentence[last:])
    return "".join(out).strip().lower().replace(_CURLY_APOSTROPHE, "'")


def _is_absence_statement(lowered: str) -> bool:
    if len(lowered.split()) > _MAX_CAVEAT_WORDS:
        return False
    if any(cue in lowered for cue in _TAIL_ABSENCE_CUES):
        return True
    return any(p in lowered for p in _THERE_IS_NO) and any(
        c in lowered for c in _THERE_IS_NO_CONTEXT
    )


def _absence_topics(lowered: str) -> list[str]:
    """Topic words (pricing, timelines, SLA ...) an absence sentence talks about."""

    words = lowered.replace(",", " ").replace(".", " ").replace("/", " ").split()
    return [
        w
        for w in words
        if any(
            w == t if len(t) <= _EXACT_TOPIC_LEN else w.startswith(t)
            for t in _CAVEAT_TOPICS
        )
    ]


def _caveat_topics(sentence: str) -> list[str]:
    """Topic words of a "not covered" caveat sentence; empty when it is not one."""

    terms = _caveat_terms(sentence)
    if terms:
        return terms
    lowered = _plain_lower(sentence)
    return _absence_topics(lowered) if _is_absence_statement(lowered) else []


def _is_handoff_only(sentence: str) -> bool:
    lowered = _plain_lower(sentence)
    return (
        _SPECIALIST in lowered
        and not _is_absence_statement(lowered)
        and len(lowered.split()) <= _MAX_CAVEAT_WORDS
    )


def _unrequested_caveat_start(pieces: list[str], asked: str) -> int:
    """Index of the first piece of a trailing unasked caveat, or -1."""

    last = len(pieces) - 1
    for index in (last, last - 1):
        if index < 0 or (index < last and not _is_handoff_only(pieces[last])):
            continue
        terms = _caveat_topics(pieces[index])
        if terms and not any(_term_asked(term, asked) for term in terms):
            return index
    return -1


def _rewrite_here_tell(sentence: str) -> str:
    """ "I can't see ... here, so ... specialists" becomes the stock sentence."""

    lowered = _plain_lower(sentence)
    if (
        _TELL_HERE in lowered
        and _SPECIALIST in lowered
        and _is_absence_statement(lowered)
    ):
        return NOT_COVERED_SENTENCE + sentence[len(sentence.rstrip()) :]
    return sentence


def drop_unrequested_caveat(answer: str, question: str | None) -> str:
    """Drop a final "can't see pricing / timelines here" the visitor never asked about.

    Only the last prose sentence (plus a bare specialist hand-off right after
    it) is considered, only when other content stays, and only when no topic
    word of the caveat appears in the visitor's question. Without a question
    nothing is removed. A kept "I can't see ... here" sentence is rewritten to
    the stock not-covered sentence (no "here" tell).
    """

    lines = answer.split("\n")
    index = len(lines) - 1
    while index >= 0 and (
        not lines[index].strip() or lines[index].strip().lower().startswith(_NEXT_STEP)
    ):
        index -= 1
    if index < 0:
        return answer
    pieces = _split_sentences(lines[index])
    if not pieces:
        return answer
    asked = (question or "").lower().strip()
    start = _unrequested_caveat_start(pieces, asked) if asked else -1
    if start < 0:
        pieces[-1] = _rewrite_here_tell(pieces[-1])
        lines[index] = "".join(pieces)
        return "\n".join(lines)
    remaining = "".join(pieces[:start]).rstrip()
    others = any(line.strip() for line in lines[:index])
    if not remaining and not others:
        pieces[-1] = _rewrite_here_tell(pieces[-1])
        lines[index] = "".join(pieces)
        return "\n".join(lines)
    if remaining:
        lines[index] = remaining
    else:
        del lines[index]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Human voice: dashes, odd characters, buzzwords, next-step labels, repeats
# ---------------------------------------------------------------------------
# Every step below is str-method code (find/split/scan): linear time, no
# backtracking regex. Code spans, fenced code, URLs, e-mail addresses,
# quoted names and citation markers are never changed.

_EM_DASHES = "\N{EM DASH}\N{EN DASH}"
_END_MARKS = ",.:;!?"
_MAX_HYPHEN_RUN = 2
_NO_WRAP_CHARS: tuple[str, ...] = (
    "-",
    "_",
    "/",
    "'",
    "\N{RIGHT SINGLE QUOTATION MARK}",
)
_PROTECTED_TOKEN_MARKS: tuple[str, ...] = ("://", "www.", "@", "/")
_MIN_DUPLICATE_WORDS = 3
_MAX_BLANK_RUN = 2

# Longest phrases first so the pair beats the single word.
_BUZZWORDS: tuple[tuple[str, str], ...] = (
    ("comprehensive, tailored", "tailored"),
    ("comprehensive and tailored", "tailored"),
    ("delves into", "looks at"),
    ("delving into", "looking at"),
    ("delve into", "look at"),
    ("comprehensive", "full"),
    ("seamlessly", "smoothly"),
    ("seamless", "smooth"),
    ("leveraging", "using"),
    ("leverages", "uses"),
    ("leveraged", "used"),
    ("leverage", "use"),
    ("robust", "strong"),
)

_GENERIC_HEADINGS = frozenset(
    {
        "key points",
        "key takeaways",
        "summary",
        "overview",
        "highlights",
        "details",
        "key details",
    }
)
_LABEL_HEADS: tuple[str, ...] = ("next step", *sorted(_GENERIC_HEADINGS))
_CLOSING_LEAD = "If you'd like, "
_SPECIALIST_CLOSING = "one of our specialists can help"
_FENCE = "```"


def _split_inline_headings(text: str) -> str:
    """Put a flattened " ## Next step" / " ## Key points" heading on its own line."""

    if " ## " not in text:
        return text
    pieces = text.split(" ## ")
    out = pieces[0]
    for piece in pieces[1:]:
        label = piece.lower().startswith(_LABEL_HEADS)
        out += ("\n\n## " if label else " ## ") + piece
    return out


def _item_text(line: str) -> str:
    """A next-step line without bullet, bold and trailing full stop."""

    body = line.strip()
    for prefix in _PREFIXES:
        if body.startswith(prefix):
            body = body[len(prefix) :]
            break
    return body.replace("**", "").strip().rstrip(".!").strip()


def _next_step_head(line: str) -> tuple[str, bool] | None:
    """(inline remainder, is_heading) for a "Next step" label line, else None."""

    stripped = line.strip()
    heading = stripped.startswith(("#", "**"))
    body = stripped.lstrip("#").lstrip().removeprefix("**").lstrip()
    if not body.lower().startswith(_NEXT_STEP):
        return None
    rest = body[len(_NEXT_STEP) :]
    rest = rest[1:] if rest[:1] in ("s", "S") else rest
    if rest[:1].isalnum():
        return None
    labelled = rest.lstrip("*").startswith(":")
    if not heading and not labelled:
        return None
    inline = rest.replace("**", "").lstrip(" :" + _EM_DASHES)
    inline = inline.removeprefix("- ")
    return inline.strip(), heading


def _closing_sentence(item: str, brand: str) -> str:
    """One natural closing sentence for a "Next step" action."""

    lowered = item.lower()
    factual = any(mark in lowered for mark in ("@", "http", "www."))
    index = lowered.find(_SPECIALIST)
    if index >= 0 and not factual:
        tail = item[index + len(_SPECIALIST) :].lstrip("s").strip()
        low_tail = tail.lower()
        if low_tail.startswith("to "):
            what = tail[3:].strip(" .,")
        elif low_tail.startswith(("about ", "with ")):
            what = "with " + tail.split(" ", 1)[1].strip(" .,")
        else:
            what = "with this"
        return f"{_CLOSING_LEAD}{_SPECIALIST_CLOSING} {what}."
    contact_brand = f"contact {brand.lower()}"
    if brand and lowered.startswith(contact_brand):
        rest = item[len(contact_brand) :]
        return f"{_CLOSING_LEAD}you can get in touch with us{rest}."
    if lowered.startswith(("if ", "you ", "we ")):
        return item[:1].upper() + item[1:] + "."
    first = item[:1] if item[1:2].isupper() else item[:1].lower()
    return f"{_CLOSING_LEAD}you can {first}{item[1:]}."


def _extract_next_steps(lines: list[str]) -> tuple[list[str], str]:
    """Remove every "Next step" block; return the rest and ONE closing sentence."""

    brand = get_settings().brand_name
    kept: list[str] = []
    closing = ""
    index = 0
    while index < len(lines):
        head = _next_step_head(lines[index])
        index += 1
        if head is None:
            kept.append(lines[index - 1])
            continue
        inline, heading = head
        items = [inline] if inline else []
        if heading or not inline:
            while (
                index < len(lines)
                and lines[index].strip()
                and not lines[index].lstrip().startswith("#")
            ):
                items.append(_item_text(lines[index]))
                index += 1
        if items and not closing:
            closing = _closing_sentence(_item_text(items[0]), brand)
    return kept, closing


def _drop_generic_headings(lines: list[str]) -> list[str]:
    """Remove "## Key points"-style form headings (the content stays)."""

    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        label = stripped.lstrip("#").strip().strip("*").rstrip(":").strip().lower()
        if label in _GENERIC_HEADINGS and stripped.startswith(("#", "**")):
            continue
        result.append(line)
    return result


def _dash_joiner(buf: list[str], following: str, *, has_text: bool) -> str:
    last = buf[-1] if buf else ""
    if not following or not has_text:
        return ""
    if last.isdigit() and following.isdigit():
        return " to "
    if buf[-2:] == ["*", "*"]:
        return " " if buf[-3:-2] == [":"] else ": "
    return " " if last in _END_MARKS else ", "


def _spaced_hyphen_end(seg: str, index: int) -> int:
    """End of a " - " / " -- " dash starting at ``index``, or ``index`` if none."""

    end = index
    while end < len(seg) and seg[end] == "-":
        end += 1
    if end - index > _MAX_HYPHEN_RUN or (end < len(seg) and seg[end] != " "):
        return index
    return end


def _dash_end(
    seg: str, index: int, buf: list[str], *, spaced: bool, has_text: bool
) -> int:
    """End index of the dash at ``index`` (0 when it is not a dash to replace)."""

    char = seg[index]
    if char in _EM_DASHES:
        end = index + 1
        while end < len(seg) and seg[end] in _EM_DASHES:
            end += 1
        return end
    if spaced and char == "-" and has_text and buf and buf[-1] == " ":
        end = _spaced_hyphen_end(seg, index)
        return end if end > index else 0
    return 0


def _replace_dashes(seg: str, lead: str, *, spaced: bool) -> str:
    """Dashes become commas (or a colon after a bold term); ranges become "to"."""

    base = len(lead)
    buf = list(lead)
    has_text = any(not char.isspace() for char in lead)
    length = len(seg)
    index = 0
    while index < length:
        char = seg[index]
        end = _dash_end(seg, index, buf, spaced=spaced, has_text=has_text)
        if not end:
            buf.append(char)
            has_text = has_text or not char.isspace()
            index += 1
            continue
        if (
            char == "\N{EN DASH}"
            and end == index + 1
            and buf
            and buf[-1].isdigit()
            and end < length
            and seg[end].isdigit()
        ):
            buf.extend(" to ")
            index = end
            continue
        while len(buf) > base and buf[-1] == " ":
            buf.pop()
        while end < length and seg[end] == " ":
            end += 1
        buf.extend(_dash_joiner(buf, seg[end : end + 1], has_text=has_text))
        index = end
    return "".join(buf[base:])


def _split_quoted(seg: str) -> list[tuple[str, bool]]:
    """(text, inside double quotes) pieces; quote marks belong to the quoted piece."""

    pieces: list[tuple[str, bool]] = []
    start = 0
    inside = False
    for index, char in enumerate(seg):
        opens = (char == '"' and not inside) or char == "\N{LEFT DOUBLE QUOTATION MARK}"
        closes = inside and char in ('"', "\N{RIGHT DOUBLE QUOTATION MARK}")
        if opens:
            if index > start:
                pieces.append((seg[start:index], False))
            start, inside = index, True
        elif closes:
            pieces.append((seg[start : index + 1], True))
            start, inside = index + 1, False
    if start < len(seg):
        pieces.append((seg[start:], inside))
    return pieces


def _is_whole_word(seg: str, start: int, stop: int) -> bool:
    before = seg[start - 1] if start else " "
    after = seg[stop] if stop < len(seg) else " "
    if before.isalnum() or after.isalnum():
        return False
    if before in _NO_WRAP_CHARS or after in _NO_WRAP_CHARS:
        return False
    token_start = seg.rfind(" ", 0, start) + 1
    token_end = seg.find(" ", stop)
    token = seg[token_start : token_end if token_end >= 0 else len(seg)]
    return not any(mark in token for mark in _PROTECTED_TOKEN_MARKS)


def _replace_phrase(seg: str, phrase: str, replacement: str) -> str:
    lowered = seg.lower()
    if len(lowered) != len(seg):
        return seg
    out: list[str] = []
    cursor = 0
    found = lowered.find(phrase)
    while found >= 0:
        stop = found + len(phrase)
        if _is_whole_word(seg, found, stop):
            new = replacement
            if seg[found].isupper():
                new = new[:1].upper() + new[1:]
            out.append(seg[cursor:found])
            out.append(new)
            cursor = stop
        found = lowered.find(phrase, stop if cursor == stop else found + 1)
    out.append(seg[cursor:])
    return "".join(out)


def _replace_buzzwords(seg: str) -> str:
    parts: list[str] = []
    for text, quoted in _split_quoted(seg):
        fixed = text
        if not quoted:
            for phrase, replacement in _BUZZWORDS:
                fixed = _replace_phrase(fixed, phrase, replacement)
        parts.append(fixed)
    return "".join(parts)


def _clean_line(line: str) -> str:
    """Odd characters, dashes and buzzwords in one line, outside code spans."""

    spaced = not line.strip().startswith("|")
    out = ""
    for index, part in enumerate(line.split("`")):
        if index % 2:
            out += f"`{part}`"
            continue
        plain = part.replace("\N{NON-BREAKING HYPHEN}", "-")
        plain = plain.replace("\N{NO-BREAK SPACE}", " ")
        out += _replace_buzzwords(_replace_dashes(plain, out[-3:], spaced=spaced))
    return out


def _clean_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    in_fence = False
    for line in lines:
        is_fence = line.strip().startswith(_FENCE)
        cleaned.append(line if in_fence or is_fence else _clean_line(line))
        in_fence = in_fence != is_fence
    return cleaned


def _drop_duplicate_sentences(lines: list[str]) -> list[str]:
    """Drop a sentence (or whole bullet) identical to the one just before it."""

    result: list[str] = []
    previous = ""
    for line in lines:
        if not line.strip():
            result.append(line)
            continue
        if line.lstrip().startswith(("|", _FENCE)):
            previous = ""
            result.append(line)
            continue
        prefix = next((p for p in _PREFIXES if line.lstrip().startswith(p)), "")
        indent = line[: len(line) - len(line.lstrip())]
        pieces: list[str] = []
        for piece in _split_sentences(line.lstrip()[len(prefix) :]):
            key = piece.strip().lower()
            if key and key == previous and len(key.split()) >= _MIN_DUPLICATE_WORDS:
                continue
            pieces.append(piece)
            previous = key or previous
        text = "".join(pieces).rstrip()
        if text:
            result.append(f"{indent}{prefix}{text}")
    return result


def _tidy(lines: list[str]) -> str:
    result: list[str] = []
    blanks = 0
    for line in lines:
        blanks = 0 if line.strip() else blanks + 1
        if blanks < _MAX_BLANK_RUN:
            result.append(line)
    return "\n".join(result).strip()


def humanise(answer: str) -> str:
    """Make an answer read like a person wrote it.

    Replaces dashes with commas, odd hyphens and spaces with plain ones, swaps
    stray buzzwords, turns a "Next step" block into one natural closing
    sentence, drops form headings like "## Key points" and repeated
    sentences. Code, URLs, quoted names and citation markers stay as written.
    """

    if not answer.strip():
        return answer
    text = _split_inline_headings(answer)
    lines, closing = _extract_next_steps(text.split("\n"))
    lines = _clean_lines(_drop_generic_headings(lines))
    body = _tidy(_drop_duplicate_sentences(lines))
    if closing and not (_SPECIALIST in closing and _SPECIALIST in body.lower()):
        body = f"{body}\n\n{closing}" if body else closing
    return body or answer


def polish_answer(
    answer: str,
    style: ResponseStyle | str | None,
    question: str | None = None,
) -> str:
    """Apply the filter under ``adaptive`` only (``current`` stays the baseline).

    The paragraph guard only logs, for the eval; it never rewrites.
    """

    if style != ResponseStyle.ADAPTIVE:
        return answer
    polished = remove_internal_wording(strip_stock_phrases(answer))
    polished = drop_unrequested_caveat(polished, question)
    polished = humanise(polished)
    if paragraph_guard(polished):
        logger.info("answer_paragraph_guard", prose_run=longest_prose_run(polished))
    return polished
