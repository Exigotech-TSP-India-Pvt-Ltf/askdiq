"""Decline hygiene: a "not covered" / hand-off answer carries no markers or sources.

Pure, linear-time string helpers (no backtracking regex on user or model text).
"""

from __future__ import annotations

import re

from app.guardrails.post_checks import citation_stems

_MAX_CHARS = 20_000
_MARKER_RE = re.compile(r"\[\d{1,2}\]")
_TERMINATORS = frozenset(".!?")
_NEWLINES = frozenset("\r\n")

_DECLINE_PHRASES = (
    "i can't see",
    "i cannot see",
    "i don't see",
    "i do not see",
    "i can't find",
    "i cannot find",
    "i couldn't find",
    "i could not find",
    "i don't have",
    "i do not have",
    "i don't have that detail",
    "i don't have details on",
    "one for our specialists",
    "not sure about that one",
    "best to ask our specialists",
    "we don't have details",
    "we do not have details",
    "we don't have any details",
    "don't have details",
    "no details on",
    "isn't published",
    "is not published",
    "not yet claimed",
    "isn't covered",
    "is not covered",
    "aren't covered",
    "not covered here",
    "best to check with",
    "best to confirm with",
    "specialists can",
    "specialist can",
    "one of our specialists",
    "our specialists",
    "i'm not able to share",
    "i am not able to share",
    "i can't share",
    "i cannot share",
    "i can't help with",
    "i cannot help with",
)
_CTA_STARTS = (
    "contact sales",
    "contact deployiq",
    "contact deploy iq",
    "contact exigo",
    "contact our",
    "contact the",
    "contact a ",
    "contact us",
    "get in touch",
    "reach out to",
    "talk to our",
    "talk to a ",
    "talk to an ",
    "talk to one of",
    "talk to the",
    "talk to sales",
    "speak to our",
    "speak to a ",
    "speak with our",
    "speak with a ",
    "please contact",
    "please reach out",
    "please get in touch",
    "you can contact",
    "you can reach out",
    "you can talk to",
    "feel free to contact",
    "feel free to reach",
)
_CTA_MAX_WORDS = 14
_FILLER_MAX_WORDS = 4
_MIXED_LEAD_CHARS = 24
_MIXED_MAX_WORDS = 16
_MIN_OVERLAP = 0.2
_MIN_LEAD_ITEMS = 2
_MIN_QUERY_WORD_LEN = 3
_FACT_PHRASES = (
    "according to the facts, ",
    "according to the facts ",
    "the facts say that ",
    "the facts say ",
    "the facts state that ",
    "the facts state ",
    "the facts show ",
    "the facts indicate ",
    "based on the facts, ",
    "based on the facts ",
    "the information provided says ",
    "the information provided states ",
    "according to the information provided, ",
    "according to the information provided ",
)
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "you",
        "your",
        "are",
        "can",
        "how",
        "what",
        "does",
        "with",
        "that",
        "this",
        "from",
        "have",
        "has",
        "not",
        "any",
        "all",
        "our",
        "about",
        "its",
        "who",
        "why",
        "when",
        "where",
        "which",
        "into",
        "than",
        "then",
        "them",
        "they",
        "there",
        "their",
        "will",
        "would",
        "could",
        "should",
        "been",
        "being",
        "was",
        "were",
        "one",
        "get",
        "use",
        "using",
        "do",
        "is",
        "it",
        "of",
        "to",
        "in",
        "on",
        "a",
        "an",
        "or",
        "we",
        "us",
        "me",
        "my",
        "i",
        "if",
        "be",
        "as",
        "at",
        "by",
    ]
)

Span = tuple[int, int]


def _lower(ch: str) -> str:
    low = ch.lower()
    return low if len(low) == 1 else ch


def _norm(text: str) -> str:
    """Lowercase, apostrophes straightened; always the same length as ``text``."""
    return "".join(_lower(c) for c in text.replace("’", "'").replace("‘", "'"))  # noqa: RUF001


