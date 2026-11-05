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
   units before it, the lead of the list it is in, or the chunk's breadcrumb /
   heading. A range ("39 to 42") must be stated as a range, so two sources'
   conflicting values are never merged. A count equal to the items of the
   quote's own list ("three themes" over "A · B · C") is backed by it;
5. subjects: a sub-heading / row label of the retrieved pages that the
   question names ("startups", "Strengthen") is named by a claim only when
   the quote sits in that subject's part of the page, and a claim about
   another label of the page ("The Baseline package ...") is dropped unless
   the quote sits under the asked one.

A verbatim quote cited with the wrong Source number is checked against the
chunk that holds it. A claim that only echoes the question ("TARGET MATURITY
LEVEL.") is dropped.

Claims of a part the model marked uncovered, decline / hand-off sentences and
duplicates are dropped too. The reply is rendered ONLY from claims that passed
(with ``[n]`` markers renumbered per document), plus the fixed partial line
naming the parts left without a claim (a price part gets the fixed pricing
line instead). Nothing else is added, so the checked text is the shipped text;
rendering only removes and restructures (``_fit_length``): one list per part
after its lead sentence, at most 8 bullets, restated facts and bare page
labels dropped, and the fixed lines counted in the word cap. All regular expressions are bounded and linear-time.
"""

# pylint: disable=too-many-lines
from __future__ import annotations

import json
import re
import unicodedata
from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.guardrails.post_checks import (
    _unsupported_figures,
    is_decline_sentence,
    is_product_negative,
    unsupported_claims,
)
from app.guardrails.task_type import is_reply_language
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
    PRICING_LINE,
    partial_line,
    sales_contact_line,
)
from app.harness.render_shape import shape_reply
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
# Length rules of the rendered reply (owner): the prompt asks for at most 6
# bullets; code caps a list at 8 so a named set (the Essential Eight) stays
# whole. A lead shared by every bullet of a run (2+ words, 2+ bullets) is cut.
_MAX_LIST_ITEMS = 8
_MIN_SHARED_RUN = 2
_MIN_SHARED_WORDS = 2
# 3+ sentences in a row opening with the same 3+ words: lead + bullets.
_MIN_REPEATED_RUN = 3
_MIN_REPEATED_LEAD = 3
# 3+ sentences in a row ending with the same 4+ words: lead + bullets.
_MIN_SHARED_TAIL = 4
# A sentence of at most 3 capitalised words and no value is a page label.
_FRAGMENT_MAX_WORDS = 3
# "Step 1, Onboard 2. Step 2, Assess 3. ...": a list flattened into a claim.
_DOT_LIST_RE = re.compile(r"\s{0,3}[·•]\s{0,3}")
# A list's shared bullet lead of 3+ words is its lead sentence when it has none.
_MIN_LEAD_WORDS = 3
_SINGULAR_LEAD_RE = re.compile(r"\b(?:is|was)$", re.IGNORECASE)
_INLINE_NUMBER_RE = re.compile(r"(?<=\S)\s{1,3}\d{1,2}[.)]\s{1,3}(?=\S)")
_STEP_PREFIX_RE = re.compile(
    r"^\s{0,3}(?:step|stage|phase)\s{1,3}\d{1,2}\s{0,2}[,:.\u2014\u2013-]?\s{0,3}",
    re.IGNORECASE,
)
# A later sentence of the same part with under a third new content words
# restates the reply (dropped).
_MIN_NEW_SHARE = 1 / 3
_ASK_MIN_SHARE = 0.5
_ENGLISH_MIN_SHARE = 0.12
_ENGLISH_MIN_WORDS = 6
_KB_WORDED_SHARE = 0.75
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
# A capitalised number word ending a name ("Essential Eight", "Top Ten").
_NAME_WORD_NUMBER_RE = re.compile(
    r"\b([A-Z][a-z]{1,30})[ ]{1,2}(Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten"
    r"|Eleven|Twelve)\b"
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
# A zero-padded page numeral copied with its item ("01 Agentic by design",
# "05, Network & Application Security ...").
_PADDED_ENUM_RE = re.compile(r"^\s{0,4}0\d(?:\s{0,2}[,.)])?\s{1,3}(?=\S)")
_PLAN_COMMA_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9]{1,20}(?: [A-Z][A-Za-z0-9]{1,20}){0,2}), ([A-Z][A-Za-z]{1,20}) "
    r"(plan|package|tier|edition)\b"
)
_LITERAL_HEAD_RE = re.compile(r"@|^(?:https?:|www\.)|\.[a-z]{2,6}/?$")
# A list label leaking into the claim ("Steps (in order): Onboard ...").
_STEP_LABEL_RE = re.compile(
    r"^\s{0,4}(?:the\s{1,3})?(?:steps|stages|phases)\s{0,3}(?:\([^()]{0,24}\))?"
    r"\s{0,3}:\s{1,3}(?=\S)",
    re.IGNORECASE,
)
# A count backed by the quote's own list ("three themes" over "A \u00b7 B \u00b7 C").
_LIST_SEP_RE = re.compile(
    r"\s{0,3}(?:[\u00b7\u2022\u2192|;,\n]|\s(?:and|or|&)\s)\s{0,3}(?:and\s{1,3})?"
)
_MAX_COUNT = 12
_PAGE_NUMERAL_RE = re.compile(r"(?<!\d)0(\d)(?!\d)")
_COUNT_TOKEN_RE = re.compile(
    r"(?<![$\w.,])(?:\d{1,2}|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b"
    r"(?![.,]?\d)(?!\s{0,2}(?:%|percent|minutes?|hours?|days?|weeks?|months?|years?)\b)",
    re.IGNORECASE,
)
# A contact value's framing words ("Our phone number is ...", "You can email").
_CONTACT_FRAMING = frozenset(
    {
        "phone", "number", "call", "ring", "email", "mail", "address", "reach",
        "contact", "write", "visit", "office", "team", "line", "details",
        "enquiries", "us", "via", "using", "send", "book",
    }
)  # fmt: skip
# A heading-like line of a chunk (sub-heading / row label), at most 4 words.
_HEADING_LINE_MAX_WORDS = 4
_MIN_SUBJECT_CHARS = 4
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
    r"(?:wh(?:at|o)(?:['\u2019]s|\s{1,3}(?:is|are|was|were))\s{1,3})?",
    re.IGNORECASE,
)
# A part still worded as a question, rewritten as the noun phrase the partial
# line names ("is X as good as Y" -> "how X compares with Y").
_ASK_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), repl)
    for pattern, repl in (
        (
            r"^(?:is|are)\s{1,3}(.+?)\s{1,3}(?:as good as|better than|worse than"
            r"|cheaper than|the same as|similar to|different from|comparable to)"
            r"\s{1,3}(.+)$",
            r"how \1 compares with \2",
        ),
        (r"^how far (?:is|are)\s{1,3}(.+?)\s{1,3}from\s{1,3}(.+)$", r"the distance from \1 to \2"),
        (r"^how many\s{1,3}(.+?)\s{1,3}(?:does|do|did)\s{1,3}(.+?)\s{1,3}have$", r"the number of \1 at \2"),
        (r"^how many\s{1,3}(.+)$", r"the number of \1"),
        (r"^what (?:does|do)\s{1,3}(.+?)\s{1,3}stand for$", r"what \1 stands for"),
        (r"^what (?:does|do)\s{1,3}(.+?)\s{1,3}mean$", r"what \1 means"),
        (r"^(where|when)\s{1,3}(is|are)\s{1,3}(.+)$", r"\1 \3 \2"),
        (
            r"^(.+?)\s{1,3}like(\s{1,3}(?:today|tomorrow|now|right now|this week"
            r"|this weekend))?$",
            r"\1\2",
        ),
    )
)  # fmt: skip
_ASK_YOUR_RE = re.compile(r"\b([Yy])our(s?)\b")
# A label that is still a raw question ("how does ransomware encrypt files").
_RAW_QUESTION_RE = re.compile(
    r"^(?:(?:who|what|when|where|why|how|which)\s{1,3})?"
    r"(?:is|are|was|were|does|do|did|can|could|will|would|should|has|have)\b",
    re.IGNORECASE,
)

# Question framing before a part's subject ("do you have", "is there").
_ASK_FRAME_RE = re.compile(
    r"^(?:(?:do|does|can|could|will|would|is|are)\s{1,3}"
    r"(?:you|we|they|there|it|deploy\s{0,1}iq)\s{1,3}(?:also\s{1,3})?"
    r"(?:(?:have|offer|provide|support|cover|include|do|got)\s{1,3})?)",
    re.IGNORECASE,
)
_ASK_PRONOUN_RE = re.compile(r"^(?:one|any|some)\b", re.IGNORECASE)
_ASK_FIRST_PERSON = frozenset({"i", "me", "my", "i'm", "im"})
_ASK_FILLER = frozenset(
    {
        "thanks",
        "thank",
        "bye",
        "oh",
        "ok",
        "okay",
        "cheers",
        "hi",
        "hello",
        "lol",
        "pls",
    }
)
_PRICE_LABEL_RE = re.compile(
    r"\b(?:how\s{1,3}much|prices?|pricing|priced|costs?|costing|quotes?|fees?)\b",
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
_ANSWER_WORDS = frozenset({"no", "yes"})
_CONJUNCTIONS = frozenset({"and", "or", "nor", "but", "vs", "versus"})
_CLAUSE_SPLIT_RE = re.compile(r"[,;]|[.!?](?=\s)")
_RESTATED_CLAUSE_SHARE = 0.5
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


def _kb_worded(texts: Sequence[str], chunks: Sequence[_Chunk]) -> bool:
    """Terse English claims ("Deploy IQ helps startups gain ...") with few
    function words: most of their words are the (English) chunks' own words,
    so they get the English checks, not the weaker translation checks."""
    joined = " ".join(texts)
    if not joined.isascii():
        return False
    words = [w for w in _words(joined) if not w.isdigit()]
    vocabulary = frozenset().union(*(c.word_set for c in chunks))
    return bool(words) and (
        sum(1 for w in words if w in vocabulary) >= _KB_WORDED_SHARE * len(words)
    )


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
        self.singular_words = frozenset(_singular(w) for w in self.word_set)
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

    def block_lead(self, first: int, back: int) -> str:
        """The ``back`` units before the bullet list that unit ``first`` is in.

        A list's heading scopes every item ("The Intelligence Shift" over
        "- 05 Secure at every step"); the other items are not included, so
        one row's label never backs another row's value.
        """
        if not _BULLET_RE.match(self.units[first]):
            return ""
        start = first
        while start > 0 and _BULLET_RE.match(self.units[start - 1]):
            start -= 1
        if start == first:
            return ""
        return " ".join(self.units[max(0, start - back) : start])

    def local_text(self, first: int, last: int) -> str:
        """The quote's units, the units before them, the list lead and scope."""
        return (
            f"{self.region(first, last, _LOCAL_WINDOW)}\n"
            f"{self.block_lead(first, _LOCAL_WINDOW)}\n{self.scope}"
        )

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
    """Odd/even negators, past a leading answer word ("No, they aren't ...");
    a leading determiner ("No technical expertise required") still counts."""
    words = text.lower().replace(_RSQUO, "'").split()
    lead = words[0].strip(".,;:!") if words else ""
    if lead in _ANSWER_WORDS and (lead != words[0] or len(words) == 1):
        words = words[1:]
    count = 0
    for word in words:
        bare = word.strip(".,;:!?()\"'")
        if bare in _NEGATORS or bare.endswith("n't"):
            count += 1
    # "based on risk rather than location" negates "location" like "risk, not
    # location" does.
    count += len(_CONTRAST_RE.findall(" ".join(words)))
    return count % 2


