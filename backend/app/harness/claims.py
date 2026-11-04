"""Quote-backed answers: parse the model's claims, verify them, render the reply.

The answer call returns strict JSON (``prompt_catalog.answer_format``) with
compact keys (``prompt_catalog.CLAIM_KEYS`` / ``PART_KEYS``)::

    {"policy": "none",
     "parts":  [{"a": "the price", "c": false}, ...],
     "claims": [{"p": 0, "s": 2, "q": "<verbatim span of Source 2>",
                 "t": "", "k": "s"}]}

``a`` ask, ``c`` covered; ``p`` part index, ``s`` Source number, ``q`` quote,
``t`` text (``""``: the quote itself is the claim text), ``k`` kind ("s"
sentence, "i" list item). The long legacy keys (ask, covered, part, source,
quote, text, kind) are still read.

Every claim is checked deterministically against the chunk it cites (no extra
LLM call); a claim that fails any check is dropped:

1. the quote occurs in the cited chunk (case, whitespace and punctuation
   normalised; ``...`` splits a quote into in-order pieces);
2. every number (digits or number words), framework name, plan word and
   ``$``/``%`` figure of the claim text occurs in the chunk, and a duration
   ("2 weeks") keeps its unit;
3. English claims: every proper noun / acronym occurs in the chunk, the
   claim's content words overlap the chunk (``citation_min_overlap``), the
   negation parity of the claim matches the quote (or the quote's sentences)
   and assurance words (certified, guaranteed, safe ...) are backed by the
   quote's own sentences. Other languages: acronyms / product names and
   numbers occur in the chunk and the claim is not much longer than the quote;
4. locality (a chunk may list several rows, e.g. tiers with their own
   roadmap and framework): the claim's values occur in the quote's own
   sentences / lines, and its subject -- the names in the claim, or else the
   question's subject (``_query_subjects``) -- occurs in the quote, the two
   units before it, or the chunk's breadcrumb / heading. A range ("39 to 42") must
   be stated as a range, so two sources' conflicting values are never merged.

Claims of a part the model marked uncovered, decline / hand-off sentences and
duplicates are dropped too. The reply is rendered ONLY from claims that passed
(with ``[n]`` markers renumbered per document), plus the fixed partial line
naming the parts left without a claim. Nothing else is added, so the checked
text is the shipped text. All regular expressions are bounded and linear-time.
"""

from __future__ import annotations

import json
import re
import unicodedata
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.guardrails.post_checks import (
    _unsupported_figures,
    is_decline_sentence,
    unsupported_claims,
)
from app.harness.citations import (
    MARKER_RE,
    _ChunkIndex,
    _Claim,
    _contains_figures,
    _normalize_number,
    _numbers,
    _overlap,
    build_citation,
    strip_markers,
)
from app.harness.prompt_catalog import (
    BANNED_PHRASES,
    NO_ANSWER_LINE,
    PARTIAL_FALLBACK_PARTS,
    partial_line,
    sales_contact_line,
)
from app.harness.tone import polish_claim
from app.schemas.query import Citation, SourceChunk

_MAX_CLAIMS = 20
_MAX_TEXT_CHARS = 600
_MAX_QUOTE_CHARS = 1500
_MAX_ASK_CHARS = 80
_MAX_ASK_WORDS = 10
_MIN_QUOTE_WORDS = 2
_MIN_QUOTE_CJK_CHARS = 4
_MAX_SENTENCES_PER_PARAGRAPH = 2
# Length rules of the rendered reply (owner): at most 6 bullets per list; a
# lead shared by every bullet of a run (2+ words, 2+ bullets) is cut.
_MAX_LIST_ITEMS = 6
_MIN_SHARED_RUN = 2
_MIN_SHARED_WORDS = 2
# 3+ sentences in a row opening with the same 3+ words: lead + bullets.
_MIN_REPEATED_RUN = 3
_MIN_REPEATED_LEAD = 3
_ASK_MIN_SHARE = 0.5
_ENGLISH_MIN_SHARE = 0.12
_ENGLISH_MIN_WORDS = 6
# A translated claim is never much longer than the quote it renders.
_TRANSLATION_MAX_RATIO = 2.0
_TRANSLATION_SLACK_CHARS = 40
_UNIT_WINDOW = 3
# A claim's subject must sit in the quote or the few sentence / line units
# before it (a row label precedes its values), or in the chunk's breadcrumb /
# heading; values sit in the quote itself.
_LOCAL_WINDOW = 2
_MAX_QUOTE_HITS = 20
_MIN_HEADING_CHARS = 4
_RANGE_RE = re.compile(
    r"\b(\d{1,6})(?:\s{0,3}[-\N{EN DASH}\N{EM DASH}]\s{0,3}|\s{1,3}to\s{1,3})(\d{1,6})\b"
    r"|\bbetween\s{1,3}(\d{1,6})\s{1,3}and\s{1,3}(\d{1,6})\b",
    re.IGNORECASE,
)
# Numbers that are part of a name ("Microsoft 365", "ISO 27001", "IG1").
_NAME_NUMBER_RE = re.compile(
    r"\b[A-Z][A-Za-z&/]{1,30}[ ]{1,2}\d[\d.]{0,12}|\b[A-Za-z]{1,10}\d[\w.]{0,12}"
)

_RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"
_QUOTE_LABEL_RE = re.compile(r"[(\[]?\s{0,3}\bquote\s{0,3}:", re.IGNORECASE)
# Page-section numbering copied from a heading ("01 - Identity").
_PHONE_RE = re.compile(r"\b1[38]00[ ]?\d{3}[ ]?\d{3}\b|\(0\d\)[ ]?\d{4}[ ]?\d{4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}\b")
_CONTACT_PAGE_KEYS = ("source_name", "page_title", "route", "section_path")
_YES_NO_RE = re.compile(r"^\s{0,4}(?:yes|no)\s{0,2}[,.!;:]\s{1,4}(?=\S)", re.I)
_QUOTE_CHARS = str.maketrans("", "", '"\u201c\u201d')
_TERMINAL = ".!?:\u3002\uff01\uff1f"
_LABEL_MAX_WORDS = 5
_BANNED_LOWER = tuple(p.lower() for p in BANNED_PHRASES)
_STEM_CHARS = 5
_ENUMERATOR_RE = re.compile(r"^\s{0,4}\d{1,2}\s{0,3}[\u2014\u2013:.-]\s{1,3}")
# A claim cut mid-phrase ("... accessible individually or combined as.").
_DANGLING_END_RE = re.compile(
    r"\b(?:as|and|or|the|an|including)\s{0,2}[.!?;:,]?\s{0,2}$",
    re.IGNORECASE,
)
# Sentence-initial words a part label lowercases mid-sentence ("the price").
_LOWER_LEADS = frozenset(
    {
        "the", "a", "an", "its", "your", "our", "their", "my", "what", "who",
        "whose", "where", "when", "why", "how", "which", "whether", "is",
        "are", "do", "does", "can", "will", "if", "since", "compare",
    }
)  # fmt: skip
# The question words before a gate sub-question's subject ("and what's ...").
_GAP_LEAD_RE = re.compile(
    r"^(?:(?:and|also|plus|but)[,\s]{1,3})?"
    r"(?:what(?:['\u2019]s|\s{1,3}(?:is|are|was|were))\s{1,3})?",
    re.IGNORECASE,
)

PLAIN_MODE = "plain_verbatim"
JSON_MODE = "json"
# ``k`` of a list item: compact "i", or the legacy long value.
_ITEM_KINDS = frozenset({"i", "item"})

_NON_WORD_RE = re.compile(r"[^\w]+")
_ELLIPSIS_RE = re.compile(r"\.{3}|…")
_BULLET_RE = re.compile(r"^\s{0,8}(?:[-*•]|\d{1,2}[.)])\s{1,4}")
_EMPHASIS_RE = re.compile(r"\*{1,3}|_{2}|`")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'&+-]{0,40}")
_RAW_SENTENCE_RE = re.compile(r"[^.!?\n]{1,2000}[.!?]?")
# One sentence (or line) with the markers that follow it.
_PLAIN_UNIT_RE = re.compile(r"[^.!?\n]{1,2000}[.!?]{0,3}(?:[ \t]{0,4}\[\d{1,2}\]){0,6}")
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_DIGIT_RE = re.compile(r"\d")
_CODE_NAME_RE = re.compile(
    r"\b(?:[A-Z]{2,}[A-Za-z0-9]*|[A-Za-z]+[0-9][A-Za-z0-9]*|[a-z]+[A-Z][A-Za-z0-9]*)\b"
)
_CONTACT_RE = re.compile(r"@|\d{3}")
_GENERALISER_RE = re.compile(
    r"\b(?:both|all\s{1,3}of\s{1,3}them|each\s{1,3}of)\b", re.I
)

_ENGLISH_WORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "with",
        "we",
        "you",
        "your",
        "our",
        "it",
        "its",
        "this",
        "that",
        "can",
        "do",
        "does",
        "not",
        "no",
        "at",
        "by",
        "from",
        "as",
        "has",
        "have",
        "will",
    ]
)
_NEGATORS = frozenset(
    [
        "no",
        "not",
        "never",
        "none",
        "nothing",
        "nobody",
        "nowhere",
        "without",
        "cannot",
        "neither",
        "nor",
    ]
)
_UNITS = {
    "minute": "minute", "minutes": "minute", "hour": "hour", "hours": "hour",
    "day": "day", "days": "day", "week": "week", "weeks": "week",
    "month": "month", "months": "month", "year": "year", "years": "year",
}  # fmt: skip
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12",
}  # fmt: skip
# Sentence-initial or function words that are capitalised without being names.
_NOT_NAMES = frozenset(
    [
        "i",
        "we",
        "you",
        "our",
        "your",
        "yes",
        "no",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "they",
        "their",
        "each",
        "every",
        "the",
        "a",
        "an",
        "if",
        "for",
        "in",
        "on",
        "with",
        "and",
        "but",
        "or",
        "to",
        "at",
        "by",
        "from",
        "as",
        "all",
        "any",
        "most",
        "some",
        "there",
        "here",
        "when",
        "what",
        "which",
        "who",
        "how",
        "why",
        "after",
        "before",
        "during",
        "once",
        "then",
        "also",
        "both",
    ]
)


@dataclass(frozen=True, slots=True)
class Part:
    ask: str
    covered: bool


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    source: int
    quote: str
    part: int = 0
    kind: str = "sentence"


@dataclass(slots=True)
class Draft:
    """The model's reply, parsed (``action`` is "clarify" for questions back)."""

    action: str
    parts: list[Part]
    claims: list[Claim]
    mode: str = JSON_MODE