def _sentence_spans(text: str) -> list[Span]:
    """Sentence [start, end) spans; markers right after a terminator stay attached."""
    spans: list[Span] = []
    n = len(text)
    start = 0
    i = 0
    while i < n:
        ch = text[i]
        boundary = False
        if ch in _NEWLINES:
            boundary = True
        elif ch in _TERMINATORS:
            boundary = i + 1 >= n or text[i + 1].isspace() or text[i + 1] == "["
        if not boundary:
            i += 1
            continue
        end = i + 1
        while True:
            j = end
            while j < n and text[j] in " \t":
                j += 1
            m = _MARKER_RE.match(text, j)
            if m is None:
                break
            end = m.end()
        spans.append((start, end))
        start = end
        i = end
    if start < n:
        spans.append((start, n))
    return [s for s in spans if text[s[0] : s[1]].strip()]


def _plain(sentence: str) -> str:
    return " ".join(_MARKER_RE.sub("", sentence).split())


def _is_decline_plain(plain: str, has_marker: bool) -> bool:
    low = _norm(plain).strip(" \t-*•>\"'")
    if not low:
        return False
    words = low.split()
    for phrase in _DECLINE_PHRASES:
        idx = low.find(phrase)
        if idx < 0:
            continue
        # A decline tacked onto a supported claim ("X is Y [1], but I can't see Z")
        # is a mixed sentence, not a decline.
        if has_marker and idx > _MIXED_LEAD_CHARS:
            return False
        return idx <= _MIXED_LEAD_CHARS or len(words) <= _MIXED_MAX_WORDS
    if len(words) <= _CTA_MAX_WORDS and not any(c.isdigit() or c in "@/" for c in low):
        return any(low.startswith(p) for p in _CTA_STARTS)
    return False


def is_decline_sentence(sentence: str) -> bool:
    """A not-covered, hand-off or call-to-action sentence (markers ignored)."""
    sentence = sentence[:_MAX_CHARS]
    has_marker = _MARKER_RE.search(sentence) is not None
    return _is_decline_plain(_plain(sentence), has_marker)


def _is_filler(sentence: str) -> bool:
    plain = _plain(sentence)
    words = plain.split()
    return (
        0 < len(words) <= _FILLER_MAX_WORDS
        and _MARKER_RE.search(sentence) is None
        and any(c.isalpha() for c in plain)
        and not any(c.isdigit() for c in plain)
    )


def _classified(text: str) -> list[tuple[Span, bool]]:
    return [((a, b), is_decline_sentence(text[a:b])) for a, b in _sentence_spans(text)]


def is_decline_only(text: str) -> bool:
    """Every sentence declines / hands off / is a very short filler (>=1 decline)."""
    text = text[:_MAX_CHARS]
    declines = 0
    for (a, b), decline in _classified(text):
        if decline:
            declines += 1
        elif not _is_filler(text[a:b]):
            return False
    return declines > 0


def _remove_markers(segment: str) -> str:
    """``segment`` without [n] markers and the spaces right before them."""
    out: list[str] = []
    last = 0
    for m in _MARKER_RE.finditer(segment):
        out.append(segment[last : m.start()].rstrip(" \t"))
        last = m.end()
    out.append(segment[last:])
    return "".join(out)


def strip_all_markers(text: str) -> str:
    return _remove_markers(text[:_MAX_CHARS]) if "[" in text else text


def strip_decline_markers(text: str) -> str:
    """Remove [n] markers attached to decline sentences; other markers stay."""
    if "[" not in text:
        return text
    text = text[:_MAX_CHARS]
    out: list[str] = []
    last = 0
    for (a, b), decline in _classified(text):
        out.append(text[last:a])
        seg = text[a:b]
        out.append(_remove_markers(seg) if decline else seg)
        last = b
    out.append(text[last:])
    return "".join(out)


def _content_words(text: str) -> set[str]:
    words: set[str] = set()
    for raw in _norm(text).split():
        word = raw.strip(".,;:!?()[]\"'-*")
        if len(word) >= _MIN_QUERY_WORD_LEN and word not in _STOP:
            words.add(word)
    return words


_NOT_COVERED_MARKS = (
    "can't",
    "cannot",
    "couldn't",
    "could not",
    "don't",
    "do not",
    "isn't",
    "is not",
    "aren't",
    "not yet",
    "no details",
    "not sure about",
    "one for our specialists",
)


def _says_not_covered(sentence: str) -> bool:
    low = _norm(sentence)
    return any(mark in low for mark in _NOT_COVERED_MARKS)