_CONTRAST_RE = re.compile(r"\b(?:rather\s+than|instead\s+of)\b")
# A claim's segments: clauses, and coordinated predicates ("does not A and
# does not B"); "or" stays inside one ("does not A or B").
_SEGMENT_SPLIT_RE = re.compile(r"[,;.!?]|\b(?:and|but|while|whereas)\b", re.I)
_LEAD_NEGATORS = frozenset({"not", "never", "cannot"})


def _negative_list(chunk: _Chunk, quote: str) -> tuple[str, list[str]] | None:
    """``(lead, items)`` when the quote is a negative list lead-in ("AI does
    not:") and/or items of the bullet list right under it; the items the
    quote spans (all of them for a lead-only quote). ``None`` otherwise."""
    units = chunk.units
    for first, last in chunk.quote_spans(quote):
        lead = first
        if _BULLET_RE.match(units[first]):
            while lead > 0 and _BULLET_RE.match(units[lead - 1]):
                lead -= 1
            lead -= 1
        if lead < 0 or not units[lead].rstrip().endswith(":"):
            continue
        if not _negation_parity(units[lead]):
            continue
        end = lead + 1
        while end < len(units) and _BULLET_RE.match(units[end]):
            end += 1
        if end == lead + 1 or last >= end:
            continue
        items = units[max(first, lead + 1) : last + 1] or units[lead + 1 : end]
        return units[lead], [_BULLET_RE.sub("", i) for i in items]
    return None


def _list_negation_ok(claim: Claim, chunk: _Chunk) -> bool:
    """The lead-in's negation carried into its items: "AI does not:" over
    "- make binding decisions" backs "AI does not make binding decisions".

    Every claim segment restating an item must carry the lead's negator
    (once for "does not A or B", or per item), and its other negators
    ("without") must match the items' own; a segment restating no item
    must carry no negation. "AI makes binding decisions" still fails.
    """
    found = _negative_list(chunk, claim.quote)
    if found is None:
        return False
    lead, items = found
    lead_words = set(_words(lead))
    verdicts = [
        _segment_negation(segment, items, lead_words)
        for segment in _SEGMENT_SPLIT_RE.split(claim.text.replace(_RSQUO, "'"))
        if _words(segment)
    ]
    return True in verdicts and False not in verdicts


def _segment_negation(
    segment: str, items: list[str], lead_words: set[str]
) -> bool | None:
    """One claim segment against a negative list: ``True`` when it restates
    items with their carried negation, ``None`` when it touches no item and
    negates nothing, ``False`` otherwise."""
    seg_words = set(_words(segment))
    hits = []
    for item in items:
        own = set(_words(item)) - lead_words
        if own and len(own & seg_words) / len(own) >= _RESTATED_CLAUSE_SHARE:
            hits.append(item)
    bare = [w.strip(".,;:!?()\"'") for w in segment.lower().split()]
    carried = sum(1 for b in bare if b in _LEAD_NEGATORS or b.endswith("n't"))
    other = sum(
        1
        for b in bare
        if b in _NEGATORS and b not in _LEAD_NEGATORS and not b.endswith("n't")
    )
    if not hits:
        # "... but does deploy changes": an item's words, not restated.
        touched = any(
            (set(_words(i)) - lead_words - _ENGLISH_WORDS) & seg_words for i in items
        )
        return False if carried or other or touched else None
    own_parity = sum(_negation_parity(i) for i in hits) % 2
    return bool(carried) and other % 2 == own_parity


def _restated_parity(claim: str, text: str) -> int:
    """Negation parity of the clauses of ``text`` the claim restates.

    A clause is restated when at least half of its words are in the claim
    (else the one sharing the most words). "are not Services ..., are not
    tailored" backs "they aren't tailored"; "Not on its own. We deliver X."
    does not back "We do not deliver X" (the "Not" is another clause's).
    """
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(text) if _words(c)]
    if len(clauses) <= 1:
        return _negation_parity(text)
    words = set(_words(claim))

    def shared(clause: str) -> float:
        own = set(_words(clause))
        return len(words & own) / len(own)

    restated = [c for c in clauses if shared(c) >= _RESTATED_CLAUSE_SHARE]
    return _negation_parity(" ".join(restated or [max(clauses, key=shared)]))


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
            anchor = _line_anchor(quote, chunk)
            if anchor is None or not _anchored(asserted, anchor):
                return "quote_too_short", ""
            return None, anchor
    evidence = chunk.quote_sentences(quote)
    if evidence is None:
        return "quote_not_in_source", ""
    return None, evidence


def _line_anchor(quote: str, chunk: _Chunk) -> str | None:
    """A one-word quote that is a whole list item / heading line of the chunk
    ("- Identity" under "## Zero Trust Pillars"), re-anchored to that line
    with its list lead-in and section heading; ``None`` when no line of the
    chunk is exactly the quote."""
    target = _norm(quote)
    if not target:
        return None
    meta = chunk.source.metadata or {}
    heading = str(meta.get("section_heading") or "")
    for index, unit in enumerate(chunk.units):
        if _norm(_BULLET_RE.sub("", unit).lstrip("# ")) == target:
            lead = chunk.block_lead(index, 1)
            return f"{unit.strip()}\n{lead}\n{heading}"
    return None


def _anchored(asserted: set[str], anchor: str) -> bool:
    """Every word the claim asserts is the item's or its lead / heading's
    ("Identity is one of the Zero Trust pillars")."""
    words = {_singular(w) for w in _words(anchor)}
    return {_singular(w) for w in asserted if w != "one"} <= words


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


def _named_number_words(text: str) -> tuple[str, list[str]]:
    """(``text`` without them, digits of) number words that end a name
    ("Essential Eight": capitalised, after a capitalised non-function word)."""
    names: list[str] = []

    def cut(match: re.Match[str]) -> str:
        if match.group(1).lower() in _NOT_NAMES | _ENGLISH_WORDS:
            return match.group(0)
        names.extend(_number_words(match.group(2)))
        return match.group(1)

    return _NAME_WORD_NUMBER_RE.sub(cut, text), names


def _value_reason(claim: Claim, span: _Chunk, local: _Chunk) -> str | None:
    """Values sit in the quote's own units; name numbers / frameworks nearby."""
    figures = _Claim(claim.text)
    named = _name_numbers(claim.text)
    plain, named_words = _named_number_words(claim.text)
    backed = (
        all(
            (local if n in named else span).index.has_number(n) for n in figures.numbers
        )
        and all(span.index.has_number(n) for n in _number_words(plain))
        and all(local.index.has_number(n) for n in named_words)
        and _unit_pairs(_words(claim.text)) <= span.pairs
        and not _unsupported_figures(claim.text, span.raw)
        and (
            not figures.frameworks or bool(figures.frameworks & local.index.frameworks)
        )
    )
    if not backed:
        return "value_not_in_quote"
    # "39 to 42": a range the source does not state merges two sources' values.
    if not _ranges(claim.text) <= _ranges(span.raw):
        return "merged_values"
    return None


def _spelled_out(name: str, text: str) -> bool:
    """An acronym the quote spells out ("ISMS" over "Information Security
    Management System"): consecutive capitalised words with its initials."""
    if not (name.isupper() and name.isalpha() and 2 <= len(name) <= 6):  # noqa: PLR2004
        return False
    words = re.findall(r"[A-Za-z]+", text)
    size = len(name)
    return any(
        all(w[:1].isupper() for w in words[i : i + size])
        and "".join(w[0] for w in words[i : i + size]) == name
        for i in range(len(words) - size + 1)
    )


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
        if _spelled_out(name, span.raw):
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
        local = chunk.view(chunk.local_text(first, last))
        reason = _value_reason(claim, span, local)
        if reason is None and _unique_contact(claim, chunk):
            # The page's one phone / email is not a row value of a subject.
            return None
        if reason is None:
            reason = _subject_reason(
                claim, chunk, span=span, local=local, subjects=subjects, english=english
            )
        if reason is None:
            return None
    return reason


def _contact_values(text: str) -> set[str]:
    """Phone numbers (digits only) and emails (lower case) written in ``text``."""
    phones = {re.sub(r"\D", "", m.group(0)) for m in _PHONE_RE.finditer(text)}
    return phones | {m.group(0).lower() for m in _EMAIL_RE.finditer(text)}


def _unique_contact(claim: Claim, chunk: _Chunk) -> bool:
    """The claim states a phone / email of the quote, the chunk's only one each."""
    values = _contact_values(claim.text)
    if not values or not values <= _contact_values(claim.quote):
        return False
    text = chunk.source.content
    phones = {re.sub(r"\D", "", m.group(0)) for m in _PHONE_RE.finditer(text)}
    emails = {m.group(0).lower() for m in _EMAIL_RE.finditer(text)}
    return len(phones) <= 1 and len(emails) <= 1


def _framed_contact(claim: Claim, chunk: _Chunk) -> bool:
    """ "Our phone number is 1300 ..." over "Call 1300 ...": only framing words
    around a contact value of the quote are not in the chunk."""
    values = _contact_values(claim.text)
    if not values or not values <= _contact_values(claim.quote):
        return False
    bare = _EMAIL_RE.sub(" ", claim.text)
    for word in _words(bare):
        if word.isdigit() or word in _ENGLISH_WORDS or word in _NOT_NAMES:
            continue
        if word not in _CONTACT_FRAMING and not chunk.has_stem(word):
            return False
    return True


def _list_counts(claim: Claim, chunk: _Chunk) -> set[str]:
    """Item counts of the lists the quote spans ("A · B · C" -> {"3"})."""
    counts: set[str] = set()
    for first, last in chunk.quote_spans(claim.quote):
        text = "\n".join(_BULLET_RE.sub("", u) for u in chunk.units[first : last + 1])
        pieces = [p for p in _LIST_SEP_RE.split(text) if _norm(p)]
        if 1 < len(pieces) <= _MAX_COUNT:
            counts.add(str(len(pieces)))
        # "principle two" over "02 Continuous automation": the page numeral.
        counts |= {str(int(n)) for n in _PAGE_NUMERAL_RE.findall(text)}
    return counts


def _without_counts(text: str, counts: set[str]) -> str:
    """``text`` without the numbers that count the quote's list items."""

    def keep(match: re.Match[str]) -> str:
        token = match.group(0).lower()
        return "" if _NUMBER_WORDS.get(token, token) in counts else match.group(0)

    return _COUNT_TOKEN_RE.sub(keep, text) if counts else text


def _singular(word: str) -> str:
    return word[:-1] if len(word) > _MIN_SUBJECT_CHARS and word.endswith("s") else word


@dataclass(frozen=True, slots=True)
class _Asked:
    """What the question names: its text, the retrieved labels it names
    (``labels``) and those that are one page part's subject (``audiences``).
    ``pages``: each retrieved document's chunks joined, by document id."""

    query: str
    labels: tuple[frozenset[str], ...]
    audiences: tuple[frozenset[str], ...]
    pages: dict[str, str] = field(default_factory=dict)