@dataclass(slots=True)
class GroundedAnswer:  # pylint: disable=too-many-instance-attributes
    """Verified claims rendered into the reply."""

    text: str
    citations: list[Citation] = field(default_factory=list)
    sources: list[SourceChunk] = field(default_factory=list)
    kept: list[Claim] = field(default_factory=list)
    dropped: list[tuple[Claim, str]] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    mode: str = JSON_MODE
    parts: list[Part] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return bool(self.kept)

    @property
    def verified_ratio(self) -> float:
        total = len(self.kept) + len(self.dropped)
        return round(len(self.kept) / total, 4) if total else 0.0

    def details(self) -> dict[str, object]:
        """What goes in token_details (bounded, no chunk text)."""
        return {
            "mode": self.mode,
            "claims": len(self.kept) + len(self.dropped),
            "kept": len(self.kept),
            "dropped": [
                {"text": c.text[:120], "source": c.source, "reason": reason}
                for c, reason in self.dropped[:_MAX_CLAIMS]
            ],
            "uncovered": self.uncovered,
            "parts": [{"ask": p.ask, "covered": p.covered} for p in self.parts],
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


def _str(value: object, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _field(item: dict[str, object], short: str, long: str) -> object:
    """A claim / part field by its compact key, else its long (legacy) key."""
    return item[short] if short in item else item.get(long)


def _json_claims(raw: object) -> list[Claim]:
    claims: list[Claim] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        quote = _str(_field(item, "q", "quote"), _MAX_QUOTE_CHARS)
        # An empty ``t`` means the quote itself is the claim's text; either
        # way the text then goes through every check below.
        text = (
            _str(_field(item, "t", "text"), _MAX_TEXT_CHARS) or quote[:_MAX_TEXT_CHARS]
        )
        claims.append(
            Claim(
                text=text,
                source=_as_int(_field(item, "s", "source"), 0),
                quote=quote,
                part=_as_int(_field(item, "p", "part"), 0),
                kind="item" if _field(item, "k", "kind") in _ITEM_KINDS else "sentence",
            )
        )
        if len(claims) >= _MAX_CLAIMS:
            break
    return claims


def _json_parts(raw: object) -> list[Part]:
    parts: list[Part] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            parts.append(
                Part(
                    _str(_field(item, "a", "ask"), _MAX_ASK_CHARS),
                    _field(item, "c", "covered") is True,
                )
            )
    return parts


def plain_claims(text: str) -> list[Claim]:
    """Claims from a plain-text reply: each sentence must be its own quote.

    Only used when the model ignored the schema (or a test double returns
    text): a sentence then ships only if it occurs verbatim in a chunk.
    """
    claims: list[Claim] = []
    for found in _PLAIN_UNIT_RE.finditer(text[: _MAX_QUOTE_CHARS * 4]):
        unit = found.group(0)
        markers = [int(m) for m in MARKER_RE.findall(unit)]
        plain = _EMPHASIS_RE.sub("", strip_markers(unit))
        is_item = _BULLET_RE.match(plain) is not None
        plain = " ".join(_BULLET_RE.sub("", plain).split())
        if not plain:
            continue
        claims.append(
            Claim(
                text=plain[:_MAX_TEXT_CHARS],
                source=markers[0] if markers else 0,
                quote=plain[:_MAX_QUOTE_CHARS],
                kind="item" if is_item else "sentence",
            )
        )
        if len(claims) >= _MAX_CLAIMS:
            break
    return claims


def parse_draft(raw: str) -> Draft | None:
    """The model's reply as a :class:`Draft`; ``None`` when it is empty."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return Draft("answer", [], plain_claims(text), PLAIN_MODE)
    if not isinstance(payload, dict):
        return Draft("answer", [], plain_claims(text), PLAIN_MODE)
    action = "clarify" if payload.get("action") == "clarify" else "answer"
    if "claims" not in payload and isinstance(payload.get("answer"), str):
        # An older answer-or-clarify reply: its free text is verbatim-checked.
        return Draft(action, [], plain_claims(payload["answer"]), PLAIN_MODE)
    return Draft(
        action, _json_parts(payload.get("parts")), _json_claims(payload.get("claims"))
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    """Lower-case, NFKC, punctuation -> space, single spaces."""
    text = unicodedata.normalize("NFKC", text).lower().replace("_", " ")
    return " ".join(_NON_WORD_RE.sub(" ", text).split())


def _words(text: str) -> list[str]:
    return _norm(text).split()


def is_english(texts: Sequence[str]) -> bool:
    """Heuristic language check of the reply: English function-word share."""
    joined = " ".join(texts)
    if _CJK_RE.search(joined):
        return False
    letters = [c for c in joined if c.isalpha()]
    if letters and sum(1 for c in letters if not c.isascii()) / len(letters) > 0.3:  # noqa: PLR2004
        return False
    words = _words(joined)
    if len(words) < _ENGLISH_MIN_WORDS:
        return True
    share = sum(1 for w in words if w in _ENGLISH_WORDS) / len(words)
    return share >= _ENGLISH_MIN_SHARE


class _Chunk:  # pylint: disable=too-many-instance-attributes
    """Normalised views of one retrieved chunk (built once per request)."""

    def __init__(self, source: SourceChunk) -> None:
        meta = source.metadata or {}
        extra = " ".join(
            str(meta.get(key) or "")
            for key in ("page_title", "section_heading", "section_path", "source_name")
        )
        raw = f"{source.content}\n{extra}"
        self.source = source
        self.raw = raw
        self.norm = _norm(raw)
        self.word_set = frozenset(self.norm.split())
        self.squashed = self.norm.replace(" ", "")
        self.index = _ChunkIndex(source)
        self.sentences = [m.group(0) for m in _RAW_SENTENCE_RE.finditer(raw)]
        self._sentence_norms = [_norm(s) for s in self.sentences]
        self.pairs = _unit_pairs(self.norm.split())
        # The breadcrumb (first line) and heading metadata scope the whole chunk.
        head = next((line for line in source.content.split("\n") if line.strip()), "")
        self.scope = f"{head}\n{extra}"
        # Sentence / line units of the body, for locating the quote.
        self.units = [
            m.group(0)
            for m in _RAW_SENTENCE_RE.finditer(source.content)
            if _norm(m.group(0))
        ]
        self._starts: list[int] = []
        position = 0
        for unit in self.units:
            self._starts.append(position)
            position += len(_norm(unit)) + 1
        self._joined = " ".join(_norm(u) for u in self.units)

    def view(self, text: str) -> _Chunk:
        """The same checks over a local span of this chunk (no metadata)."""
        return _Chunk(self.source.model_copy(update={"content": text, "metadata": {}}))

    def quote_spans(self, quote: str) -> list[tuple[int, int]]:
        """(first, last) unit of every place the quote occurs in the body."""
        pieces = [p for p in (_norm(x) for x in _ELLIPSIS_RE.split(quote)) if p]
        spans: list[tuple[int, int]] = []
        start = self._joined.find(pieces[0]) if pieces else -1
        while start >= 0 and len(spans) < _MAX_QUOTE_HITS:
            end = start + len(pieces[0])
            for piece in pieces[1:]:
                found = self._joined.find(piece, end)
                if found < 0:
                    return spans
                end = found + len(piece)
            spans.append(
                (
                    bisect_right(self._starts, start) - 1,
                    bisect_right(self._starts, end - 1) - 1,
                )
            )
            start = self._joined.find(pieces[0], start + 1)
        return spans

    def region(self, first: int, last: int, back: int) -> str:
        """Units ``first..last`` plus ``back`` units before them."""
        return " ".join(self.units[max(0, first - back) : last + 1])

    def quote_sentences(self, quote: str) -> str | None:
        """The chunk sentences the quote spans, or ``None`` if it is not there."""
        pieces = [p for p in (_norm(x) for x in _ELLIPSIS_RE.split(quote)) if p]
        if not pieces:
            return None
        position = 0
        for piece in pieces:
            found = self.norm.find(piece, position)
            if found < 0:
                return None
            position = found + len(piece)
        hits = [
            raw
            for raw, norm in zip(self.sentences, self._sentence_norms, strict=True)
            if norm
            and any(norm in p or p in norm or _shares_edge(norm, p) for p in pieces)
        ]
        return " ".join(hits) or quote

    def has_stem(self, token: str) -> bool:
        """A chunk word sharing the token's first letters ("Changes" ~ "change")."""
        stem = _norm(token)[:_STEM_CHARS]
        return any(word.startswith(stem) for word in self.word_set)

    def has_word(self, token: str) -> bool:
        words = _norm(token).split()
        if all(w in self.word_set for w in words):
            return True
        return "".join(words) in self.squashed


def _shares_edge(sentence: str, piece: str) -> bool:
    """True when a quote piece spans a sentence boundary into ``sentence``."""
    head = " ".join(sentence.split()[:4])
    tail = " ".join(sentence.split()[-4:])
    return bool(head) and (head in piece or tail in piece)


def _number(token: str) -> str | None:
    if token.isdigit():
        return token
    return _NUMBER_WORDS.get(token)


def _number_words(text: str) -> list[str]:
    """Digits of the number words in ``text`` ("five" -> "5"; "one" is too common)."""
    return [_NUMBER_WORDS[w] for w in _words(text) if w in _NUMBER_WORDS and w != "one"]


def _unit_pairs(words: list[str]) -> set[tuple[str, str]]:
    """(number, unit) pairs: a number within 3 words before a time unit."""
    pairs: set[tuple[str, str]] = set()
    for index, word in enumerate(words):
        unit = _UNITS.get(word)
        if unit is None:
            continue
        for back in words[max(0, index - _UNIT_WINDOW) : index]:
            number = _number(back)
            if number is not None:
                pairs.add((number, unit))
    return pairs


def _negation_parity(text: str) -> int:
    words = text.lower().replace(_RSQUO, "'").split()
    count = 0
    for word in words:
        bare = word.strip(".,;:!?()\"'")
        if bare in _NEGATORS or bare.endswith("n't"):
            count += 1
    return count % 2


def _names(text: str) -> list[tuple[str, bool]]:
    """``(word, sentence_initial)`` for proper nouns / acronyms of a claim.

    A sentence-initial capital may be an ordinary word ("Changes"), so the
    caller checks it leniently (word stem in the chunk); "Telstra uses ..."
    still fails because nothing like it is in the chunk.
    """
    names: list[tuple[str, bool]] = []
    initial = True
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0)
        before = text[max(0, match.start() - 2) : match.start()]
        if any(ch in ".!?" for ch in before):
            initial = True
        word = token.removesuffix("'s").removesuffix(f"{_RSQUO}s")
        capital = word[:1].isupper()
        coded = _CODE_NAME_RE.fullmatch(word) is not None
        if (coded or capital) and word.lower() not in _NOT_NAMES:
            names.append((word, initial and not coded))
        initial = False
    return names


def _brand_words() -> frozenset[str]:
    brand = get_settings().brand_name
    return frozenset(_norm(brand).split()) | {_norm(brand).replace(" ", "")}


def _quote_reason(claim: Claim, chunk: _Chunk) -> tuple[str | None, str]:
    """(failure reason, evidence sentences) for the quote itself."""
    quote = claim.quote
    short = (
        len(_words(quote)) < _MIN_QUOTE_WORDS
        and len(_CJK_RE.findall(quote)) < _MIN_QUOTE_CJK_CHARS
    )
    if short:
        # A one-word quote backs only a claim that IS that label ("Assess").
        asserted = set(_words(claim.text)) - _NOT_NAMES - _ENGLISH_WORDS
        if not asserted or not asserted <= set(_words(quote)):
            return "quote_too_short", ""
    evidence = chunk.quote_sentences(quote)
    if evidence is None:
        return "quote_not_in_source", ""
    return None, evidence


def _figure_reason(claim: Claim, chunk: _Chunk) -> str | None:
    """Numbers, amounts, durations, frameworks and plan words must be there."""
    if not _contains_figures(_Claim(claim.text), chunk.index):
        return "figure_not_in_source"
    if _unsupported_figures(claim.text, chunk.raw):
        return "amount_not_in_source"
    if not all(chunk.index.has_number(n) for n in _number_words(claim.text)):
        return "figure_not_in_source"
    if not _unit_pairs(_words(claim.text)) <= chunk.pairs:
        return "duration_not_in_source"
    return None


def _ranges(text: str) -> set[tuple[str, str]]:
    return {
        (m.group(1) or m.group(3), m.group(2) or m.group(4))
        for m in _RANGE_RE.finditer(text)
    }


def _name_numbers(text: str) -> set[str]:
    return {
        _normalize_number(n)
        for m in _NAME_NUMBER_RE.finditer(text)
        for n in _numbers(m.group(0))
    }


def _value_reason(claim: Claim, span: _Chunk, local: _Chunk) -> str | None:
    """Values sit in the quote's own units; name numbers / frameworks nearby."""
    figures = _Claim(claim.text)
    named = _name_numbers(claim.text)
    backed = (
        all(
            (local if n in named else span).index.has_number(n)
            for n in figures.numbers
        )
        and all(span.index.has_number(n) for n in _number_words(claim.text))
        and _unit_pairs(_words(claim.text)) <= span.pairs
        and not _unsupported_figures(claim.text, span.raw)
        and (not figures.frameworks or bool(figures.frameworks & local.index.frameworks))
    )
    if not backed:
        return "value_not_in_quote"
    # "39 to 42": a range the source does not state merges two sources' values.
    if not _ranges(claim.text) <= _ranges(span.raw):
        return "merged_values"
    return None


def _subject_reason(  # pylint: disable=too-many-arguments
    claim: Claim,
    chunk: _Chunk,
    *,
    span: _Chunk,
    local: _Chunk,
    subjects: Sequence[str],
    english: bool,
) -> str | None:
    """The claim's subject (named, or else the question's) is near the value.

    Other languages capitalise ordinary nouns, so there only the names the
    chunk itself contains (KB entities: tiers, services) count.
    """
    brand = _brand_words()
    named = [
        (name, initial)
        for name, initial in _names(claim.text)
        if english or chunk.has_word(name)
    ]
    for name, initial in named:
        if _norm(name) in brand or span.has_word(name) or local.has_word(name):
            continue
        if not initial or not local.has_stem(name):
            return "subject_not_local"
    claim_words = set(_words(claim.text))
    # "Typical roadmap: 1-2 weeks" answering "the Foundation tier": the
    # question's subject must be the one this part of the chunk is about.
    restated = any(set(_words(s)) <= claim_words for s in subjects)
    if subjects and not restated and not any(local.has_word(s) for s in subjects):
        return "subject_not_local"
    return None


def _local_reason(
    claim: Claim, chunk: _Chunk, english: bool, subjects: Sequence[str]
) -> str | None:
    """A chunk listing several rows backs a value only with its own subject."""
    figures = _Claim(claim.text)
    if not (figures.numbers or figures.frameworks or _number_words(claim.text)):
        return None
    spans = chunk.quote_spans(claim.quote)
    reason: str | None = None
    for first, last in spans:
        span = chunk.view(chunk.region(first, last, 0))
        local = chunk.view(f"{chunk.region(first, last, _LOCAL_WINDOW)}\n{chunk.scope}")
        reason = _value_reason(claim, span, local)
        if reason is None:
            reason = _subject_reason(
                claim, chunk, span=span, local=local, subjects=subjects, english=english
            )
        if reason is None:
            return None
    return reason


def _query_subjects(
    query: str, parts: Sequence[Part], part: int, chunks: Sequence[_Chunk]
) -> list[str]:
    """Entities the question asks about that the retrieved chunks know.

    Names capitalised in the question, plus retrieved section headings the
    question spells in lower case ("foundation"). With several parts, only
    the subjects the claim's part names (all of them when it names none).
    """
    brand = _brand_words()
    found: dict[str, str] = {}
    for name, initial in _names(query):
        if initial or _norm(name) in brand:
            continue
        if any(c.has_word(name) for c in chunks):
            found.setdefault(_norm(name), name)
    query_words = set(_words(query))
    for chunk in chunks:
        heading = str((chunk.source.metadata or {}).get("section_heading") or "")
        words = _words(heading)
        if (
            len(heading) >= _MIN_HEADING_CHARS
            and words
            and set(words) <= query_words
            and _norm(heading) not in brand
        ):
            found.setdefault(_norm(heading), heading)
    subjects = list(found.values())
    if len(parts) > 1 and 0 <= part < len(parts):
        ask = set(_words(parts[part].ask))
        return [s for s in subjects if set(_words(s)) <= ask] or subjects
    return subjects


def _english_reason(claim: Claim, chunk: _Chunk, evidence: str) -> str | None:
    brand = _brand_words()
    for name, initial in _names(claim.text):
        if _norm(name) in brand or chunk.has_word(name):
            continue
        if not initial or not chunk.has_stem(name):
            return "name_not_in_source"
    if _overlap(_Claim(claim.text), chunk.index) < get_settings().citation_min_overlap:
        return "low_overlap"
    parity = _negation_parity(claim.text)
    if parity not in (_negation_parity(claim.quote), _negation_parity(evidence)):
        return "negation_mismatch"
    if unsupported_claims(claim.text, evidence):
        return "assurance_not_in_quote"
    # "Both" / "all of them" generalises one source over several subjects.
    if _GENERALISER_RE.search(claim.text) and not _GENERALISER_RE.search(evidence):
        return "generalised_beyond_quote"
    return None


def _other_language_reason(claim: Claim, chunk: _Chunk) -> str | None:
    brand = _brand_words()
    for name in _CODE_NAME_RE.findall(claim.text):
        if _norm(name) not in brand and not chunk.has_word(name):
            return "name_not_in_source"
    limit = len(claim.quote) * _TRANSLATION_MAX_RATIO + _TRANSLATION_SLACK_CHARS
    if len(claim.text) > limit:
        return "longer_than_quote"
    return None


def _check(
    claim: Claim, chunk: _Chunk, english: bool, subjects: Sequence[str] = ()
) -> str | None:
    """Why ``claim`` is not backed by ``chunk`` (``None`` when it is)."""
    reason, evidence = _quote_reason(claim, chunk)
    if reason is None:
        reason = _figure_reason(claim, chunk)
    if reason is None:
        reason = (
            _english_reason(claim, chunk, evidence)
            if english
            else _other_language_reason(claim, chunk)
        )
    if reason is None:
        reason = _local_reason(claim, chunk, english, subjects)
    return reason


def _find_source(
    claim: Claim, chunks: list[_Chunk], english: bool, subjects: Sequence[str]
) -> int:
    """Plain mode without a marker: the first chunk that backs the sentence."""
    for number, chunk in enumerate(chunks, start=1):
        if _check(claim, chunk, english, subjects) is None:
            return number
    return 0


def _is_decline(text: str) -> bool:
    """An absence / hand-off sentence (the fixed lines replace those)."""
    return is_decline_sentence(text) and _CONTACT_RE.search(text) is None


def _clean_text(claim: Claim) -> Claim:
    raw = claim.text
    # A quote pasted into the text ("... (quote: ...)") is cut, never shipped.
    cut = _QUOTE_LABEL_RE.search(raw)
    if cut is not None:
        raw = raw[: cut.start()]
    raw = _ENUMERATOR_RE.sub("", _EMPHASIS_RE.sub("", raw))
    # "No, ..." / "Yes, ..." is an inference on top of the quote: cut it, the
    # rest is verified on its own.
    raw = _YES_NO_RE.sub("", raw)
    raw = raw.translate(_QUOTE_CHARS)
    text = polish_claim(strip_markers(raw))
    # A trailing comma copied from a list row would render as "matures.,.".
    text = " ".join(_BULLET_RE.sub("", text).split()).rstrip(" ,;")
    if text[:1].islower() and text[1:2].islower():
        text = text[:1].upper() + text[1:]
    return Claim(text, claim.source, claim.quote, claim.part, claim.kind)


def _verify(
    draft: Draft, chunks: list[_Chunk], english: bool, query: str = ""
) -> tuple[list[Claim], list[tuple[Claim, str]]]:
    kept: list[Claim] = []
    dropped: list[tuple[Claim, str]] = []
    seen: set[str] = set()
    for original in draft.claims:
        claim = _clean_text(original)
        reason = _precheck(claim, draft.parts, seen)
        subjects = _query_subjects(query, draft.parts, claim.part, chunks)
        if reason is None and draft.mode == PLAIN_MODE and claim.source == 0:
            claim = Claim(
                claim.text,
                _find_source(claim, chunks, english, subjects),
                claim.quote,
                claim.part,
                claim.kind,
            )
        if reason is None and not 1 <= claim.source <= len(chunks):
            reason = "bad_source"
        if reason is None:
            reason = _check(claim, chunks[claim.source - 1], english, subjects)
        if reason is None:
            seen.add(_norm(claim.text))
            kept.append(claim)
        else:
            dropped.append((original, reason))
    return kept, dropped


def _precheck(claim: Claim, parts: list[Part], seen: set[str]) -> str | None:
    if not claim.text:
        return "empty"
    lowered = claim.text.lower()
    if any(p in lowered for p in _BANNED_LOWER):
        # Banned stock / internal wording ("materials") never ships, even
        # when a page uses it: the claim is dropped, nothing is rewritten.
        return "banned_wording"
    if _norm(claim.text) in seen:
        return "duplicate"
    if _DANGLING_END_RE.search(claim.text):
        return "truncated"
    if _is_decline(claim.text):
        return "decline"
    if parts and not (0 <= claim.part < len(parts) and parts[claim.part].covered):
        return "uncovered_part"
    return None


def _ask_label(ask: str, query: str) -> str | None:
    """The model's name for a part, only if it restates the question."""
    words = _words(ask)
    if not words or len(words) > _MAX_ASK_WORDS or MARKER_RE.search(ask):
        return None
    query_words = set(_words(query))
    if _DIGIT_RE.search(ask) and not all(
        w in query_words for w in words if _DIGIT_RE.search(w)
    ):
        return None
    content = [w for w in words if len(w) > 3 or _CJK_RE.search(w)]  # noqa: PLR2004
    if not content:
        return None
    hits = sum(
        1
        for w in content
        if w in query_words or any(q.startswith(w[:4]) for q in query_words)
    )
    if hits / len(content) < _ASK_MIN_SHARE:
        return None
    label = _GAP_LEAD_RE.sub("", " ".join(ask.split())).rstrip(" ?.!") or ask
    # "The price" -> "the price", never "CrowdStrike" -> "crowdStrike".
    if label.split(" ", 1)[0].lower() in _LOWER_LEADS:
        label = label[:1].lower() + label[1:]
    return label


def uncovered_parts(draft: Draft, kept: list[Claim], query: str) -> list[str] | None:
    """Names of the parts left without a verified claim (``None``: unnamed)."""
    answered = {c.part for c in kept}
    missing = [p for i, p in enumerate(draft.parts) if i not in answered]
    labels = [_ask_label(p.ask, query) for p in missing]
    if any(label is None for label in labels):
        return None
    return [label for label in labels if label]


def _gap_label(text: str, query: str) -> str | None:
    """A gate sub-question ("and what are its opening hours?") as a label."""
    label = _GAP_LEAD_RE.sub("", " ".join(text.split())).rstrip(" ?.!,;")
    return _ask_label(label, query) if label else None


def _named_gaps(
    draft: Draft, kept: list[Claim], query: str, gap_parts: Sequence[str]
) -> list[str] | None:
    """The uncovered parts in the visitor's words; ``None`` only if unnamed.

    The model's part names come first; when one cannot be named (or the
    model folded an uncovered ask into a covered part), the retrieval gate's
    uncovered sub-questions name it instead of "the rest of your question".
    """
    answered = {c.part for c in kept}
    labels = [
        _ask_label(p.ask, query) for i, p in enumerate(draft.parts) if i not in answered
    ]
    named = [label for label in labels if label]
    gaps = [g for g in (_gap_label(t, query) for t in gap_parts) if g]
    if gaps and (None in labels or not named):
        seen = {label.lower() for label in named}
        named += [g for g in gaps if g.lower() not in seen]
        return named
    return None if None in labels else named


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _sentence(text: str) -> str:
    return text if text[-1:] in _TERMINAL else f"{text}."


def _shared_lead(claims: list[Claim]) -> int:
    """Words every claim of ``claims`` starts with (case-insensitive)."""
    shared = 0
    for words in zip(*(c.text.lower().split() for c in claims), strict=False):
        if len(set(words)) != 1:
            break
        shared += 1
    return shared


def _cut_lead(run: list[Claim], shared: int, kind: str) -> list[Claim]:
    """``run`` as ``kind`` claims without their first ``shared`` words
    (unchanged when a claim would keep fewer than 2 words). Subtractive only:
    the lead sentence names the subject once."""
    split = [c.text.split() for c in run]
    if any(len(w) - shared < _MIN_SHARED_WORDS for w in split):
        return run
    out: list[Claim] = []
    for claim, words in zip(run, split, strict=True):
        rest = " ".join(words[shared:])
        text = rest[:1].upper() + rest[1:]
        out.append(Claim(text, claim.source, claim.quote, claim.part, kind))
    return out


def _strip_shared_lead(run: list[Claim]) -> list[Claim]:
    """Bullets that all repeat one lead ("The Frontier package includes ...")
    lose it."""
    shared = _shared_lead(run) if len(run) >= _MIN_SHARED_RUN else 0
    return _cut_lead(run, shared, "item") if shared >= _MIN_SHARED_WORDS else run


def _bulletise(kept: list[Claim]) -> list[Claim]:
    """3+ sentences in a row repeating one lead ("The Frontier package
    includes ...") become the first sentence plus bullets without it."""
    out: list[Claim] = []
    index = 0
    while index < len(kept):
        end = index + 1
        while (
            end < len(kept)
            and kept[end].kind == kept[index].kind == "sentence"
            and _shared_lead(kept[index : end + 1]) >= _MIN_REPEATED_LEAD
        ):
            end += 1
        run = kept[index:end]
        if len(run) >= _MIN_REPEATED_RUN:
            items = _cut_lead(run[1:], _shared_lead(run), "item")
            # Not cut (a bullet would be too short): keep the sentences.
            out.extend([run[0], *items] if items[0].kind == "item" else run)
        else:
            out.extend(run)
        index = end
    return out


def _fit_length(kept: list[Claim], max_words: int) -> list[Claim]:
    """The verified claims that ship, within the length rules (drop-only).

    At most ``_MAX_LIST_ITEMS`` items per list (shared bullet lead removed)
    and about ``max_words`` words; each answered part keeps its first claim,
    so a long answer never turns a covered part into a gap.
    """
    shown: list[Claim] = []
    run: list[Claim] = []
    words = 0
    parts_shown: set[int] = set()

    def flush_run() -> None:
        shown.extend(_strip_shared_lead(run))
        run.clear()

    for claim in _bulletise(kept):
        size = len(claim.text.split())
        if words + size > max_words and claim.part in parts_shown:
            continue
        if claim.kind == "item":
            if len(run) >= _MAX_LIST_ITEMS:
                continue
            run.append(claim)
        else:
            flush_run()
            shown.append(claim)
        words += size
        parts_shown.add(claim.part)
    flush_run()
    return shown


def _is_label(text: str) -> bool:
    """A bare list label ("Identity (Entra)"): short, no closing punctuation."""
    return len(text.split()) <= _LABEL_MAX_WORDS and text[-1:] not in _TERMINAL


def _render(  # pylint: disable=too-many-locals
    kept: list[Claim], sources: Sequence[SourceChunk], numbered: bool
) -> tuple[str, list[Citation], list[SourceChunk]]:
    numbers: dict[str, int] = {}
    selected: list[SourceChunk] = []

    def marker(claim: Claim) -> int:
        source = sources[claim.source - 1]
        if source.document_id not in numbers:
            selected.append(source)
            numbers[source.document_id] = len(selected)
        return numbers[source.document_id]

    blocks: list[list[str]] = []
    paragraph: list[tuple[str, int]] = []
    items: list[str] = []
    last_label: Claim | None = None

    def flush_paragraph() -> None:
        if not paragraph:
            return
        out: list[str] = []
        for index, (text, n) in enumerate(paragraph):
            following = paragraph[index + 1][1] if index + 1 < len(paragraph) else None
            out.append(text if following == n else f"{text} [{n}]")
        blocks.append([" ".join(out)])
        paragraph.clear()

    def flush_items() -> None:
        if items:
            blocks.append(list(items))
            items.clear()

    for claim in kept:
        n = marker(claim)
        if claim.kind == "item":
            flush_paragraph()
            prefix = f"{len(items) + 1}." if numbered else "-"
            items.append(f"{prefix} {claim.text.rstrip()} [{n}]")
            last_label = claim if _is_label(claim.text) else None
            continue
        if last_label is not None and items and claim.source == last_label.source:
            # "- Identity (Entra)" + "Sign-in security ..." from the same
            # source: one item, label then description (both verified).
            items[-1] = f"{items[-1][: -len(f' [{n}]')]}: {_sentence(claim.text)} [{n}]"
            last_label = None
            continue
        last_label = None
        flush_items()
        paragraph.append((_sentence(claim.text), n))
        if len(paragraph) >= _MAX_SENTENCES_PER_PARAGRAPH:
            flush_paragraph()
    flush_paragraph()
    flush_items()
    text = "\n\n".join("\n".join(block) for block in blocks)
    citations = [build_citation(i, s) for i, s in enumerate(selected, start=1)]
    return text, citations, selected


@dataclass(frozen=True, slots=True)
class SalesContact:
    """The public sales phone / email, as written in a retrieved chunk."""

    source: int  # 1-based index into the sources
    phone: str
    email: str

    @property
    def line(self) -> str:
        return sales_contact_line(self.phone, self.email)


def find_sales_contact(sources: Sequence[SourceChunk]) -> SalesContact | None:
    """The contact page's phone and email from the retrieved chunks, if any.

    Values are copied from the chunk text (never hardcoded); a chunk from a
    contact page is preferred over any other chunk that lists both.
    """
    best: SalesContact | None = None
    for number, source in enumerate(sources, start=1):
        text = source.content[: _MAX_QUOTE_CHARS * 4]
        phone, email = _PHONE_RE.search(text), _EMAIL_RE.search(text)
        if phone is None or email is None:
            continue
        found = SalesContact(number, phone.group(0), email.group(0))
        meta = source.metadata or {}
        page = " ".join(str(meta.get(k) or "") for k in _CONTACT_PAGE_KEYS).lower()
        if "contact" in page:
            return found
        best = best or found
    return best


_OVERVIEW_MIN_WORDS = 6
_OVERVIEW_MAX_CLAIMS = 5
_BREADCRUMB_SEP = chr(0x203A)  # breadcrumb separator (app.chunking.section_tree)


def overview_draft(
    sources: Sequence[SourceChunk], numbers: Sequence[int], ask: str
) -> Draft:
    """A bare topic's page overview: its own sentences, copied verbatim.

    Deterministic last resort when the topic's page was retrieved but the
    model returned no verified claim twice. Nothing is generated: each claim
    is a statement of those chunks (breadcrumbs, headings, tables, questions
    and fragments skipped) and still goes through the normal verification
    in ``ground_answer``.
    """
    claims: list[Claim] = []
    seen: set[str] = set()
    for number in numbers:
        if not 1 <= number <= len(sources):
            continue
        for line in sources[number - 1].content.split("\n"):
            plain = _EMPHASIS_RE.sub("", _BULLET_RE.sub("", line)).strip()
            skip = not plain or plain.startswith("#")
            if skip or "|" in plain or _BREADCRUMB_SEP in plain:
                continue
            for found in _RAW_SENTENCE_RE.finditer(plain):
                sentence = " ".join(found.group(0).split())
                key = _norm(sentence)
                if (
                    len(sentence.split()) < _OVERVIEW_MIN_WORDS
                    or sentence[-1:] not in ".!"
                    or key in seen
                ):
                    continue
                seen.add(key)
                text = sentence[:_MAX_TEXT_CHARS]
                claims.append(Claim(text, number, sentence[:_MAX_QUOTE_CHARS]))
                if len(claims) >= _OVERVIEW_MAX_CLAIMS:
                    return Draft("answer", [Part(ask, True)], claims)
    return Draft("answer", [Part(ask, True)], claims)


def ground_answer(  # pylint: disable=too-many-arguments,too-many-locals
    draft: Draft,
    sources: Sequence[SourceChunk],
    query: str,
    *,
    numbered: bool = False,
    gate_uncovered: bool = False,
    gap_parts: Sequence[str] = (),
    sales_contact: SalesContact | None = None,
) -> GroundedAnswer:
    """Verify ``draft`` against ``sources`` and render the shipped reply.

    No verified claim -> ``text`` is the fixed no-answer line and
    ``answered`` is False. Some parts without a verified claim -> the fixed
    partial line naming them is appended. ``numbered``: items as steps.
    ``gate_uncovered``: the retrieval gate found an ask with no evidence, so
    the partial line is added even if the model claimed every part.
    ``gap_parts``: the gate's uncovered sub-questions, naming a gap the model
    left unnamed. ``sales_contact`` (buying intent): its fixed line is appended with the
    marker of the chunk it was read from.
    """
    chunks = [_Chunk(s) for s in sources]
    english = is_english([c.text for c in draft.claims] or [query])
    kept, dropped = _verify(draft, chunks, english, query)
    if not kept:
        return GroundedAnswer(
            NO_ANSWER_LINE, dropped=dropped, mode=draft.mode, parts=draft.parts
        )
    contact = None
    if sales_contact is not None and not any(
        sales_contact.phone in c.text for c in kept
    ):
        contact = Claim(sales_contact.line, sales_contact.source, sales_contact.phone)
    shown = _fit_length(kept, get_settings().answer_max_words)
    text, citations, selected = _render(
        [*shown, contact] if contact else shown, sources, numbered
    )
    missing = uncovered_parts(draft, kept, query) if draft.parts else []
    if gate_uncovered and missing == []:
        missing = None
    if missing is None and gap_parts:
        missing = _named_gaps(draft, kept, query, gap_parts)
    if missing is None or missing:
        text = f"{text}\n\n{partial_line(missing or [])}"
    return GroundedAnswer(
        text,
        citations,
        selected,
        kept=kept,
        dropped=dropped,
        uncovered=missing if missing is not None else [PARTIAL_FALLBACK_PARTS],
        mode=draft.mode,
        parts=draft.parts,
    )