def drop_leading_irrelevant(text: str, query: str) -> str:
    """Drop an off-topic factual lead sentence that precedes a pure decline."""
    text = text[:_MAX_CHARS]
    items = _classified(text)
    if len(items) < _MIN_LEAD_ITEMS or items[0][1] or not all(d for _, d in items[1:]):
        return text
    lead_span = items[0][0]
    if not any(_says_not_covered(text[a:b]) for (a, b), _ in items[1:]):
        return text  # a hand-off CTA after a real answer, not a decline
    query_words = _content_words(query[:_MAX_CHARS])
    if not query_words:
        return text
    lead_words = _content_words(_plain(text[lead_span[0] : lead_span[1]]))
    if len(query_words & lead_words) / len(query_words) >= _MIN_OVERLAP:
        return text
    return text[lead_span[1] :].lstrip()


def strip_source_references(text: str) -> str:
    """Remove "the facts say ..." style references to the model's own context."""
    text = text[:_MAX_CHARS]
    low = _norm(text)
    if "the facts" not in low and "the information provided" not in low:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        for phrase in _FACT_PHRASES:
            if low.startswith(phrase, i):
                i += len(phrase)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


_SUPPORT_THRESHOLD = 0.6
_MAX_TAIL_LINES = 2000
_LIST_START_RE = re.compile(r"^\s{0,8}(?:[-*•]|\d{1,2}[.)])\s+")
_INLINE_NUM_RE = re.compile(r"(?<=\s)(\d{1,2})[.)]\s+")
_FIGURE_RE = re.compile(r"\d+(?:[.,]\d+)*")
_IMPERATIVE_STARTS = frozenset(
    [
        "ask",
        "contact",
        "submit",
        "fill",
        "request",
        "book",
        "schedule",
        "create",
        "configure",
        "enable",
        "set",
        "open",
        "go",
        "click",
        "navigate",
        "select",
        "choose",
        "install",
        "download",
        "start",
        "begin",
        "first",
        "then",
        "next",
        "finally",
        "ensure",
        "make",
        "send",
        "email",
        "call",
        "visit",
        "use",
        "add",
        "define",
        "review",
        "follow",
        "check",
        "log",
        "sign",
        "register",
        "upload",
        "run",
        "apply",
        "deploy",
        "complete",
        "provide",
    ]
)


class _Piece:
    """A list item or a run of free text inside the text that follows a decline."""

    __slots__ = ("is_item", "orig", "removed", "text")

    def __init__(self, text: str, *, is_item: bool) -> None:
        self.text = self.orig = text
        self.is_item = is_item
        self.removed = False


def _source_stems(sources_text: str) -> frozenset[str]:
    stems: set[str] = set()
    for start in range(0, len(sources_text), _MAX_CHARS):
        stems |= citation_stems(sources_text[start : start + _MAX_CHARS])
    return frozenset(stems)


class _Support:
    """Is an item backed by the retrieved sources (or a specialist hand-off)?"""

    def __init__(self, sources_text: str, query: str) -> None:
        self._text = sources_text.replace(",", "")
        self._stems = _source_stems(sources_text)
        self._query_stems = citation_stems(query[:_MAX_CHARS])

    def __call__(self, plain: str) -> bool:
        if is_decline_sentence(plain) or "specialist" in _norm(plain):
            return True
        stems = citation_stems(plain)
        if not stems or not stems - self._query_stems:
            return False  # nothing to check, or it only echoes the question
        if any(f.replace(",", "") not in self._text for f in _FIGURE_RE.findall(plain)):
            return False
        return len(stems & self._stems) / len(stems) >= _SUPPORT_THRESHOLD


def _split_line(line: str) -> list[_Piece]:
    """Pieces of one line; an inline "1. a 2. b" list is split into items."""
    cuts: list[int] = []
    expected = 1
    start = 0
    head = _LIST_START_RE.match(line)
    if head is not None:
        cuts.append(0)
        start = head.end()
        digits = "".join(c for c in head.group(0) if c.isdigit())
        expected = int(digits) + 1 if digits else 1
    for m in _INLINE_NUM_RE.finditer(line, start):
        if int(m.group(1)) == expected:
            cuts.append(m.start())
            expected += 1
    if not cuts:
        return [_Piece(line, is_item=False)]
    pieces: list[_Piece] = []
    if cuts[0] > 0 and line[: cuts[0]].strip():
        pieces.append(_Piece(line[: cuts[0]], is_item=False))
    bounds = [*cuts, len(line)]
    pieces.extend(
        _Piece(line[a:b].strip(), is_item=True)
        for a, b in zip(bounds, bounds[1:], strict=False)
    )
    return pieces