def _chunk_labels(chunk: _Chunk) -> list[frozenset[str]]:
    """Content words of the chunk's sub-headings / row labels ("Startups",
    "Strengthen"): lines of at most 4 words without closing punctuation."""
    brand = _brand_words()
    labels: list[frozenset[str]] = []
    for line in chunk.source.content.split("\n")[1:]:
        plain = _EMPHASIS_RE.sub("", _BULLET_RE.sub("", line)).strip(" #")
        words = _words(plain)
        if not words or len(words) > _HEADING_LINE_MAX_WORDS or plain[-1:] in _TERMINAL:
            continue
        content = frozenset(
            _singular(w)
            for w in words
            if len(w) >= _MIN_SUBJECT_CHARS
            and w not in _ENGLISH_WORDS
            and w not in _NOT_NAMES
            and w not in brand
        )
        if content:
            labels.append(content)
    return labels


def _asked_labels(query: str, chunks: Sequence[_Chunk]) -> list[frozenset[str]]:
    """The retrieved chunks' labels the question names."""
    query_words = {_singular(w) for w in _words(query)}
    found = {a for c in chunks for a in _chunk_labels(c) if a <= query_words}
    return sorted(found, key=sorted)


def _audiences(
    asked: Sequence[frozenset[str]], chunks: Sequence[_Chunk]
) -> list[frozenset[str]]:
    """The asked labels that are a subject of one part of a page.

    "Is the platform suitable for startups?" with a "Startups" sub-heading
    in a retrieved chunk: a claim naming startups must quote that subject's
    own part of a page. A word most retrieved chunks use ("security") is the
    topic, not such a subject.
    """
    return [
        a
        for a in asked
        if 2 * sum(1 for c in chunks if a <= c.singular_words) <= len(chunks)
    ]


def _local_texts(claim: Claim, chunk: _Chunk) -> list[set[str]]:
    """Singular words of each place the quote occurs (with its local context)."""
    spans = chunk.quote_spans(claim.quote)
    texts = [chunk.local_text(f, last) for f, last in spans] or [chunk.raw]
    return [{_singular(w) for w in _words(t)} for t in texts]


def _other_subject_reason(
    claim: Claim, chunk: _Chunk, asked: Sequence[frozenset[str]], query: str
) -> str | None:
    """ "The Baseline package includes ..." answering "Strengthen": a claim
    naming another label of the page, not the one asked, from a part of the
    page the asked one is not in."""
    if not asked or claim.kind == "item":
        return None
    claim_words = {_singular(w) for w in _words(claim.text)}
    if any(a <= claim_words for a in asked):
        return None
    query_words = {_singular(w) for w in _words(query)}
    # A chunk that never names the asked subject ("Customer Data" asked, a
    # privacy list of "information" uses): any of its labels in the claim is
    # another subject, verb or not.
    covers_asked = any(a <= chunk.singular_words for a in asked)
    # The label is the claim's subject: the claim says something about it
    # (a bare list item "Support" is that label, not a claim about it).
    others = [
        label
        for label in _chunk_labels(chunk)
        if label <= claim_words
        and not label & query_words
        # "Call / 1300 394 468", "- 30 minutes": a value line is no subject.
        and not all(w.isdigit() or w in _UNITS for w in label)
        and len(claim_words - label - _ENGLISH_WORDS) >= _MIN_SHARED_WORDS
        and (not covers_asked or _names_label(claim, chunk, label))
    ]
    if not others:
        return None
    if any(any(a <= local for a in asked) for local in _local_texts(claim, chunk)):
        return None
    return "other_subject"


def _names_label(claim: Claim, chunk: _Chunk, label: frozenset[str]) -> bool:
    """The claim names the label as a subject: capitalised ("The Baseline
    package"), or the label heads the part of the page the quote is in.
    "approves ... before agents apply it" over a far "4. Approve / 5. Apply"
    step list uses the words as verbs, not as those steps."""
    capitalised = {
        _singular(w.lower()) for w in _TOKEN_RE.findall(claim.text) if w[:1].isupper()
    }
    if label <= capitalised:
        return True
    spans = chunk.quote_spans(claim.quote)
    if not spans:
        return True
    for first, _last in spans:
        before = "\n".join(chunk.units[max(0, first - _LOCAL_WINDOW) : first])
        lead = chunk.block_lead(first, _LOCAL_WINDOW)
        context = chunk.view(f"\n{before}\n{lead}")
        scope = {_singular(w) for w in _words(chunk.scope)}
        if label <= scope or label in _chunk_labels(context):
            return True
    return False


def _audience_reason(
    claim: Claim,
    chunk: _Chunk,
    audiences: Sequence[frozenset[str]],
    asked: _Asked | None = None,
) -> str | None:
    """A claim naming a subject of the question quotes that subject's part."""
    claim_words = {_singular(w) for w in _words(claim.text)}
    named = [a for a in audiences if a <= claim_words]
    if not named:
        return None
    for local in _local_texts(claim, chunk):
        # "The Baseline stage" for "the Baseline maturity stage": "stage"
        # only qualifies the asked label the quote's part is about.
        heads = [
            a
            for a in (asked.labels if asked else ())
            if a <= local and a <= claim_words
        ]
        missing = [a for a in named if not a <= local]
        if not missing or (
            asked is not None
            and all(
                any(
                    _qualifies(h, a, claim.text) and _qualifies(h, a, asked.query)
                    for h in heads
                )
                for a in missing
            )
        ):
            return None
    return "subject_not_local"


def _qualifies(head: frozenset[str], word: frozenset[str], text: str) -> bool:
    """``word`` follows ``head`` in ``text`` with at most one word between
    and no conjunction ("Baseline maturity stage"; not "startups and
    enterprises")."""
    words = [_singular(w) for w in _words(text)]
    for index, current in enumerate(words):
        if current not in head:
            continue
        after = words[index + 1 : index + 3]
        for gap, candidate in enumerate(after):
            if (
                candidate in word
                and len(word) == 1
                and not any(w in _CONJUNCTIONS for w in after[:gap])
            ):
                return True
    return False


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
        own = [s for s in subjects if set(_words(s)) <= ask]
        # "zero trust basics; frontier package": a part naming its own
        # framework is never held to another part's subject ("Frontier").
        if own or _Claim(parts[part].ask).frameworks:
            return own
        return subjects
    return subjects


def _echo_overlap(claim: Claim, chunk: _Chunk, query: str) -> bool:
    """A list item framed in the question's words ("The service handles
    Cryptography" for "... areas you handle"): its other words overlap."""
    if claim.kind != "item" or not query:
        return False
    asked = {_singular(w) for w in _words(query)}
    rest = " ".join(w for w in claim.text.split() if _singular(_norm(w)) not in asked)
    figures = _Claim(rest)
    return bool(figures.stems) and (
        _overlap(figures, chunk.index) >= get_settings().citation_min_overlap
    )


def _answered_no(claim: Claim, chunk: _Chunk) -> bool:
    """ "We don't certify ..." over "No. We focus on ...": the chunk asks
    "Will DeployIQ certify us ...?" right before the quote's leading "No.",
    and the claim negates that question's own verb, not another word of
    the quote ("We don't focus on ISO 42001" is still a mismatch)."""
    if not re.match(r"\s{0,4}no\s{0,2}[.!]", claim.quote, re.IGNORECASE):
        return False
    lowered = claim.text.lower().replace(_RSQUO, "'")
    negator = re.search(r"\b(?:not|never|no)\b|n't\b", lowered)
    if negator is None:
        return False
    clause = _CLAUSE_SPLIT_RE.split(lowered[negator.end() :])[0]
    negated = {w[:_STEM_CHARS] for w in _words(clause) if len(w) >= _MIN_SUBJECT_CHARS}
    for first, _last in chunk.quote_spans(claim.quote):
        question = chunk.units[first - 1].strip() if first else ""
        if not question.endswith("?"):
            continue
        names = {_norm(n) for n, _initial in _names(question)}
        asked = {
            w[:_STEM_CHARS]
            for w in _words(question)
            if len(w) >= _MIN_SUBJECT_CHARS and w not in names and not w.isdigit()
        }
        if negated & asked:
            return True
    return False


def _english_reason(
    claim: Claim, chunk: _Chunk, evidence: str, query: str = "", page: str = ""
) -> str | None:
    brand = _brand_words()
    for name, initial in _names(claim.text):
        if _norm(name) in brand or chunk.has_word(name):
            continue
        # "MDR" over the chunk's "Managed Detection & Response".
        if _spelled_out(name, chunk.raw):
            continue
        # "Email info@... to book" over "Mail info@...": a framing word
        # around the quote's own contact value, not a name.
        if _norm(name) in _CONTACT_FRAMING and _framed_contact(claim, chunk):
            continue
        if not initial or not chunk.has_stem(name):
            return "name_not_in_source"
    figures = _Claim(claim.text)
    # A short item with no content stem ("Support", "ML3") is the quote's
    # own words; it has nothing to overlap.
    bare = not figures.stems and set(_words(claim.text)) <= set(_words(claim.quote))
    low = _overlap(figures, chunk.index) < get_settings().citation_min_overlap
    if (
        low
        and not bare
        and not _framed_contact(claim, chunk)
        and not _echo_overlap(claim, chunk, query)
    ):
        return "low_overlap"
    parity = _negation_parity(claim.text)
    if parity not in {
        _restated_parity(claim.text, claim.quote),
        _restated_parity(claim.text, evidence),
    } and not (_answered_no(claim, chunk) or _list_negation_ok(claim, chunk)):
        return "negation_mismatch"
    # "The free assessment takes only 5 minutes" over "Takes only 5 minutes":
    # "free" may come from the same page, about the same subject.
    if unsupported_claims(claim.text, evidence, page_text=page):
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
    claim: Claim,
    chunk: _Chunk,
    english: bool,
    subjects: Sequence[str] = (),
    asked: _Asked | None = None,
) -> str | None:
    """Why ``claim`` is not backed by ``chunk`` (``None`` when it is)."""
    reason, evidence = _quote_reason(claim, chunk)
    if reason is None:
        reason = _item_name_reason(claim, chunk)
    # "The three themes are A, B and C" over "A · B · C": the count is the
    # quote's own list, every other figure is still checked.
    counted = _list_counts(claim, chunk) if reason is None else set()
    figures = Claim(
        _without_counts(claim.text, counted),
        claim.source,
        claim.quote,
        claim.part,
        claim.kind,
    )
    if reason is None:
        reason = _figure_reason(figures, chunk)
    if reason is None:
        query = asked.query if asked else ""
        doc = chunk.source.document_id
        page = asked.pages.get(str(doc), "") if asked and doc else ""
        reason = (
            _english_reason(claim, chunk, evidence, query, page)
            if english
            else _other_language_reason(claim, chunk)
        )
    if reason is None:
        reason = _local_reason(figures, chunk, english, subjects)
    if reason is None and asked is not None:
        reason = _audience_reason(claim, chunk, asked.audiences, asked)
        if reason is None:
            reason = _other_subject_reason(claim, chunk, asked.labels, asked.query)
    return reason


# "Assess: Evaluate ..." - an item's name before its copy.
_ITEM_NAME_RE = re.compile(r"^\s{0,3}([^:.!?\n]{1,60}?):\s{1,3}\S")
# Heading numbering ("01 — Assess", "Step 2 - Assess").
_HEADING_NUMBER_RE = re.compile(r"^\W{0,3}(?:step\s{1,3})?\d{1,2}\W{0,4}", re.I)