def _item_body(piece: str) -> str:
    head = _LIST_START_RE.match(piece)
    if head is None:
        head = _INLINE_NUM_RE.match(piece)
    return _plain(piece[head.end() :] if head else piece)


def _is_instruction(sentence: str) -> bool:
    words = _plain(sentence).split()
    return bool(words) and words[0].lower().strip(",:") in _IMPERATIVE_STARTS


def _filter_free(text: str, supported: _Support) -> str | None:
    """``text`` without unsupported instruction sentences; None when unchanged."""
    out: list[str] = []
    changed = False
    last = 0
    for a, b in _sentence_spans(text):
        sentence = text[a:b]
        if _is_instruction(sentence) and not supported(_plain(sentence)):
            changed = True
            out.append(text[last:a].rstrip(" \t") if last == 0 else "")
        else:
            out.append(text[last:b])
        last = b
    if not changed:
        return None
    out.append(text[last:])
    return "".join(out)


def _mark_removed(pieces: list[_Piece], supported: _Support) -> bool:
    changed = False
    for piece in pieces:
        if piece.is_item:
            piece.removed = not supported(_item_body(piece.text))
        else:
            new = _filter_free(piece.text, supported)
            if new is not None:
                piece.text, piece.removed = new, not new.strip()
                changed = True
        changed = changed or piece.removed
    return changed


def _drop_orphan_lead_ins(pieces: list[_Piece]) -> None:
    """A "Here is how:" lead-in whose whole list was removed goes too."""
    for i in range(len(pieces) - 1):
        piece, nxt = pieces[i], pieces[i + 1]
        if (
            not piece.is_item
            and not piece.removed
            and nxt.is_item
            and nxt.removed
            and piece.text.rstrip().endswith(":")
            and len(_sentence_spans(piece.text)) == 1
        ):
            piece.removed = True


def strip_invented_after_decline(answer: str, sources_text: str, query: str) -> str:
    """Drop steps / instructions after a decline that the sources do not support.

    After "I can't see that detail" only nothing, or the specialist hand-off, may
    follow. Each numbered / bulleted item or instruction that follows is kept only
    when >= 60% of its content words (and every figure) occur in the sources.
    """
    text = answer[:_MAX_CHARS]
    decline_end = -1
    for (a, b), decline in _classified(text):
        if decline and _says_not_covered(text[a:b]):
            decline_end = b
            break
    if decline_end < 0:
        return answer
    supported = _Support(sources_text, query)
    lines = text[decline_end:].split("\n")
    if len(lines) > _MAX_TAIL_LINES:
        return answer
    per_line = [_split_line(line) if line.strip() else [] for line in lines]
    flat = [p for pieces in per_line for p in pieces]
    if not _mark_removed(flat, supported):
        return answer
    _drop_orphan_lead_ins(flat)
    out: list[str] = [text[:decline_end]]
    for i, (line, pieces) in enumerate(zip(lines, per_line, strict=True)):
        kept = [p.text.strip() for p in pieces if not p.removed]
        if all(not p.removed and p.text == p.orig for p in pieces):
            body = line
        elif kept:
            body = " ".join(kept)
            body = f" {body}" if i == 0 else body
        else:
            continue
        out.append("\n" + body if i else body)
    return "".join(out).rstrip()


def clean_declines(
    answer: str, query: str, sources_text: str | None = None
) -> tuple[str, bool]:
    """(cleaned answer, is_pure_decline). A pure decline carries no markers.

    ``sources_text`` (the retrieved chunks) enables dropping steps invented after a decline.
    """
    if sources_text is not None:
        answer = strip_invented_after_decline(answer, sources_text, query)
    text = strip_source_references(drop_leading_irrelevant(answer, query))
    if is_decline_only(text):
        return strip_all_markers(text).strip(), True
    return strip_decline_markers(text), False