def _item_name_reason(claim: Claim, chunk: _Chunk) -> str | None:
    """A "Name: copy" claim whose name is not in its quote must name that
    chunk's own section: its last breadcrumb item ("Zero Trust › Our
    Delivery Approach › 01 — Assess") or a heading / label line of its body,
    never a parent heading or another chunk's item."""
    match = _ITEM_NAME_RE.match(claim.text)
    if match is None:
        return None
    name = _norm(match.group(1))
    if not name or len(name.split()) > _HEADING_LINE_MAX_WORDS:
        return None
    if f" {name} " in f" {_norm(claim.quote)} ":
        return None
    leaf = _norm(_HEADING_NUMBER_RE.sub("", _crumb_text(chunk).strip()))
    if leaf == name:
        return None
    lines = (
        _norm(_HEADING_NUMBER_RE.sub("", _BULLET_RE.sub("", unit).lstrip("# ")))
        for unit in chunk.units[1:]
    )
    return None if name in lines else "item_name_not_heading"


def _find_source(
    claim: Claim,
    chunks: list[_Chunk],
    english: bool,
    subjects: Sequence[str],
    asked: _Asked | None = None,
) -> int:
    """The first chunk that backs the claim (plain mode without a marker, or
    a verbatim quote cited with the wrong Source number); 0 when none does."""
    for number, chunk in enumerate(chunks, start=1):
        if number == claim.source or chunk.quote_sentences(claim.quote) is None:
            continue
        if _check(claim, chunk, english, subjects, asked) is None:
            return number
    return 0


def _is_decline(text: str) -> bool:
    """An absence / hand-off sentence (the fixed lines replace those)."""
    return is_decline_sentence(text) and _CONTACT_RE.search(text) is None


_QUOTE_NEGATOR_RE = re.compile(r"\b(?:not|no|never|without|excludes?)\b|n't\b", re.I)


def _quoted_negative(claim: Claim, cited: str) -> bool:
    """ "Baseline does not include 24/7 MDR" over "MDR: ... not included in
    Baseline": a product fact the quote itself negates, not a decline. The
    quote must not be an absence line, and the claim's content words must
    be the quote's or the cited chunk's (stem match); the negation is then
    verified like any other claim's."""
    quote = claim.quote.replace(_RSQUO, "'")
    if (
        not is_product_negative(claim.text)
        or not _QUOTE_NEGATOR_RE.search(quote)
        or is_decline_sentence(quote, handoff=False)
    ):
        return False
    stems = {w[:_STEM_CHARS] for w in _words(f"{quote} {cited}")}
    # "included only in Frontier" / "the other packages" over "included in
    # Frontier; not included in Baseline, ...": the quote's own contrast.
    skip = _EXCLUSIVE_WORDS if _contrasted(quote) else frozenset()
    content = [
        w
        for w in _words(claim.text)
        if len(w) >= _MIN_SUBJECT_CHARS and w not in _ENGLISH_WORDS | _NOT_NAMES | skip
    ]
    return bool(content) and all(w[:_STEM_CHARS] in stems for w in content)


_EXCLUSIVE_WORDS = frozenset({"only", "other", "others"})


def _page_copy(claim: Claim, cited: str) -> bool:
    """ "Access guides, get help, or contact our support team" quoting that
    page line: the page's own copy, not a hand-off the model wrote. Every
    claim word is the quote's and the quote is verbatim in the cited chunk;
    the claim is then verified like any other."""
    quote = _norm(claim.quote)
    return (
        bool(quote)
        and quote in _norm(cited)
        and set(_words(claim.text)) <= set(quote.split())
    )


def _contrasted(quote: str) -> bool:
    """The quote states one verb both affirmed and negated ("included in
    Frontier; not included in Baseline")."""
    affirmed: set[str] = set()
    negated: set[str] = set()
    for clause in re.split(r"[;,.]", quote):
        stems = {w[:_STEM_CHARS] for w in _words(clause) if len(w) >= _STEM_CHARS}
        (negated if _QUOTE_NEGATOR_RE.search(clause) else affirmed).update(stems)
    return bool(affirmed & negated)


def _clean_text(claim: Claim) -> Claim:
    raw = claim.text
    # A quote pasted into the text ("... (quote: ...)") is cut, never shipped.
    cut = _QUOTE_LABEL_RE.search(raw)
    if cut is not None:
        raw = raw[: cut.start()]
    raw = _ENUMERATOR_RE.sub("", _BULLET_RE.sub("", _EMPHASIS_RE.sub("", raw)))
    raw = _STEP_LABEL_RE.sub("", _PADDED_ENUM_RE.sub("", raw))
    # "No, ..." / "Yes, ..." is an inference on top of the quote: cut it, the
    # rest is verified on its own.
    raw = _YES_NO_RE.sub("", raw)
    raw = raw.translate(_QUOTE_CHARS)
    text = polish_claim(strip_markers(raw))
    # A trailing comma copied from a list row would render as "matures.,.".
    text = " ".join(_BULLET_RE.sub("", text).split()).rstrip(" ,;")
    # "The Zero Trust, Starter plan" (a page's "Zero Trust - Starter"): one name.
    text = _PLAN_COMMA_RE.sub(r"\1 \2 \3", text)
    head = text.split(" ", 1)[0]
    # "info@deployiq.co" / "www..." stay as written.
    if text[:1].islower() and text[1:2].islower() and not _LITERAL_HEAD_RE.search(head):
        text = text[:1].upper() + text[1:]
    return Claim(text, claim.source, claim.quote, claim.part, claim.kind)


def _pages(chunks: Sequence[_Chunk]) -> dict[str, str]:
    """Each retrieved document's chunks (with their headings), joined."""
    pages: dict[str, list[str]] = {}
    for chunk in chunks:
        if chunk.source.document_id:
            pages.setdefault(str(chunk.source.document_id), []).append(chunk.raw)
    return {doc: "\n".join(texts) for doc, texts in pages.items()}


# ---------------------------------------------------------------------------
# Subject attribution: a claim about X quotes X's own section
# ---------------------------------------------------------------------------

_CRUMB_SEP = "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"  # "Page › Section › Sub-section" (each chunk's first line)
_KIND_MAX_WORDS = 2  # "Strengthen package", "Foundation maturity stage"
# Degree words that strengthen wording, weakest tier first: a claim's word
# needs one of the same or a stronger tier in the quote ("Comprehensive
# protection" never backs "full protection").
_DEGREE_TIERS = (
    re.compile(r"\b(?:comprehensive|thorough|extensive)(?:ly)?\b", re.I),
    re.compile(
        r"\b(?:full|fully|(?<!to )complete|completely|total|totally|entire|entirely|whole"
        r"|absolute|absolutely|perfect|perfectly)\b",
        re.I,
    ),
)


def _label_words(text: str) -> frozenset[str]:
    """Content words of a heading ("Package cards" -> {"package", "card"})."""
    skip = _ENGLISH_WORDS | _NOT_NAMES | _brand_words()
    return frozenset(
        _singular(w)
        for w in _words(text)
        if len(w) >= _MIN_SUBJECT_CHARS and w not in skip
    )


def _crumb_text(chunk: _Chunk, index: int = -1) -> str:
    """One raw part of the chunk's breadcrumb (the leaf by default)."""
    head = next((line for line in chunk.source.content.split("\n") if line.strip()), "")
    return head.split(_CRUMB_SEP)[index]


def _crumbs(chunk: _Chunk) -> tuple[str, ...]:
    """The chunk's breadcrumb ("Page › Section › Sub-section"), normalised."""
    head = next((line for line in chunk.source.content.split("\n") if line.strip()), "")
    return tuple(_norm(part) for part in head.split(_CRUMB_SEP))


def _section(chunk: _Chunk) -> tuple[tuple[str, ...], frozenset[str]] | None:
    """(parent breadcrumb, leaf heading words) of a sub-section chunk."""
    crumbs = _crumbs(chunk)
    if len(crumbs) < 2:  # noqa: PLR2004 - a page-level chunk has no leaf
        return None
    leaf = _label_words(crumbs[-1])
    if not leaf or len(crumbs[-1].split()) > _HEADING_LINE_MAX_WORDS:
        return None
    return crumbs[:-1], leaf


def _singular_set(text: str) -> set[str]:
    return {_singular(w) for w in _words(text)}


def _sibling_labels(
    chunk: _Chunk,
    parent: tuple[str, ...],
    leaf: frozenset[str],
    chunks: Sequence[_Chunk],
) -> list[frozenset[str]]:
    """Other subjects under the same parent heading: retrieved sibling
    sections ("Package cards › Frontier" beside "… › Strengthen") and the
    labels the parent section's own chunk lists."""
    labels: list[frozenset[str]] = []
    for other in chunks:
        if other is chunk:
            continue
        section = _section(other)
        if section is not None and section[0] == parent:
            labels.append(section[1])
        if _crumbs(other) == parent:
            labels.extend(_chunk_labels(other))
    # "Compliance" beside "Compliance Score" is no other subject.
    return [label for label in labels if not label & leaf]


def _names_sibling_in_quote(claim: Claim, chunk: _Chunk, label: frozenset[str]) -> bool:
    """ "Everything in Strengthen, plus:" on the Frontier card: a one-line
    quote that itself names the sibling the claim names."""
    spans = chunk.quote_spans(claim.quote)
    return bool(spans) and all(
        first == last and label <= _singular_set(chunk.units[first])
        for first, last in spans
    )


def _sibling_reason(
    claim: Claim, chunk: _Chunk, chunks: Sequence[_Chunk], query: str
) -> str | None:
    """ "24/7 MDR" (the Frontier card) answering "the Strengthen package": a
    quote under a sibling heading backs no claim about another sibling."""
    section = _section(chunk)
    # Only a sub-section of a named group ("Package cards › Strengthen") is
    # one of several peer subjects; a page's top-level sections are not.
    if section is None or len(section[0]) < 2:  # noqa: PLR2004
        return None
    parent, leaf = section
    claim_words = _singular_set(claim.text)
    if leaf <= claim_words:
        return None
    siblings = _sibling_labels(chunk, parent, leaf, chunks)
    if any(
        s <= claim_words and not _names_sibling_in_quote(claim, chunk, s)
        for s in siblings
    ):
        return "other_section_subject"
    query_words = _singular_set(query)
    own = _crumb_text(chunk)
    # "MFA" asks about the "Multi-Factor Auth" section itself.
    if leaf <= query_words or any(
        _spelled_out(name, own)
        for name in _CODE_NAME_RE.findall(f"{query} {claim.text}")
    ):
        return None
    if any(s <= query_words for s in siblings):
        return "other_section_subject"
    return None


def _kind_words(leaf: frozenset[str], text: str) -> set[str]:
    """The content words right after the subject's name ("Strengthen
    package" -> {"package"}): which kind of item ``text`` means."""
    words = [_singular(w) for w in _words(text)]
    skip = _ENGLISH_WORDS | _NOT_NAMES | _CONJUNCTIONS
    kinds: set[str] = set()
    for index, word in enumerate(words):
        if word not in leaf:
            continue
        for after in words[index + 1 : index + 1 + _KIND_MAX_WORDS]:
            if after in skip or after in leaf or len(after) < _MIN_SUBJECT_CHARS:
                break
            kinds.add(after)
    return kinds


def _other_kind_reason(
    claim: Claim, chunk: _Chunk, chunks: Sequence[_Chunk], query: str
) -> str | None:
    """ "The Strengthen package ..." quoting home's "Strengthen" roadmap card:
    a same-named item of another kind on another page. Dropped only when a
    retrieved same-named section of another page is headed by that kind
    ("Microsoft 365 Security Packages › … › Strengthen") and none of this
    page's same-named sections uses the word."""
    section = _section(chunk)
    if section is None:
        return None
    leaf = section[1]
    kinds = _kind_words(leaf, claim.text) | _kind_words(leaf, query)
    if not kinds:
        return None
    named = [c for c in chunks if (s := _section(c)) is not None and s[1] == leaf]
    doc = chunk.source.document_id
    if any(kinds & c.singular_words for c in named if c.source.document_id == doc):
        return None
    for other in named:
        if other.source.document_id == doc:
            continue
        headed = _singular_set(other.scope).union(*_chunk_labels(other))
        if kinds & headed:
            return "other_kind_subject"
    return None


def _stretched_reason(claim: Claim, chunk: _Chunk) -> str | None:
    """ "full protection" over "Comprehensive protection": a degree word the
    quote's sentences do not use (or a stronger one) strengthens the page."""
    evidence = chunk.quote_sentences(claim.quote) or claim.quote
    for tier, pattern in enumerate(_DEGREE_TIERS):
        if pattern.search(claim.text) and not any(
            p.search(evidence) for p in _DEGREE_TIERS[tier:]
        ):
            return "stretched_wording"
    return None


def _page_degree(claim: Claim, original: Claim, chunks: Sequence[_Chunk]) -> Claim:
    """Keep the page's own degree word the polish swapped for a stronger one
    ("comprehensive" -> "full"): the model's word when the quote uses it."""
    if not 1 <= claim.source <= len(chunks):
        return claim
    evidence = chunks[claim.source - 1].quote_sentences(claim.quote) or claim.quote
    weak, strong = _DEGREE_TIERS
    page = weak.search(evidence)
    said = weak.search(original.text)
    swapped = strong.search(claim.text)
    if not (page and said and swapped) or strong.search(evidence):
        return claim
    if said.group(0).lower() != page.group(0).lower():
        return claim
    word = said.group(0).lower()
    if swapped.group(0)[:1].isupper():
        word = word.capitalize()
    text = f"{claim.text[: swapped.start()]}{word}{claim.text[swapped.end() :]}"
    return Claim(text, claim.source, claim.quote, claim.part, claim.kind)


def _attribution_reason(
    claim: Claim, chunks: Sequence[_Chunk], query: str
) -> str | None:
    """A verified claim's quote is about the claim's own subject, in the
    page's own strength of wording (``None`` when it is)."""
    if not 1 <= claim.source <= len(chunks):
        return None
    chunk = chunks[claim.source - 1]
    return (
        _stretched_reason(claim, chunk)
        or _sibling_reason(claim, chunk, chunks, query)
        or _other_kind_reason(claim, chunk, chunks, query)
    )


_LABEL_SEP_RE = re.compile(r"^\s{0,3}[:\-\u2013\u2014]\s{0,3}")


def _label_body_quote(claim: Claim, chunks: Sequence[_Chunk]) -> Claim:
    """ "Zero Trust Services: Deploy IQ delivers Zero Trust as ..." quoting
    only the card heading: the quote becomes the description, when the cited
    chunk has it word for word (the heading alone backs none of it)."""
    if not 1 <= claim.source <= len(chunks):
        return claim
    quote = claim.quote.strip().rstrip(_TERMINAL)
    text = claim.text.strip()
    if not quote or _norm(text[: len(quote)]) != _norm(quote):
        return claim
    rest = _LABEL_SEP_RE.sub("", text[len(quote) :], count=1).strip()
    if rest == text[len(quote) :].strip() or not rest:
        return claim  # no "Label: body" shape
    body = rest.rstrip(_TERMINAL)
    if _norm(body) and _norm(body) in chunks[claim.source - 1].norm:
        return Claim(claim.text, claim.source, body, claim.part, claim.kind)
    return claim


def _verify(
    draft: Draft, chunks: list[_Chunk], english: bool, query: str = ""
) -> tuple[list[Claim], list[tuple[Claim, str]]]:
    kept: list[Claim] = []
    dropped: list[tuple[Claim, str]] = []
    seen: set[str] = set()
    labels = tuple(_asked_labels(query, chunks))
    asked = _Asked(query, labels, tuple(_audiences(labels, chunks)), _pages(chunks))
    for original in _names_into_list(list(draft.claims)):
        claim = _label_body_quote(_clean_text(original), chunks)
        cited = chunks[claim.source - 1].raw if 1 <= claim.source <= len(chunks) else ""
        reason = _precheck(claim, draft.parts, seen, cited)
        if reason is None and _echoes_question(claim.text, query):
            reason = "label_only"
        subjects = _query_subjects(query, draft.parts, claim.part, chunks)
        if reason is None and draft.mode == PLAIN_MODE and claim.source == 0:
            claim = _with_source(
                claim, _find_source(claim, chunks, english, subjects, asked)
            )
        if reason is None and not 1 <= claim.source <= len(chunks):
            reason = "bad_source"
        if reason is None:
            reason = _check(claim, chunks[claim.source - 1], english, subjects, asked)
        if reason in ("quote_not_in_source", "bad_source"):
            # The quote is verbatim in another retrieved chunk: the model
            # cited the wrong Source number; that chunk is checked instead.
            number = _find_source(claim, chunks, english, subjects, asked)
            if number:
                claim, reason = _with_source(claim, number), None
        claim = _page_degree(claim, original, chunks) if reason is None else claim
        reason = reason or _attribution_reason(claim, chunks, query)
        reason = reason or _fragment_reason(claim, chunks, query)
        if reason is None:
            seen.add(_norm(claim.text))
            kept.append(claim)
        else:
            dropped.append((original, reason))
    return kept, dropped


def _with_source(claim: Claim, source: int) -> Claim:
    return Claim(claim.text, source, claim.quote, claim.part, claim.kind)


def _echoes_question(text: str, query: str) -> bool:
    """A label that only repeats the question ("TARGET MATURITY LEVEL.")."""
    words = _words(text)
    if not words or len(words) > _LABEL_MAX_WORDS or _DIGIT_RE.search(text):
        return False
    content = {_singular(w) for w in words if w not in _ENGLISH_WORDS | _NOT_NAMES}
    return bool(content) and content <= {_singular(w) for w in _words(query)}


def _page_term(text: str, phrase: str, cited: str) -> bool:
    """Every use of ``phrase`` in ``text`` is the cited chunk's own term: the
    content word before it comes with it ("Downloadable Materials"), never
    a function word ("the materials say")."""
    words, target = _norm(text).split(), _norm(phrase).split()
    page = f" {_norm(cited)} "
    found = False
    for index in range(1, len(words) - len(target) + 1):
        if words[index : index + len(target)] != target:
            continue
        found = True
        term = " ".join(words[index - 1 : index + len(target)])
        if words[index - 1] in _ENGLISH_WORDS | _NOT_NAMES or f" {term} " not in page:
            return False
    return found and words[: len(target)] != target


def _precheck(  # pylint: disable=too-many-return-statements
    claim: Claim, parts: list[Part], seen: set[str], cited: str = ""
) -> str | None:
    if not claim.text:
        return "empty"
    lowered = claim.text.lower()
    quote = claim.quote.lower()
    if any(
        p in lowered and p not in quote and not _page_term(lowered, p, cited)
        for p in _BANNED_LOWER
    ):
        # Banned stock / internal wording ("the materials say") never ships;
        # the page's own words ("Downloadable Materials", in the quote or
        # the cited chunk) are a fact, not stock wording. The claim is
        # dropped, nothing is rewritten.
        return "banned_wording"
    if _norm(claim.text) in seen:
        return "duplicate"
    if _DANGLING_END_RE.search(claim.text):
        return "truncated"
    if (
        _is_decline(claim.text)
        and not _quoted_negative(claim, cited)
        and not _page_copy(claim, cited)
    ):
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
    label = _noun_phrase(label, query)
    if label is None:
        return None
    # "The price" -> "the price", never "CrowdStrike" -> "crowdStrike".
    if label.split(" ", 1)[0].lower() in _LOWER_LEADS:
        label = label[:1].lower() + label[1:]
    return label


def _noun_phrase(label: str, query: str) -> str | None:
    """The part as a noun phrase ("do you have one in Perth" -> "an office in
    Perth"); ``None`` when it is not one ("suggest I talk to a specialist",
    "thanks, bye - oh")."""
    words = _words(label)
    if any(w in _ASK_FIRST_PERSON for w in words) or (
        sum(1 for w in words if w in _ASK_FILLER) * 2 >= len(words)
    ):
        return None
    label = _ASK_FRAME_RE.sub("", label).strip(" ,;-")
    head = _ASK_PRONOUN_RE.match(label)
    if head is not None:
        # "one in Perth": the plural noun before it in the question names it.
        before = query[: max(0, query.lower().find(label.lower()))]
        nouns = [
            w for w in _words(before) if len(w) > _MIN_SUBJECT_CHARS and w.endswith("s")
        ]
        if not nouns:
            return None
        noun = nouns[-1][:-1]
        article = "an" if noun[:1] in "aeiou" else "a"
        label = f"{article} {noun}{label[head.end() :]}"
    return _question_phrase(label)


def _question_phrase(label: str) -> str | None:
    """A question-worded label as a noun phrase in our voice ("your CEO" ->
    "our CEO", "the weather like today" -> "the weather today"); ``None``
    when it is still a raw question ("how does ransomware encrypt files")."""
    for pattern, repl in _ASK_REWRITES:
        if pattern.match(label):
            label = pattern.sub(repl, label, count=1)
            break
    label = _ASK_YOUR_RE.sub(
        lambda m: f"{'O' if m.group(1) == 'Y' else 'o'}ur{m.group(2)}", label
    ).strip(" ,;-")
    if not label or _RAW_QUESTION_RE.match(label):
        return None
    return label


def _unanswered(draft: Draft, kept: list[Claim], query: str) -> list[int]:
    """Indexes of the parts no kept claim answers. A part restating the whole
    question is answered by any kept claim: "What types of cookies ...?"
    answered with every cookie type never ends "I don't have details on what
    types of cookies ..."."""
    answered = {c.part for c in kept}
    whole = _norm(query)
    return [
        i
        for i, p in enumerate(draft.parts)
        if i not in answered
        and not (answered and _norm(p.ask) == whole)
        and not (answered and is_reply_language(p.ask))
    ]


def uncovered_parts(draft: Draft, kept: list[Claim], query: str) -> list[str] | None:
    """Names of the parts left without a verified claim (``None``: unnamed)."""
    missing = [draft.parts[i] for i in _unanswered(draft, kept, query)]
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
    labels = [
        _ask_label(draft.parts[i].ask, query) for i in _unanswered(draft, kept, query)
    ]
    named = [label for label in labels if label]
    gaps = [g for g in (_gap_label(t, query) for t in gap_parts) if g]
    if gaps and (None in labels or not named):
        for gap in gaps:
            # "Google Workspace" is already in "Google Workspace support".
            words = set(_norm(gap).split())
            if not any(
                words <= (seen := set(_norm(n).split())) or seen <= words for n in named
            ):
                named.append(gap)
        return named
    return None if None in labels else named


def _unbacked_asks(
    asks: Sequence[str],
    kept: list[Claim],
    chunks: Sequence[_Chunk],
    query: str,
    named: Sequence[str],
) -> list[str]:
    """Asks naming something ("AWS") that no kept claim's chunk mentions, in
    the visitor's words: "Zero Trust works across cloud environments" does
    not answer "do you support AWS?". Already ``named`` gaps are skipped."""
    brand = _brand_words()
    backing = [chunks[c.source - 1] for c in kept if 1 <= c.source <= len(chunks)]
    taken = set(_norm(" ".join(named)).split())
    # A gate sub-question may be a bare name ("Huntress"): its capital is a
    # name when the question has it mid-sentence ("Is Huntress better ...").
    mid_names = {_norm(n) for n, initial in _names(query) if not initial}
    out: list[str] = []
    for ask in dict.fromkeys(" ".join(a.split()) for a in asks):
        names = [
            n
            for n, initial in _names(ask)
            if (not initial or _norm(n) in mid_names) and _norm(n) not in brand
        ]
        unbacked = [n for n in names if not any(c.has_word(n) for c in backing)]
        if not unbacked or any(_norm(n) in taken for n in unbacked):
            continue
        label = _gap_label(ask, query)
        if label:
            out.append(label)
            taken |= set(_norm(label).split())
    return out


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


def _cut_lead(
    run: list[Claim], shared: int, kind: str, min_rest: int = _MIN_SHARED_WORDS
) -> list[Claim]:
    """``run`` as ``kind`` claims without their first ``shared`` words
    (unchanged when a claim would keep fewer than ``min_rest`` words).
    Subtractive only: the lead sentence names the subject once."""
    split = [c.text.split() for c in run]
    if any(len(w) - shared < min_rest for w in split):
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
    return _cut_lead(run, shared, "item", 1) if shared >= _MIN_SHARED_WORDS else run


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


def _as_kind(claim: Claim, kind: str, text: str | None = None) -> Claim:
    return Claim(
        claim.text if text is None else text,
        claim.source,
        claim.quote,
        claim.part,
        kind,
    )


def _follows(chunks: Sequence[_Chunk], label: Claim, claim: Claim) -> bool:
    """``claim``'s quote starts within two units after ``label``'s quote."""
    if not 1 <= label.source <= len(chunks):
        return False
    chunk = chunks[label.source - 1]
    ends = [last for _, last in chunk.quote_spans(label.quote)]
    starts = [first for first, _ in chunk.quote_spans(claim.quote)]
    return any(0 < start - end <= _LOCAL_WINDOW for end in ends for start in starts)


def _describes(
    label: Claim, claim: Claim, follows: Callable[[Claim, Claim], bool] | None
) -> bool:
    """``claim`` is the description of the list label ``label`` (never the
    next label of the list: "Data" + "Visibility & Analytics ...")."""
    return (
        claim.kind == "sentence"
        and label.kind == "item"
        and _is_label(label.text)
        and not _is_label(" ".join(claim.quote.split()))
        and label.source == claim.source
        and (follows is None or follows(label, claim))
    )


def _merge_labels(
    kept: list[Claim], follows: Callable[[Claim, Claim], bool] | None = None
) -> list[Claim]:
    """ "- Identity (Entra)" + "Sign-in security ..." from the same source: one
    item, label then description (both verified). ``follows``: the sentence's
    quote comes right after the label's on the page (when known)."""
    out: list[Claim] = []
    for claim in kept:
        last = out[-1] if out else None
        if last is not None and _describes(last, claim, follows):
            out[-1] = _as_kind(last, "item", f"{last.text}: {_sentence(claim.text)}")
            continue
        out.append(claim)
    return out


def _split_steps(kept: list[Claim]) -> list[Claim]:
    """ "Step 1, Onboard 2. Step 2, Assess 3. ..." or "A · B · C" (a list
    flattened into one claim): one item per entry, without "Step n," labels."""
    out: list[Claim] = []
    for claim in kept:
        splitter = (
            _DOT_LIST_RE if _DOT_LIST_RE.search(claim.text) else _INLINE_NUMBER_RE
        )
        pieces = [p.strip(" ,;") for p in splitter.split(claim.text)]
        pieces = [_STEP_PREFIX_RE.sub("", p).strip(" ,;.") for p in pieces]
        if len(pieces) < _MIN_REPEATED_RUN or not all(pieces):
            out.append(claim)
            continue
        out.extend(_as_kind(claim, "item", p[:1].upper() + p[1:]) for p in pieces)
    return out


def _shared_tail(claims: list[Claim]) -> int:
    """Words every claim of ``claims`` ends with (case-insensitive)."""
    reversed_words = (
        reversed(c.text.lower().rstrip(_TERMINAL).split()) for c in claims
    )
    shared = 0
    for words in zip(*reversed_words, strict=False):
        if len(set(words)) != 1:
            break
        shared += 1
    return shared


def _bulletise_tail(kept: list[Claim]) -> list[Claim]:
    """3+ sentences (or bullets) in a row ending the same way ("X is one of
    Deploy IQ's Responsible AI principles."): the first as a sentence, then
    the others' heads as bullets."""
    out: list[Claim] = []
    index = 0
    while index < len(kept):
        end = index + 1
        while (
            end < len(kept)
            and kept[end].kind == kept[index].kind
            and kept[end].part == kept[index].part
            and _shared_tail(kept[index : end + 1]) >= _MIN_SHARED_TAIL
        ):
            end += 1
        run = kept[index:end]
        shared = _shared_tail(run) if len(run) >= _MIN_REPEATED_RUN else 0
        heads = (
            [c.text.rstrip(_TERMINAL).split()[:-shared] for c in run] if shared else []
        )
        # A head cut before its complement ("Application allowlisting is")
        # keeps the sentences.
        if (
            shared
            and all(heads)
            and not any(_DANGLING_TAIL_RE.search(" ".join(h)) for h in heads)
        ):
            out.append(_as_kind(run[0], "sentence"))
            out.extend(
                _as_kind(c, "item", " ".join(h).rstrip(" ,;"))
                for c, h in zip(run[1:], heads[1:], strict=True)
            )
        else:
            out.extend(run)
        index = end
    return out


def _group_parts(kept: list[Claim]) -> list[Claim]:
    """Each part once: its sentences up to the list's lead, every item of the
    part as one list, then its other sentences (restructure only)."""
    order: list[int] = []
    by_part: dict[int, list[Claim]] = {}
    for claim in kept:
        if claim.part not in by_part:
            order.append(claim.part)
            by_part[claim.part] = []
        by_part[claim.part].append(claim)
    out: list[Claim] = []
    for part in order:
        claims = by_part[part]
        first_item = next((i for i, c in enumerate(claims) if c.kind == "item"), None)
        if first_item is None:
            out.extend(claims)
            continue
        sentences = [c for c in claims if c.kind != "item"]
        # The lead: the sentence right before the first item, else the
        # part's first sentence (moved before the list).
        before = [c for c in claims[:first_item] if c.kind != "item"]
        lead = len(before) if before else min(1, len(sentences))
        out.extend(sentences[:lead])
        out.extend(c for c in claims if c.kind == "item")
        out.extend(sentences[lead:])
    return out


def _fact_keys(claim: Claim) -> set[str]:
    """Content words, numbers and emails: what a claim states (an item's
    every word: "ISO 27001" and "ISO 42001" differ in one short token)."""
    keys = {m.group(0).lower() for m in _EMAIL_RE.finditer(claim.text)}
    item = claim.kind == "item"
    return keys | {
        w
        for w in _words(_EMAIL_RE.sub(" ", claim.text))
        if (item and w not in _ENGLISH_WORDS) or len(w) >= _STEM_CHARS or w.isdigit()
    }


def _restates(claim: Claim, keys: set[str], seen: set[str]) -> bool:
    """Nothing (an item) or under a third (a sentence) of it is new."""
    if not keys:
        return False
    new = len(keys - seen)
    return new == 0 if claim.kind == "item" else new < len(keys) * _MIN_NEW_SHARE


def _is_fragment(claim: Claim) -> bool:
    """A bare page label as a sentence ("Call.", "Sydney Office.", a
    verbless unpunctuated "30 minutes")."""
    words = claim.text.rstrip(_TERMINAL).split()
    if claim.kind != "sentence" or _CONTACT_RE.search(claim.text):
        return False
    if len(words) <= _FRAGMENT_MAX_WORDS and all(
        w[:1].isupper() or not w[:1].isalpha() for w in words
    ):
        return True
    return (
        _is_label(claim.text)
        and len(words) <= _HEADLINE_MAX_WORDS
        and not _has_finite_verb(claim.text)
    )


# A page heading / badge / eyebrow written as a sentence ("Your security
# maturity path.", "30 minutes.", "What you get."): short, no finite verb.
_HEADLINE_MAX_WORDS = 6
_SHARD_MAX_WORDS = 3
_WH_HEADLINE_MAX_WORDS = 4
_MIN_TITLE_WORDS = 3
_MIN_VERB_S_CHARS = 4
_FINITE_WORDS = frozenset(
    {
        "is", "are", "was", "were", "am", "be", "been", "has", "have", "had",
        "do", "does", "did", "can", "could", "will", "would", "should", "may",
        "might", "must", "shall", "isn't", "aren't", "doesn't", "don't",
        "won't", "can't", "get", "gets", "got", "need", "help", "make", "take",
        "give", "keep", "use", "provide", "offer", "include", "cover", "run",
        "work", "stay", "go", "come", "let", "ensure", "deliver", "support",
    }
)  # fmt: skip
_SUBJECT_PRONOUNS = frozenset({"i", "we", "you", "they", "he", "she", "it"})
_CTA_LEADS = frozenset(
    {"see", "explore", "discover", "learn", "view", "browse", "find", "read",
     "check", "compare", "meet"}
)  # fmt: skip
_WH_LEADS = frozenset({"what", "how", "why", "where", "who", "when", "which"})
_VALUE_CUE_RE = re.compile(
    r"\b(?:how\s{1,3}(?:long|much|many|fast|quick(?:ly)?|soon|often)|duration|"
    r"timeline|minutes?|hours?|days?|weeks?|months?|cost|price|when)\b",
    re.IGNORECASE,
)
_HEADING_CUE_RE = re.compile(
    r"\b(?:tagline|slogan|motto|headline|heading|title|eyebrow|badge|label|"
    r"called|named|who|whom)\b",
    re.IGNORECASE,
)
_QUERY_FILLER = frozenset({"get", "got", "only", "not", "page", "offer", "offered"})
_LINE_LABEL_RE = re.compile(r"^[A-Za-z][\w ]{0,20}:\s{1,3}")  # "Eyebrow: ..."
_BOLD_RE = re.compile(r"\*\*([^*\n]{1,160})\*\*")
_SHARD_SPLIT_RE = re.compile(r"(?<=[.!?])\s{1,4}")
_EDGE_PUNCT = ".,;:!?()[]\"'“”"


def _verb_s(token: str) -> bool:
    """A third-person verb form ("offers"), not "access" / "status" / "IQ's"."""
    return (
        len(token) >= _MIN_VERB_S_CHARS
        and token.isalpha()
        and token.endswith("s")
        and not token.endswith(("ss", "us", "is", "ics"))
    )


def _has_finite_verb(text: str) -> bool:
    """Heuristic: an auxiliary / common verb, a pronoun subject, or an "-s"
    verb that is not the phrase's last word ("Technical controls, not ...")."""
    raw = text.split()
    tokens = [w.strip(_EDGE_PUNCT).lower() for w in raw]
    for index, token in enumerate(tokens):
        if token in _FINITE_WORDS:
            return True
        if token in _SUBJECT_PRONOUNS and index + 1 < len(tokens):
            return True
        closes = index + 1 == len(tokens) or raw[index][-1:] in ",;:"
        if _verb_s(token) and (index == 0 or not closes):
            return True
    return False


def _standalone(quote: str, chunk: _Chunk) -> bool:
    """The quote is a whole line, bullet, heading or bold span of the chunk."""
    target = _norm(quote)
    if not target:
        return False
    for line in chunk.source.content.split("\n"):
        plain = _BULLET_RE.sub("", line.strip()).lstrip("# ")
        segments = [m.group(1) for m in _BOLD_RE.finditer(plain)]
        plain = _EMPHASIS_RE.sub("", plain).strip()
        segments += [plain, _LINE_LABEL_RE.sub("", plain)]
        if any(_norm(s) == target for s in segments):
            return True
    return False


def _asks_for(text: str, query: str) -> bool:
    """The question asks for exactly this value ("how long?" + "30 minutes")
    or names one of its words."""
    if _HEADING_CUE_RE.search(query):
        return True
    if _DIGIT_RE.search(text) and _VALUE_CUE_RE.search(query):
        return True
    stop = _ENGLISH_WORDS | _NOT_NAMES | _QUERY_FILLER | _brand_words()
    content = {_singular(w) for w in _words(text) if w not in stop and not w.isdigit()}
    return bool(content & {_singular(w) for w in _words(query) if w not in stop})


def _title_case(words: list[str]) -> bool:
    """A page title: 3+ words, every word with letters capitalised."""
    alpha = [w for w in words if any(ch.isalpha() for ch in w)]
    return len(alpha) >= _MIN_TITLE_WORDS and all(w[:1].isupper() for w in alpha)


def _fragment_reason(claim: Claim, chunks: Sequence[_Chunk], query: str) -> str | None:
    """ "heading_fragment" for a page heading / badge / eyebrow written as a
    sentence (list items are fine; the asked-for value is kept). Only a claim
    citing one of ``chunks`` (1-based ``source``) is judged."""
    if not 1 <= claim.source <= len(chunks):
        return None
    chunk = chunks[claim.source - 1]
    text = claim.text.strip()
    words = text.rstrip(_TERMINAL).split()
    if (
        claim.kind != "sentence"
        or not words
        or len(words) > _HEADLINE_MAX_WORDS
        or _CONTACT_RE.search(text)
        or text.endswith("?")
    ):
        return None
    shards = [s for s in _SHARD_SPLIT_RE.split(text) if s]
    if len(shards) > 1 and all(
        len(s.split()) <= _SHARD_MAX_WORDS and not _has_finite_verb(s) for s in shards
    ):
        return "heading_fragment"  # "Human-approved. AI AUTOMATION."
    # A bare label ("Identity") may be one row of a list (rendering decides).
    if _is_label(text) or not _standalone(claim.quote, chunk) or _asks_for(text, query):
        return None
    lead = words[0].strip(_EDGE_PUNCT).lower()
    headline = (
        lead in _CTA_LEADS
        or (lead in _WH_LEADS and len(words) <= _WH_HEADLINE_MAX_WORDS)
        or _title_case(words)  # "Stop Threats Before They Run."
    )
    # "Clear alignment to intent and obligations" under "What Deploy IQ
    # Platform Enables", asked "What does the platform enable?": the asked
    # section's own rows are the answer, not stray headings.
    row = not headline and (_has_finite_verb(text) or _section_asked(chunk, query))
    return None if row else "heading_fragment"


def _section_asked(chunk: _Chunk, query: str) -> bool:
    """The chunk's section heading names nothing the question does not."""
    heading = str((chunk.source.metadata or {}).get("section_heading") or "")
    brand = _brand_words()

    def stems(text: str) -> set[str]:
        return {
            w[:_STEM_CHARS]
            for w in _words(text)
            if len(w) >= _MIN_SUBJECT_CHARS
            and w not in _ENGLISH_WORDS | _NOT_NAMES
            and w not in brand
        }

    wanted = stems(heading)
    return bool(wanted) and wanted <= stems(query)


# Near-duplicates: one fact from two sources ("info@... [1] Contact: info@...").
_DUPLICATE_SHARE = 0.8
_CONTACT_EXTRA_STEMS = 1
_SUBJECT_WORDS = 2
# A claim / bullet cut before its complement ("Application allowlisting is").
_DANGLING_TAIL_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|of|the|an|and|or|as|including|includes?|"
    r"such\s{1,2}as|with|by|plus)\s{0,2}[.,;:]?\s{0,2}$",
    re.IGNORECASE,
)


_SUFFIXES = ("ing", "ed", "es", "s")


def _stem(word: str) -> str:
    """ "used" / "uses" -> "us", "governed" -> "gover" (light, for overlap)."""
    for suffix in _SUFFIXES:
        if len(word) >= len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)][:_STEM_CHARS]
    return word[:_STEM_CHARS]


def _stems(text: str) -> set[str]:
    stop = _ENGLISH_WORDS | _NOT_NAMES | _brand_words()
    return {_stem(w) for w in _words(text) if w not in stop}


def _duplicates(claim: Claim, shown: Sequence[Claim]) -> bool:
    """A sentence restating a shown sentence: ≥80% shared stems, all its stems
    already said, the same email / phone, or the same values and subject."""
    if claim.kind == "item":
        return False
    stems = _stems(claim.text)
    contact = _contact_values(claim.text)
    values = {w for w in _words(claim.text) if w.isdigit()}
    subject = _words(claim.text)[:_SUBJECT_WORDS]
    for prior in shown:
        if prior.kind == "item":
            continue
        other = _stems(prior.text)
        if stems and (
            stems <= other
            or len(stems & other) >= _DUPLICATE_SHARE * len(stems | other)
        ):
            return True
        if contact and contact <= _contact_values(prior.text):
            value_stems = set().union(*(_stems(v) for v in contact))
            if len(stems - other - value_stems) <= _CONTACT_EXTRA_STEMS:
                return True
        if (
            values
            and values <= {w for w in _words(prior.text) if w.isdigit()}
            and subject == _words(prior.text)[:_SUBJECT_WORDS]
        ):
            return True
    return False


def _cap(shown: list[Claim], max_words: int) -> list[Claim]:
    """Hard caps on the reply: ``max_words`` words and ``_MAX_LIST_ITEMS``
    bullets per part; the latest claim of a part with others goes first."""
    out = list(shown)

    def items_of(part: int) -> int:
        return sum(c.kind == "item" and c.part == part for c in out)

    while True:
        crowded = {c.part for c in out if items_of(c.part) > _MAX_LIST_ITEMS}
        if not crowded and sum(len(c.text.split()) for c in out) <= max_words:
            return out
        spare = [
            i
            for i, c in enumerate(out)
            if sum(o.part == c.part for o in out) > 1
            and (not crowded or (c.kind == "item" and c.part in crowded))
        ]
        if not spare:
            return out
        del out[spare[-1]]


def _tidy_ends(shown: list[Claim]) -> list[Claim]:
    """A lead ending in ":" with no list after it ends in "."; a claim cut
    before its complement ("... are", "... every stage:" alone) is dropped
    (unless it is the reply's only claim)."""
    out: list[Claim] = []
    for index, claim in enumerate(shown):
        text = claim.text.rstrip()
        listed = index + 1 < len(shown) and shown[index + 1].kind == "item"
        if text.endswith(":") and listed and claim.kind != "item":
            out.append(claim)  # a list's lead
            continue
        text = text.rstrip(" :")
        if text and _DANGLING_TAIL_RE.search(text) is None:
            out.append(
                claim if text == claim.text else _as_kind(claim, claim.kind, text)
            )
        elif len(shown) == 1:
            out.append(claim)
    return out


def _settle_lone_items(shown: list[Claim]) -> list[Claim]:
    """A list of one item is a sentence; a lone label next to other claims of
    its part is dropped."""
    out: list[Claim] = []
    for index, claim in enumerate(shown):
        prev_item = index > 0 and shown[index - 1].kind == "item"
        next_item = index + 1 < len(shown) and shown[index + 1].kind == "item"
        if claim.kind != "item" or prev_item or next_item or len(shown) == 1:
            out.append(claim)
            continue
        others = any(c.part == claim.part for c in shown if c is not claim)
        if others and _is_label(claim.text):
            continue
        out.append(_as_kind(claim, "sentence"))
    return out


def _strip_item_leads(shown: list[Claim]) -> list[Claim]:
    """Every list loses the lead all its bullets repeat; a list with no
    sentence of its part before it keeps that lead once, as its lead
    sentence ("The controls include:" + the bullets)."""
    out: list[Claim] = []
    run: list[Claim] = []
    for claim in [*shown, None]:
        if claim is not None and claim.kind == "item":
            run.append(claim)
            continue
        for index, group in enumerate(_lead_groups(run)):
            _emit_group(out, group, index)
        run = []
        if claim is not None:
            out.append(claim)
    return out


_PRONOUN_LEADS = frozenset({"it", "they", "this", "these", "its", "their"})


def _lead_groups(run: list[Claim]) -> list[list[Claim]]:
    """``run`` split into consecutive bullets repeating one lead ("The
    Strengthen solution includes ..." x3, then "It adds ..." x5)."""
    groups: list[list[Claim]] = []
    for claim in run:
        last = groups[-1] if groups else None
        if last is not None and _shared_lead([*last, claim]) >= _MIN_SHARED_WORDS:
            last.append(claim)
        else:
            groups.append([claim])
    return groups


def _emit_group(out: list[Claim], group: list[Claim], index: int) -> None:
    """One lead group of a list, its repeated lead cut. The list's first group
    without a sentence of its part before it keeps the lead once as a lead
    sentence; a later group's pronoun lead ("It adds") continues the list; a
    later group's own subject ("The Frontier package includes") gets a lead
    sentence, or keeps its lead when it is too short for one."""
    cut = _strip_shared_lead(group)
    shared = _shared_lead(group) if cut is not group else 0
    lead = " ".join(group[0].text.split()[:shared]).rstrip(" ,;:")
    pronoun = bool(shared) and lead.split()[0].lower() in _PRONOUN_LEADS
    led = (bool(out) and out[-1].part == group[0].part) if index == 0 else pronoun
    # "The control area is:" reads as one item, not a list's lead.
    if shared >= _MIN_LEAD_WORDS and not led and not _SINGULAR_LEAD_RE.search(lead):
        out.append(_as_kind(group[0], "sentence", f"{lead}:"))
    elif index and not led:
        cut = group
    out.extend(cut)


def _labels_as_items(kept: list[Claim]) -> list[Claim]:
    """3+ bare labels in a row of one part and source ("Identity",
    "Devices", ...) written as sentences: a list, not "Identity. Devices."
    (the verified texts unchanged, only their kind)."""
    out: list[Claim] = []
    index = 0
    while index < len(kept):
        end = index
        while (
            end < len(kept)
            and kept[end].kind == "sentence"
            and kept[end].part == kept[index].part
            and kept[end].source == kept[index].source
            and _is_label(kept[end].text)
            and not _CONTACT_RE.search(kept[end].text)
        ):
            end += 1
        if end - index >= _MIN_REPEATED_RUN:
            out.extend(_as_kind(c, "item") for c in kept[index:end])
            index = end
        else:
            out.append(kept[index])
            index += 1
    return out


# A run-on series ("The service includes A, B, C, ... and J."): a list.
_MIN_SERIES_ITEMS = 5
_SERIES_LEAD_RE = re.compile(
    r"^(.{3,120}?\b(?:includes?|covers?|offers?|provides?|delivers?|features?|"
    r"comprises?|that|such\s{1,2}as|including))\s{1,3}(\S.*)$",
    re.IGNORECASE,
)
_SERIES_SPLIT_RE = re.compile(r",\s{1,3}(?:and\s{1,3}|or\s{1,3})?|;\s{1,3}")
# 5+ sentences in a row of one part and source: a lead and bullets.
_MIN_PROSE_RUN = 5


def _is_series(text: str) -> bool:
    """ "A, B, C and D": an "and" / "or" joins only the last entry. "a
    governed, continuous system that connects X, Y, and Z into structured,
    traceable action" is prose with commas, not a list."""
    joins = [
        bool(re.search(r"\b(?:and|or)\b", m.group(0)))
        for m in _SERIES_SPLIT_RE.finditer(text)
    ]
    return not any(joins[:-1])


def _split_series(kept: list[Claim]) -> list[Claim]:
    """A sentence listing 5+ comma-separated things after a lead verb: the
    lead ("The service includes:") then one item per entry (same words, a
    one-word entry joins the next: "scoped, phased implementation")."""
    out: list[Claim] = []
    for claim in kept:
        found = _SERIES_LEAD_RE.match(claim.text.rstrip(_TERMINAL))
        if claim.kind != "sentence" or found is None or not _is_series(found.group(2)):
            out.append(claim)
            continue
        entries: list[str] = []
        for raw in _SERIES_SPLIT_RE.split(found.group(2)):
            piece = raw.strip()
            if entries and len(entries[-1].split()) == 1:
                entries[-1] = f"{entries[-1]}, {piece}"
            elif piece:
                entries.append(piece)
        if len(entries) < _MIN_SERIES_ITEMS:
            out.append(claim)
            continue
        out.append(_as_kind(claim, "sentence", f"{found.group(1)}:"))
        out.extend(_as_kind(claim, "item", e[:1].upper() + e[1:]) for e in entries)
    return out


def _prose_as_list(kept: list[Claim]) -> list[Claim]:
    """5+ sentences in a row of one part and source (a page's list told as
    prose): the first stays the lead, the others become bullets."""
    out: list[Claim] = []
    index = 0
    while index < len(kept):
        end = index
        while (
            end < len(kept)
            and kept[end].kind == "sentence"
            and kept[end].part == kept[index].part
            and kept[end].source == kept[index].source
        ):
            end += 1
        if end - index >= _MIN_PROSE_RUN:
            out.append(kept[index])
            out.extend(_as_kind(c, "item") for c in kept[index + 1 : end])
            index = end
        else:
            out.append(kept[index])
            index += 1
    return out


_NAME_JOINERS = frozenset({"for", "in", "of", "and", "the", "&", "to", "with"})
_MAX_NAME_WORDS = 10


def _is_bare_name(claim: Claim) -> bool:
    """A verbless Title Case name as a sentence ("Zero Trust Services.")."""
    text = claim.text.rstrip()
    words = text.rstrip(_TERMINAL).split()
    return (
        claim.kind == "sentence"
        and not text.endswith(":")
        and 0 < len(words) <= _MAX_NAME_WORDS
        and not _has_finite_verb(text)
        and all(
            w[:1].isupper() or not w[:1].isalpha() or w.lower() in _NAME_JOINERS
            for w in words
        )
    )


def _names_into_list(claims: list[Claim]) -> list[Claim]:
    """Bare names written as sentences right before a part's first list item
    are that list's first entries ("Zero Trust Services." then "- Essential
    Eight..."): items, not label fragments to drop."""
    out = list(claims)
    index = 0
    while index < len(out):
        end = index
        while (
            end < len(out)
            and out[end].part == out[index].part
            and _is_bare_name(out[end])
        ):
            end += 1
        listed_before = any(
            c.kind == "item" and c.part == out[index].part for c in out[:index]
        )
        if (
            end > index
            and not listed_before  # "Call." amid the bullets stays a stray label
            and end < len(out)
            and out[end].kind == "item"
            and out[end].part == out[index].part
        ):
            for k in range(index, end):
                out[k] = Claim(
                    out[k].text.rstrip(" ."),
                    out[k].source,
                    out[k].quote,
                    out[k].part,
                    "item",
                )
        index = max(end, index + 1)
    return out


def _fit_length(  # pylint: disable=too-many-locals
    kept: list[Claim],
    max_words: int,
    follows: Callable[[Claim, Claim], bool] | None = None,
) -> list[Claim]:
    """The verified claims that ship, within the length rules (removes and
    restructures only; every shipped word is a verified claim's).

    One list per part, after its lead sentence; at most ``_MAX_LIST_ITEMS``
    bullets per part (shared bullet lead removed); restated facts and bare
    label fragments dropped; about ``max_words`` words. Each answered part
    keeps its first claim, so a long answer never turns a covered part into
    a gap: the words of the later parts' first claims are reserved, so the
    budget trims within parts instead of crowding out a later one.
    """
    steps = _split_series(_split_steps(_merge_labels(_labels_as_items(kept), follows)))
    claims = _names_into_list(
        _group_parts(_prose_as_list(_bulletise_tail(_bulletise(steps))))
    )
    solid = {c.part for c in claims if not _is_fragment(c)}
    firsts: dict[int, int] = {}
    for claim in claims:
        if not (_is_fragment(claim) and claim.part in solid):
            firsts.setdefault(claim.part, len(claim.text.split()))
    shown: list[Claim] = []
    words = 0
    items: dict[int, int] = {}
    parts_shown: set[int] = set()
    seen: set[str] = set()
    for claim in claims:
        size = len(claim.text.split())
        first = claim.part not in parts_shown
        keys = _fact_keys(claim)
        if _is_fragment(claim) and claim.part in solid:
            continue
        reserved = sum(
            n for p, n in firsts.items() if p not in parts_shown and p != claim.part
        )
        over = words + size + reserved > max_words or (
            claim.kind == "item" and items.get(claim.part, 0) >= _MAX_LIST_ITEMS
        )
        if not first and (over or _restates(claim, keys, seen)):
            continue
        if _duplicates(claim, shown):
            continue  # said already (another part's claim / another source)
        seen |= keys
        shown.append(claim)
        words += size
        items[claim.part] = items.get(claim.part, 0) + (claim.kind == "item")
        parts_shown.add(claim.part)
    listed = _tidy_ends(_strip_item_leads(_settle_lone_items(shown)))
    return _tidy_ends(_cap(listed, max_words))


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

    last_part: int | None = None
    for claim in kept:
        n = marker(claim)
        part, last_part = last_part, claim.part
        if claim.kind == "item":
            flush_paragraph()
            if part != claim.part:
                flush_items()  # each part's list on its own
            prefix = f"{len(items) + 1}." if numbered else "-"
            items.append(f"{prefix} {claim.text.rstrip()} [{n}]")
            continue
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


_MAX_LIST_NAME_WORDS = 10


def list_names(source: SourceChunk) -> list[str]:
    """The entry names of a card/list section, verbatim and in page order:
    its bullet lines and its card headings (a short line with no end
    punctuation followed by its description)."""
    lines = [line.strip() for line in source.content.split("\n")]
    names: list[str] = []
    for index, line in enumerate(lines):
        if not line or _BREADCRUMB_SEP in line or line.startswith("#"):
            continue
        bullet = _BULLET_RE.sub("", line).strip()
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        card = (
            bullet == line
            and nxt
            and not _BULLET_RE.match(nxt)
            and len(nxt.split()) > len(line.split())
        )
        if (bullet != line or card) and (
            0 < len(bullet.split()) <= _MAX_LIST_NAME_WORDS
            and bullet[-1:] not in _TERMINAL
        ):
            names.append(_EMPHASIS_RE.sub("", bullet).strip())
    return names


def missing_list_names(text: str, names: Sequence[str]) -> list[str]:
    """The names a shipped reply leaves out."""
    shipped = _norm(text)
    return [name for name in names if _norm(name) not in shipped]


def list_draft(names: Sequence[str], source: int, ask: str) -> Draft:
    """Every entry of a list section as items, copied verbatim (verified as
    any claim): the deterministic backstop for "what services do you have?"
    when the model's list left entries out."""
    return Draft(
        action="answer",
        parts=[Part(ask, True)],
        claims=[Claim(name, source, name, 0, "item") for name in names],
    )


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
    named_asks: Sequence[str] = (),
    sales_contact: SalesContact | None = None,
) -> GroundedAnswer:
    """Verify ``draft`` against ``sources`` and render the shipped reply.

    No verified claim -> ``text`` is the fixed no-answer line and
    ``answered`` is False. Some parts without a verified claim -> the fixed
    partial line naming them is appended. ``numbered``: items as steps.
    ``gate_uncovered``: the retrieval gate found an ask with no evidence, so
    the partial line is added even if the model claimed every part.
    ``gap_parts``: the gate's uncovered sub-questions, naming a gap the model
    left unnamed. ``named_asks``: further asks (a re-ask's) held to the same
    rule as ``gap_parts``: one naming something ("AWS") that no kept claim's
    chunk mentions is a gap even if the model claimed it. ``sales_contact``
    (buying intent): its fixed line is appended with the marker of the chunk
    it was read from.
    """
    chunks = [_Chunk(s) for s in sources]
    texts = [c.text for c in draft.claims] or [query]
    english = is_english(texts) or _kb_worded(texts, chunks)
    kept, dropped = _verify(draft, chunks, english, query)
    if not kept:
        return GroundedAnswer(
            NO_ANSWER_LINE, dropped=dropped, mode=draft.mode, parts=draft.parts
        )
    missing = uncovered_parts(draft, kept, query) if draft.parts else []
    if gate_uncovered and missing == []:
        missing = None
    if missing is None and gap_parts:
        missing = _named_gaps(draft, kept, query, gap_parts)
    if missing is not None:
        missing = [
            *missing,
            *_unbacked_asks([*gap_parts, *named_asks], kept, chunks, query, missing),
        ]
    # A price part gets the fixed pricing line, not "I don't have details on".
    priced = [m for m in missing or [] if _PRICE_LABEL_RE.search(m)]
    gaps = [m for m in missing or [] if m not in priced]
    fixed: list[str] = []
    if missing is None or gaps:
        fixed.append(partial_line(gaps))
    if priced:
        fixed.append(PRICING_LINE)
    shown_from = kept
    contact = None
    if sales_contact is not None:
        # The fixed sales line replaces any model sentence restating it.
        values = _contact_values(f"{sales_contact.phone} {sales_contact.email}")
        shown_from = [c for c in kept if not _contact_values(c.text) & values] or kept
        if shown_from is not kept or not any(
            sales_contact.phone in c.text for c in kept
        ):
            contact = Claim(
                sales_contact.line, sales_contact.source, sales_contact.phone
            )
            fixed.append(sales_contact.line)
            if _PRICE_LABEL_RE.search(query) and not priced:
                fixed.append(PRICING_LINE)  # the orchestrator appends it
    # The fixed lines count towards the reply's length cap.
    budget = get_settings().answer_max_words - sum(len(f.split()) for f in fixed)
    shown = _fit_length(shown_from, max(budget, 1), lambda a, b: _follows(chunks, a, b))
    shown = shape_reply(shown, shown_from, sources)
    text, citations, selected = _render(
        [*shown, contact] if contact else shown, sources, numbered
    )
    if missing is None or gaps:
        text = f"{text}\n\n{partial_line(gaps)}"
    if priced:
        text = f"{text}\n\n{PRICING_LINE}"
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
