# pylint: disable=too-many-lines
"""Splits a message into its asks, retrieves per ask and gates relevance.

Multi-part messages ("What is E8? And who is your CEO?", numbered lists,
"compare A, B and C", "X, how long does it take and which levels...") are
split into parts (``split_query``); a referential part ("what does it cover?")
carries the subject of the part before it. Each part gets its own embedding,
retrieval pass and chunk budget, and its own covered verdict from the raw
cross-encoder logit of its best chunk (``decide_route``), so one uncovered
part never declines the covered ones and parts are never folded together.
"""

from __future__ import annotations

import difflib
import math
import re
import time
import uuid
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.governance.pii import redact_pii
from app.guardrails.normalize import strip_leading_interjections
from app.guardrails.pre_checks import is_gibberish_token
from app.harness.decision import PartVerdict, Route, RouteDecision
from app.knowledge.links import citation_url
from app.knowledge.settings_service import current_knowledge_settings
from app.retrieval.embeddings import embed_text, embed_texts, embedding_timing_sink
from app.retrieval.reranker import (
    entity_documents,
    entity_mentions,
    looks_english,
    rerank_async,
)
from app.retrieval.vector_store import VectorStore
from app.schemas.query import ChatTurn, SourceChunk

# Query-decomposition heuristics.
_MAX_SHORT_INPUT_CHARS = 2
_SHORT_DOMAIN_TERMS = {"e8", "ai", "ip", "db"}
_MAX_SCAN_CHARS = 2000

logger = get_logger(__name__)

# A self-introduced name ("my name is John Smith", "I'm John from Acme"):
# masked in log lines with the phone numbers and emails redact_pii finds.
_SELF_NAME_RE = re.compile(
    r"\b((?i:my\s{1,3}name\s{1,3}is|my\s{1,3}name's|i'm|i\s{1,3}am|im|"
    r"name\s{0,2}:|this\s{1,3}is))\s{1,3}[A-Z][a-z'-]{1,20}(?:\s{1,3}[A-Z][a-z'-]{1,20})?"
)
_LOG_TEXT_MAX_CHARS = 500


def log_safe_text(text: str, limit: int = _LOG_TEXT_MAX_CHARS) -> str:
    """Visitor text for a log line: personal data redacted (phone numbers,
    emails, ... via redact_pii, keeping PII_ALLOWLIST, and self-introduced
    names), then capped at ``limit`` characters."""
    capped = (text or "")[:_LOG_TEXT_MAX_CHARS]
    redacted = redact_pii(capped, allowlist=get_settings().pii_allowlist)[0]
    return _SELF_NAME_RE.sub(r"\1 [REDACTED_NAME]", redacted)[:limit]


# Referential language that signals a query only makes sense alongside prior
# conversation turns (e.g. "what about ITS pricing?", "does IT support SSO?").
_REFERENTIAL_RE = re.compile(
    r"\b(it|its|this|that|these|those|they|them|their|the same|"
    r"what about|how about|and (?:the|its|their)|the above|the previous one|"
    r"mentioned\s{1,3}(?:above|earlier|previously)|"
    r"the one\s{1,3}(?:above|you mentioned|from earlier)|"
    r"the (?:first|second|third|last) one|the one you mentioned)\b",
    re.IGNORECASE,
)
# A named subject (brand, service or framework) makes a short "Who is Exigo?"
# standalone: it needs no earlier turn to be understood. Literals only.
_NAMED_SUBJECT_RE = re.compile(
    r"\b(exigo|deploy[ _-]{0,3}iq|essential[ _-]{0,3}(?:eight|8)|e8|acsc|iso|cis|"
    r"zero[ _-]{0,3}trust|security[ _-]{0,3}baseline|m365|microsoft[ _-]{0,3}365)\b",
    re.IGNORECASE,
)
# Capitalised word after the first one: a name, not sentence case.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]+\b")
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,6}\b")

_SHORT_QUERY_WORD_COUNT = 3
_SHORT_FOLLOW_UP_TOKENS = {
    "why",
    "how",
    "more",
    "details",
    "pricing",
    "cost",
    "features",
    "elaborate",
    "explain",
    "what",
    "can",
    "does",
    "is",
    "are",
}


def _scan(text: str) -> str:
    """Whitespace-collapsed, length-bounded user text (before any regex)."""
    return " ".join(text.split())[:_MAX_SCAN_CHARS]


@lru_cache(maxsize=1024)
def needs_conversation_context(query: str) -> bool:
    """Heuristic: does this query look like a follow-up on its own?

    Flags queries that are either short interrogatives or contain referential
    language that can't be resolved without the preceding conversation turns.
    Guards against attaching history to gibberish or pure punctuation.
    """

    text = query.strip()

    if not text:
        return False

    if re.match(r"^[\s\W_]+$", text):
        return False

    words = text.split()
    if any(is_gibberish_token(w) for w in words):
        return False

    clean_chars = re.sub(r"[\W_]", "", text).lower()
    if (
        len(clean_chars) <= _MAX_SHORT_INPUT_CHARS
        and clean_chars not in _SHORT_DOMAIN_TERMS
    ):
        return False

    text = _scan(text)
    if _REFERENTIAL_RE.search(text):
        return True

    # "Who is Exigo?": a named subject stands on its own.
    rest = text.split(" ", 1)[1] if " " in text else ""
    named = _NAMED_SUBJECT_RE.search(text) or _PROPER_NOUN_RE.search(rest)

    clean_words = {w.lower().strip("?!.,:;") for w in words}
    return (
        not named
        and len(words) <= _SHORT_QUERY_WORD_COUNT
        and bool(clean_words & _SHORT_FOLLOW_UP_TOKENS)
    )


def _recent(history: list[ChatTurn] | None, role: str) -> str | None:
    return next(
        (turn.content for turn in reversed(history or []) if turn.role == role),
        None,
    )


def contextualize_query(query: str, history: list[ChatTurn] | None) -> str:
    """Fold recent conversation turns into the retrieval query when needed.

    Only applied when the current query looks like a follow-up (see
    needs_conversation_context); otherwise the query is returned unchanged
    so on-topic standalone questions aren't diluted by unrelated history.
    """

    if not history or not needs_conversation_context(query):
        return query
    parts = [p for p in (_recent(history, "user"), _recent(history, "assistant")) if p]
    if not parts:
        return query
    return " ".join(parts) + " " + query


# ---------------------------------------------------------------------------
# Splitting a message into its asks
# ---------------------------------------------------------------------------

_ASK_WORDS = (
    r"(?:what(?:'s|s)?|who(?:'s)?|whom|whose|where|when|why|how|which|is|are|"
    r"do|does|did|can|could|will|would|should|has|have|may|tell|explain|"
    r"describe|list|show|give|define|compare)"
)
_CONNECTOR = r"(?:and|also|plus|so|then|ok|okay|or)"
_ASK_START_RE = re.compile(
    rf"^(?:{_CONNECTOR}\s{{1,3}}){{0,2}}{_ASK_WORDS}\b", re.IGNORECASE
)
_LEAD_CONNECTOR_RE = re.compile(
    rf"^(?:{_CONNECTOR}\b[\s,]{{0,3}}){{1,3}}", re.IGNORECASE
)
# Hard boundaries: newlines, ';', after '?', inline "2)" / "2." markers, and a
# sentence end followed by a capital letter.
_HARD_SPLIT_RE = re.compile(
    r"\n+|;|(?<=\?)|(?:^|\s)\(?\d{1,2}[.)]\s|(?<=[.!])\s{1,3}(?=[A-Z])"
)
_MARKER_RE = re.compile(r"^\s{0,3}(?:\(?\d{1,2}[.)]|[-*•·])\s{1,3}")
# Soft boundaries inside one sentence. "and also" / ", also" / ", plus" always
# split; ", <ask word>" / "and <ask word>" / "also|plus <ask word>" split when
# both sides are long enough to be asks of their own.
_ALWAYS_SPLIT_RE = re.compile(
    r",?\s{1,3}and\s{1,3}also\s{1,3}|,\s{0,3}(?:also|plus)\s{1,3}", re.IGNORECASE
)
_ASK_SPLIT_RE = re.compile(
    rf"(?:,\s{{0,3}}(?:and\s{{1,3}}|or\s{{1,3}})?|\s{{1,3}}(?:and|also|plus)\s{{1,3}})"
    rf"(?={_ASK_WORDS}\b)",
    re.IGNORECASE,
)
_MIN_LEFT_WORDS = 3
_MIN_RIGHT_WORDS = 3
_TOPIC_MAX_WORDS = 8
# "When I fill in the form, what ...": a subordinate clause, not an ask.
_CONTEXT_CLAUSE_RE = re.compile(
    r"^(?:when|where|if|once|after|before|while|since|as)\s{1,3}"
    r"(?:i|we|you|they|my|our|your|the|a|an)\b",
    re.IGNORECASE,
)
_LIST_ITEM_MAX_WORDS = 5
_MIN_LIST_ITEMS = 3
_LIST_SPLIT_RE = re.compile(r"\s{0,3},\s{0,3}(?:and\s{1,3}|or\s{1,3})?", re.IGNORECASE)
_LAST_AND_RE = re.compile(r"\s{1,3}(?:and|or|&)\s{1,3}", re.IGNORECASE)
# The verb/preposition a list of objects hangs off ("explain A, B and C").
_PIVOT_RE = re.compile(
    r"\b(?:explain|describe|about|on|regarding|cover|covers|include|includes|"
    r"offer|offers|support|supports|with|for|is|are|of)\s{1,3}",
    re.IGNORECASE,
)
_COMPARE_RES = (
    re.compile(
        r"\b(?:compare|comparing|comparison\s{1,3}(?:of|between))\s{1,3}(?P<rest>.+)",
        re.IGNORECASE,
    ),
    re.compile(r"\bdifferen\w{0,4}\s{1,3}between\s{1,3}(?P<rest>.+)", re.IGNORECASE),
)
_COMPARE_PAIR_RES = (
    re.compile(
        r"(?P<a>[^,?]{2,80}?)\s{1,3}(?:vs\.?|versus|compared?\s{1,3}(?:to|with)|"
        r"(?:better|worse|different)\s{1,3}(?:than|from))\s{1,3}(?P<b>[^?]{2,80})",
        re.IGNORECASE,
    ),
)
_OPERAND_SPLIT_RE = re.compile(
    r"\s{0,3},\s{0,3}(?:and\s{1,3}|or\s{1,3})?|\s{1,3}(?:and|or|vs\.?|versus|with)\s{1,3}",
    re.IGNORECASE,
)
_OPERAND_LEAD_RE = re.compile(
    rf"^(?:{_ASK_WORDS}\s{{1,3}}){{0,2}}(?:(?:the|a|an|your|my|our)\s{{1,3}})?",
    re.IGNORECASE,
)
_OPERAND_MAX_WORDS = 6
_SELF_OPERAND_RE = re.compile(
    r"^(?:you(?:\s{1,3}guys)?|u|us|we|yourselves|it|this|that|they|them)$",
    re.IGNORECASE,
)
_HAS_WORD_RE = re.compile(r"[A-Za-z0-9]")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'&+-]*")
_PRONOUN_RE = re.compile(
    r"\b(?:it|its|it's|they|them|their|this|that|these|those)\b", re.IGNORECASE
)
_FILLER = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "do",
        "does",
        "did",
        "can",
        "could",
        "will",
        "would",
        "should",
        "you",
        "your",
        "we",
        "our",
        "i",
        "my",
        "me",
        "it",
        "its",
        "they",
        "them",
        "their",
        "this",
        "that",
        "what",
        "how",
        "which",
        "who",
        "when",
        "where",
        "why",
        "much",
        "many",
        "long",
        "take",
        "takes",
        "typically",
        "usually",
        "get",
        "there",
        "any",
        "about",
        "cover",
        "covers",
        "include",
        "includes",
        "mean",
        "means",
        "work",
        "works",
        "offer",
        "have",
        "has",
    ]
)
# An elliptic ask made only of these words ("how long does implementation
# take?", "can I upgrade later?") is about the subject named before it.
_ATTRIBUTE_WORDS = frozenset(
    [
        "implementation",
        "implement",
        "implementing",
        "cost",
        "costs",
        "price",
        "prices",
        "pricing",
        "timeline",
        "timelines",
        "duration",
        "time",
        "levels",
        "level",
        "maturity",
        "outcomes",
        "outcome",
        "benefits",
        "benefit",
        "steps",
        "step",
        "stages",
        "stage",
        "phases",
        "phase",
        "process",
        "scope",
        "features",
        "feature",
        "requirements",
        "requirement",
        "evidence",
        "upgrade",
        "later",
        "expect",
        "included",
        "covered",
        "deliver",
        "delivery",
        "approach",
        "help",
        "support",
        "available",
        "involved",
        "need",
        "needed",
        "start",
        "started",
        "first",
        "next",
    ]
)
# Words that extend an entity mention into its subject ("Zero Trust Starter plan").
_SUBJECT_TAIL = frozenset(
    [
        "starter",
        "professional",
        "plan",
        "plans",
        "package",
        "packages",
        "tier",
        "tiers",
        "assessment",
        "assessments",
        "benchmark",
        "technical",
        "controls",
        "control",
        "services",
        "service",
        "implementation",
        "pillars",
    ]
)
_SUBJECT_TAIL_WORDS = 3


@dataclass(frozen=True, slots=True)
class QueryPart:
    """One ask of the message."""

    text: str  # what the visitor asked (used in a not-covered line)
    search: str  # standalone retrieval/cross-encoder text (subject carried)
    comparison: bool = False


def _words(text: str) -> list[str]:
    return text.split()


def _is_ask(segment: str) -> bool:
    return segment.rstrip().endswith("?") or bool(_ASK_START_RE.match(segment))


# Pleasantries before the ask ("thanks, bye - oh, does it cover Intune?") and
# dictated punctuation ("... full stop", "... question mark").
_LEAD_PLEASANTRY_RE = re.compile(
    r"^(?:(?:hi|hello|hey|thanks|thank\s{1,3}you|ta|cheers|bye|goodbye|ok|okay|oh|"
    r"um+|uh+|hmm+|er+m?|like|so|well|yeah|yes|great|cool|sorry|actually)"
    r"[\s,.!:;]{1,5}(?:[-\N{EN DASH}\N{EM DASH}][\s,.!:;]{0,3})?)+(?=\S)",
    re.IGNORECASE,
)
_DICTATED_PUNCT_RE = re.compile(
    r"\s{1,3}(?:full\s{1,3}stop|question\s{1,3}mark|exclamation\s{1,3}mark|comma)\s{0,3}$",
    re.IGNORECASE,
)


_CLEAN_EDGE_CHARS = " ,.;:-\N{EN DASH}\N{EM DASH}"


def _clean(segment: str) -> str:
    text = _MARKER_RE.sub("", segment.strip())
    text = _LEAD_CONNECTOR_RE.sub("", text).strip(" ,.;:")
    stripped = _LEAD_PLEASANTRY_RE.sub("", text)
    if _HAS_WORD_RE.search(stripped) and len(_words(stripped)) >= 2:  # noqa: PLR2004
        text = stripped
    return _DICTATED_PUNCT_RE.sub("", text).strip(_CLEAN_EDGE_CHARS)


def _hard_segments(text: str) -> list[tuple[str, bool]]:
    """(segment, is_list_item) at hard boundaries, statements merged into asks."""
    raw: list[tuple[str, bool]] = []
    for line in re.split(r"\n+", text):
        marker = bool(_MARKER_RE.match(line))
        pieces = [
            p.strip()
            for p in _HARD_SPLIT_RE.split(line)
            if p and _HAS_WORD_RE.search(p)
        ]
        # "zero trust basics; frontier package; your phone number": a
        # semicolon list of short topics is one ask per topic.
        topics = ";" in line and all(
            len(_words(p)) <= _TOPIC_MAX_WORDS for p in line.split(";") if p.strip()
        )
        raw.extend(
            (p, marker or topics or (len(pieces) > 1 and _is_ask(p))) for p in pieces
        )
    if not any(_is_ask(p) or item for p, item in raw):
        return [(" ".join(p.strip() for p, _ in raw), False)]
    merged: list[tuple[str, bool]] = []
    pending = ""
    for piece, item in raw:
        if _is_ask(piece) or item:
            merged.append(((pending + " " + piece).strip(), item))
            pending = ""
        else:
            pending = (pending + " " + piece).strip()
    if pending:
        if merged:
            last, item = merged[-1]
            merged[-1] = ((last + " " + pending).strip(), item)
        else:
            merged.append((pending, False))
    return merged


def _comparison_operands(segment: str) -> list[str] | None:
    """The things a comparison names ("compare A, B and C"), or None."""
    rest: str | None = None
    for regex in _COMPARE_RES:
        match = regex.search(segment)
        if match:
            rest = match.group("rest")
            break
    if rest is not None:
        operands = _OPERAND_SPLIT_RE.split(rest.strip(" ?.!"))
    else:
        match = next((m for r in _COMPARE_PAIR_RES if (m := r.search(segment))), None)
        if match is None:
            return None
        operands = [match.group("a"), *_OPERAND_SPLIT_RE.split(match.group("b"))]
    cleaned = [_OPERAND_LEAD_RE.sub("", o.strip(" ?.!,")).strip() for o in operands]
    cleaned = [o for o in cleaned if o and _HAS_WORD_RE.search(o)]
    if len(cleaned) < 2 or any(len(_words(o)) > _OPERAND_MAX_WORDS for o in cleaned):  # noqa: PLR2004
        return None
    if any(_SELF_OPERAND_RE.match(o) for o in cleaned):
        # "How are you different from a one-off auditor?": about us, one ask.
        return None
    return list(dict.fromkeys(cleaned))


def _soft_split(segment: str) -> list[str]:
    """Split one sentence at "and also" / ", also", and before ask words."""
    pieces = [p for p in _ALWAYS_SPLIT_RE.split(segment) if _HAS_WORD_RE.search(p)]
    out: list[str] = []
    for piece in pieces:
        start = 0
        current: list[str] = []
        for match in _ASK_SPLIT_RE.finditer(piece):
            left = piece[start : match.start()]
            right = piece[match.end() :]
            # A leading context clause ("In the CIS assessment, what's ...",
            # "For ISO 27001, what ...", "Hypothetically, if ..., could you
            # ...") is not an ask of its own: it stays with the ask after it.
            if (
                len(_words(left)) >= _MIN_LEFT_WORDS
                and len(_words(right)) >= _MIN_RIGHT_WORDS
                and _is_ask(_clean(left))
                and not _CONTEXT_CLAUSE_RE.match(_clean(left))
            ):
                current.append(left)
                start = match.end()
        current.append(piece[start:])
        out.extend(current)
    return [p.strip(" ,") for p in out if _HAS_WORD_RE.search(p)]


def _lead_and_first(first: str) -> tuple[str, str]:
    """Split "Can you explain Responsible AI" into ("Can you explain", "Responsible AI")."""
    pivots = list(_PIVOT_RE.finditer(first))
    if not pivots:
        return "", first
    cut = pivots[-1].end()
    return first[:cut].strip(), first[cut:].strip()


def _list_split(segment: str) -> list[str]:
    """ "Explain A, B, C and D" -> one ask per listed object (3+ items only)."""
    items = [i for i in _LIST_SPLIT_RE.split(segment.strip(" ?.!")) if i.strip()]
    if len(items) < 2:  # noqa: PLR2004
        return [segment]
    tail = _LAST_AND_RE.split(items[-1])
    if len(tail) == 2 and all(len(_words(t)) <= _LIST_ITEM_MAX_WORDS for t in tail):  # noqa: PLR2004
        items = [*items[:-1], *tail]
    rest = items[1:]
    if len(items) < _MIN_LIST_ITEMS or any(
        len(_words(i)) > _LIST_ITEM_MAX_WORDS or _ASK_START_RE.match(i) for i in rest
    ):
        return [segment]
    lead, first = _lead_and_first(items[0])
    if not lead or not first:
        return [segment]
    return [f"{lead} {item.strip()}" for item in [first, *rest]]


def _entity_and_split(segment: str) -> list[str]:
    """ "Tell me about Zero Trust and Essential Eight": one ask per entity."""
    pieces = _LAST_AND_RE.split(segment.strip(" ?.!"))
    if len(pieces) != 2:  # noqa: PLR2004
        return [segment]
    left, right = pieces
    left_keys = {k for _s, _e, k in entity_mentions(left)}
    right_keys = {k for _s, _e, k in entity_mentions(right)}
    if (
        not left_keys
        or not right_keys
        or left_keys & right_keys
        or len(_words(right)) > _LIST_ITEM_MAX_WORDS
        or _ASK_START_RE.match(right)
    ):
        return [segment]
    lead, _first = _lead_and_first(left)
    return [left, f"{lead} {right}".strip() if lead else right]


def _subject(text: str) -> str | None:
    """The last named service/framework in ``text``, with its tail words."""
    mentions = entity_mentions(text)
    if not mentions:
        return None
    start, end, _key = mentions[-1]
    tail = []
    for word in text[end:].split()[:_SUBJECT_TAIL_WORDS]:
        bare = word.strip("?.,!;:").lower()
        if bare not in _SUBJECT_TAIL:
            break
        tail.append(word.strip("?.,!;:"))
    return " ".join([text[start:end], *tail])


def _names_own_subject(text: str) -> bool:
    """A catalogue entity, an acronym or a capitalised name after the first word."""
    if entity_mentions(text):
        return True
    rest = text.split(" ", 1)[1] if " " in text else ""
    return bool(_PROPER_NOUN_RE.search(rest)) or bool(_ACRONYM_RE.search(text))


def _needs_subject(text: str) -> bool:
    """A referential ("what does it cover?") or elliptic ("how long?") ask."""
    if _names_own_subject(text):
        return False
    if _PRONOUN_RE.search(text):
        return True
    content = [w for w in _WORD_RE.findall(text.lower()) if w not in _FILLER]
    return all(w in _ATTRIBUTE_WORDS for w in content)


def _with_subject(text: str, subject: str) -> str:
    """ "how long does it take?" + "Essential Eight" -> standalone ask."""
    replaced, count = re.subn(
        r"\b(?:it|this|that)\b", subject, text, count=1, flags=re.IGNORECASE
    )
    if count:
        return replaced
    replaced, count = re.subn(
        r"\b(?:its|their)\b", f"{subject}'s", text, count=1, flags=re.IGNORECASE
    )
    if count:
        return replaced
    replaced, count = re.subn(
        r"\b(?:they|them|these|those)\b", subject, text, count=1, flags=re.IGNORECASE
    )
    return replaced if count else f"{text.rstrip(' ?')} ({subject})"


_OUR_NAME_RE = re.compile(r"\b(?:deploy\s{0,3}iq|exigo\s{1,3}tech)\b", re.IGNORECASE)


def _our_name(text: str) -> str | None:
    found = _OUR_NAME_RE.search(text)
    return found.group(0) if found else None


def split_query(query: str) -> list[QueryPart]:
    """Cheap rule-based split of a message into its asks (no LLM call).

    Boundaries: newlines, ';', '?', numbered/bulleted items, ", <ask word>",
    "and/also/plus <ask word>", "and also", lists of objects ("explain A, B
    and C") and comparisons (one part per compared thing). Statements are
    kept as context of the ask that follows them. A referential part ("what
    does it cover?") carries the subject named before it.
    """
    text = "\n".join(
        " ".join(line.split()) for line in query[:_MAX_SCAN_CHARS].splitlines()
    )
    text = text.strip()
    if not text:
        return [QueryPart("", "")]
    settings = get_settings()

    parts: list[QueryPart] = []
    for segment, _item in _hard_segments(text):
        operands = _comparison_operands(segment)
        if operands:
            parts.extend(
                QueryPart(o, f"What is {o}?", comparison=True) for o in operands
            )
            continue
        for soft in _soft_split(segment):
            listed = _list_split(soft)
            if len(listed) == 1:
                listed = _entity_and_split(soft)
            parts.extend(QueryPart(_clean(p), _clean(p)) for p in listed if _clean(p))
    parts = [p for p in parts if _HAS_WORD_RE.search(p.text)] or [QueryPart(text, text)]

    carried: list[QueryPart] = []
    subject: str | None = None
    for raw_part in parts:
        part = raw_part
        own = _subject(part.text)
        refers_back = (
            own is not None and own != subject and _PRONOUN_RE.search(part.text)
        )
        if subject is not None and (
            (own is None and _needs_subject(part.text)) or refers_back
        ):
            part = QueryPart(
                part.text, _with_subject(part.search, subject), part.comparison
            )
        carried.append(part)
        # Our own name is the subject only when no service was named ("Is
        # DeployIQ AI-native, and who funds it?" -> "who funds DeployIQ?").
        subject = own or subject or _our_name(part.text)

    if len(carried) > settings.retrieval_max_parts:
        logger.info(
            "subquery_overflow", parts=len(carried), kept=settings.retrieval_max_parts
        )
        carried = carried[: settings.retrieval_max_parts]
    return carried


def decompose_query(query: str) -> list[str]:
    """The asks of a message, as the visitor wrote them (see ``split_query``)."""
    return [part.text for part in split_query(query)]


def history_subject(history: list[ChatTurn] | None) -> str | None:
    """The service/framework the conversation is about.

    Newest first over the last few turns: a user turn naming one, or an
    assistant answer naming exactly one ("is it self-service?" -> "how do I
    get the tool?" still refers to the plan named two turns back).
    """
    recent = list(reversed(history or []))[:_SUBJECT_LOOKBACK_TURNS]
    for turn in recent:
        text = _scan(turn.content)
        keys = {k for _s, _e, k in entity_mentions(text)}
        if (turn.role == "user" and keys) or (
            turn.role == "assistant" and len(keys) == 1
        ):
            return _subject(text)
    return None


_SUBJECT_LOOKBACK_TURNS = 6


# Purchase wording, dropped from the retrieval text when the ask names a
# service: the pages describe the service, never a price, so "how much is Zero
# Trust?" retrieves (and is gated on) the Zero Trust page; generation then adds
# the public sales contact (RouteDecision.buying).
_BUYING_TERMS_RE = re.compile(
    r"\b(?:how\s{1,3}much\s{1,3}(?:is|are|does|do|for)?|prices?|pricing|priced|"
    r"costs?|costing|quotes?|quotation|estimate|buy|buying|purchas(?:e|ing)|"
    r"sign\s{1,3}(?:(?:me|us)\s{1,3})?up(?:\s{1,3}for)?|subscribe|demo|trial|"
    r"ready\s{1,3}to|"
    r"(?:service|plan|package)\s{0,3}:)\b",
    re.IGNORECASE,
)


def _service_search(part: QueryPart) -> QueryPart:
    """A buying ask that names a service searches for that service."""
    if not entity_mentions(part.search):
        return part
    stripped = " ".join(_BUYING_TERMS_RE.sub(" ", part.search).split()).strip(" ?()")
    return QueryPart(part.text, stripped, part.comparison) if stripped else part


_FOLLOW_UP_MAX_WORDS = 8


def _standalone(part: QueryPart, history: list[ChatTurn] | None) -> QueryPart:
    """Resolve a follow-up against the conversation (subject first, then fold).

    A short ask that names nothing of its own ("which version do you use?",
    "how do I get the tool?") is about the subject the conversation is on.
    """
    if not history or _names_own_subject(part.search):
        return part
    referential = needs_conversation_context(part.text)
    if not referential and len(part.text.split()) > _FOLLOW_UP_MAX_WORDS:
        return part
    subject = history_subject(history)
    if subject is not None:
        return QueryPart(
            part.text, _with_subject(part.search, subject), part.comparison
        )
    if not referential:
        return part
    # A new named subject ("what about SOC 2?") stands on its own.
    rest = part.text.split(" ", 1)[1] if " " in part.text else ""
    if _PROPER_NOUN_RE.search(rest):
        return part
    last_user = _recent(history, "user") or ""
    return QueryPart(part.text, _scan(f"{last_user} {part.search}"), part.comparison)


_E8_RE = re.compile(r"\be8\b", re.IGNORECASE)
_E8_FULL_RE = re.compile(r"\bessential[\s_-]{0,3}(?:eight|8)\b", re.IGNORECASE)
_ML_RE = re.compile(r"\b(?:ml|maturity\s{1,3}level)\s{0,3}([1-3])\b", re.IGNORECASE)
_LEVEL_WORDS = {"1": "one", "2": "two", "3": "three"}
_LOCATION_RE = re.compile(
    r"\bwhere\b[^?.]{0,30}\b(?:based|located|headquartered|headquarters)\b"
    r"|\b(?:head\s{0,3}office|headquarters|hq)\b|\b(?:your|the)\s{1,3}(?:office|location)s?\b",
    re.IGNORECASE,
)
_LOCATION_TERMS = "Exigo Tech company headquarters office location Australia about us"
# Bare Microsoft product words embed poorly on their own ("intune" scored
# cosine 0.2 / logit -10 against the page that covers it): retrieval-only
# expansion to the wording the pages use.
_PRODUCT_EXPANSIONS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), phrase)
    for pattern, phrase in (
        (r"(?<!microsoft )\bintune\b", "Microsoft Intune endpoint management"),
        (r"(?<!microsoft )\bentra(?:\s{1,3}id)?\b", "Microsoft Entra ID identity"),
        (r"\b(?:m365|o365|office\s{0,3}365)\b", "Microsoft 365"),
        (r"(?<!microsoft )\bdefender\b", "Microsoft Defender"),
        (r"(?<!microsoft )\bpurview\b", "Microsoft Purview data protection"),
        (r"(?<!microsoft )\bcopilot\b", "Microsoft Copilot"),
        (r"\bmfa\b", "multi-factor authentication (MFA)"),
        (r"\bmdr\b", "Managed Detection and Response (MDR)"),
        (r"\bzt\b", "Zero Trust"),
        # Only "your/the/this platform" or a bare "platform": "what platform is
        # best for selling shoes" is not about the Deploy IQ platform.
        (
            r"(?:(?<=\byour )|(?<=\bthe )|(?<=\bthis )|(?<=\bur )|^)platform\b",
            "Deploy IQ platform",
        ),
        (
            r"\b(?:ts?\s{0,2}&\s{0,2}cs?|tncs?|t\s{1,3}and\s{1,3}cs?)\b",
            "Terms & Conditions",
        ),
    )
)
# Casual paraphrases the cross-encoder cannot bridge: "who owns you guys?"
# (logit -9.6; "who owns Deploy IQ?" 7.1) and "what jurisdiction applies to
# the T&Cs?" (-10.3; with the pages' "governing law" wording -0.9).
_OWNER_YOU_RE = re.compile(
    r"\b(?:(?<=owns\s)|(?<=runs\s)|(?<=behind\s)|(?<=founded\s)|(?<=started\s))"
    r"(?:you(?:\s{1,3}guys|\s{1,3}all)?|your\s{1,3}(?:company|business|firm)|this\s{1,3}company)\b"
    r"|\b(?:are|r)\s{1,3}(?:you(?:\s{1,3}guys)?)(?=\s{1,3}owned\b)",
    re.IGNORECASE,
)
_JURISDICTION_RE = re.compile(
    r"\b(?:jurisdictions?|which\s{1,3}(?:state'?s?\s{1,3})?laws?|laws?\s{1,3}appl\w{1,4})\b",
    re.IGNORECASE,
)
_GOVERNING_LAW = "governing law"
# Contact asks the contact-us page answers but the embedding misses ("what is
# your email address?" cosine 0.24, below the off-domain floor). English nouns
# need a "you/your/Deploy IQ" marker ("the IP address of google" is not ours);
# the common non-English office/contact words stand on their own.
_CONTACT_EN_RE = re.compile(
    r"\b(?:e-?mail(?:\s{1,3}address)?|phone(?:\s{1,3}number)?|telephone|"
    r"contact\s{1,3}(?:details|info|information|number|address)|addresse?s?|offices?|"
    r"mail\s{1,3}(?:you|us|address)|(?:your|ur)\s{1,3}mail|"
    r"reach\s{1,3}(?:you|your|us|out)|(?:get|be|keep)\s{1,3}in\s{1,3}touch)\b",
    re.IGNORECASE,
)
_CONTACT_YOU_RE = re.compile(
    r"\b(?:your|you|ur|deploy\s{0,3}iq|deployiq|exigo)\b", re.IGNORECASE
)
_CONTACT_INTL_RE = re.compile(
    r"\b(?:b(?:ü|ue|u)ros?|telefon(?:nummer)?|kontakt|e-?mail-?adresse|anschrift|"
    r"oficinas?|direcci[óo]n|correo|tel[ée]fono|contacto|bureaux?|"
    r"t[ée]l[ée]phone|courriel|coordonn[ée]es|uffici?[oa]?|indirizzo|contatt[oi]|"
    r"escrit[óo]rios?|endere[çc]o|v[ăa]n\s{1,3}ph[òo]ng|kantoor)\b",
    re.IGNORECASE,
)
_CONTACT_TERMS = "Deploy IQ Contact Us contact information phone email office address"
# "Free trial" is the home page's free Security Posture Assessment / Snapshot.
_FREE_TRIAL_RE = re.compile(
    r"\bfree\s{1,3}(?:trial|test|version|tier|evaluation)s?\b|\btrials?\b",
    re.IGNORECASE,
)
_FREE_TRIAL_TERMS = "free Security Posture Assessment snapshot"


# A bare contact fragment ("what's the phone number", "email?") is ours: no
# other party is named in it.
_CONTACT_FRAGMENT_RE = re.compile(
    r"\b(?:e-?mail(?:\s{1,3}address)?|phone(?:\s{1,3}number)?|telephone|number\s{1,3}to\s{1,3}(?:call|ring)|"
    r"contact\s{1,3}(?:details|info|information|number))\b",
    re.IGNORECASE,
)
_OTHER_PARTY_RE = re.compile(
    r"\b(?:of|for|from|at)\s{1,3}(?!you\b|your\b|us\b)\w", re.IGNORECASE
)
_CONTACT_FRAGMENT_MAX_WORDS = 8
# "gmail's email", "google's phone number": another party's contact details
# ("what's" / "deploy iq's" are not another party).
_OTHER_POSSESSIVE_RE = re.compile(
    r"\b(?!(?:what|that|where|who|it|here|there|how|let|he|she|iq|deploy\s{0,3}iq|"
    r"exigo)['\N{RIGHT SINGLE QUOTATION MARK}]s\b)"
    r"[a-z][\w-]*['\N{RIGHT SINGLE QUOTATION MARK}]s\b",
    re.IGNORECASE,
)


def _contact_ask(text: str) -> bool:
    if _OTHER_POSSESSIVE_RE.search(text) is not None:
        return False
    if _CONTACT_INTL_RE.search(text) is not None or (
        _CONTACT_EN_RE.search(text) is not None
        and _CONTACT_YOU_RE.search(text) is not None
    ):
        return True
    # "what about email (Security Baseline Assessment)": a follow-up about a
    # named service's email area, not our email address.
    return (
        _CONTACT_FRAGMENT_RE.search(text) is not None
        and len(_words(text)) <= _CONTACT_FRAGMENT_MAX_WORDS
        and _OTHER_PARTY_RE.search(text) is None
        and not entity_mentions(text)
    )


def is_contact_ask(text: str) -> bool:
    """Our own contact details: "what's your email?", "how do I reach you"."""
    return _contact_ask(text)


# A pasted link to one of our pages ("https://deployiq.co/packages what's on
# this page?") is that page by name: "the packages page" (page scope).
_SITE_URL_RE = re.compile(
    r"(?:https?://)?(?:[\w-]{1,30}\.){0,3}deploy-?iq\.[a-z]{2,6}(?:\.[a-z]{2})?"
    r"(?P<path>/[\w\-/]{0,120})?(?:[?#]\S{0,200})?",
    re.IGNORECASE,
)


def _page_name_from_url(match: re.Match[str]) -> str:
    slug = (match.group("path") or "/").strip("/").split("/")[0]
    words = " ".join(slug.replace("_", "-").split("-")).strip()
    return f"the {words or 'home'} page"


def resolve_page_urls(text: str) -> str:
    """Our page URLs in an ask become the page's name (retrieval only)."""
    return _SITE_URL_RE.sub(_page_name_from_url, text)


# Courtesy sentences around a long message's real ask ("Good afternoon, I hope
# this message finds you well. ... Many thanks for your patience."): the
# cross-encoder scores the whole text, and the padding sank an in-KB Essential
# Eight question to -10.3. Retrieval drops them and resolves "it".
_COURTESY_RE = re.compile(
    r"^(?:good\s{1,3}(?:morning|afternoon|evening|day)|hi|hello|hey|dear|greetings|"
    r"i\s{1,3}hope\s{1,3}(?:this|you|all)|(?:many\s{1,3}|thanks?\s{1,3})?thanks|thank\s{1,3}you|"
    r"cheers|(?:kind\s{1,3}|best\s{1,3})?regards|best\s{1,3}wishes|sorry\s{1,3}to\s{1,3}bother|"
    r"(?:could|can)\s{1,3}you\s{1,3}let\s{1,3}me\s{1,3}know|appreciate\s{1,3}(?:it|your))\b",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s{1,3}")
_COURTESY_MAX_WORDS = 12
_LONG_ASK_WORDS = 25


def distil_long_ask(text: str) -> str:
    """A long message without its courtesy sentences, "it" resolved to the
    service it names (retrieval/gate text only; short asks unchanged)."""
    if len(_words(text)) <= _LONG_ASK_WORDS:
        return text
    sentences = [s for s in _SENTENCE_RE.split(text) if _HAS_WORD_RE.search(s)]
    kept = [
        s
        for s in sentences
        if not (_COURTESY_RE.match(s.strip()) and len(_words(s)) <= _COURTESY_MAX_WORDS)
    ]
    if not kept or len(kept) == len(sentences):
        return text
    distilled = " ".join(kept)
    subject = _subject(distilled)
    last = kept[-1]
    if subject is not None and subject not in last and _PRONOUN_RE.search(last):
        distilled = " ".join([*kept[:-1], _with_subject(last, subject)])
    return distilled


_TYPO_MIN_LEN = 5
_TYPO_LONG_WORD = 8
_TYPO_CUTOFF = 0.8
_TYPO_WORD_RE = re.compile(rf"[A-Za-z]{{{_TYPO_MIN_LEN},}}")


# Typo targets: the service / product / framework words visitors misspell.
# Correcting toward any KB word rewrote real English ("weather" -> "whether",
# "stages" -> "states"), so only these distinctive terms are targets.
_DOMAIN_TERMS = frozenset(
    [
        "essential",
        "eight",
        "zero",
        "trust",
        "conditional",
        "access",
        "assessment",
        "assessments",
        "security",
        "compliance",
        "compliant",
        "baseline",
        "foundation",
        "foundations",
        "strengthen",
        "frontier",
        "fortify",
        "governance",
        "intune",
        "defender",
        "purview",
        "sharepoint",
        "exchange",
        "onedrive",
        "copilot",
        "entra",
        "microsoft",
        "privacy",
        "encryption",
        "maturity",
        "application",
        "applications",
        "patching",
        "patch",
        "benchmark",
        "responsible",
        "certification",
        "certified",
        "authentication",
        "multifactor",
        "deployiq",
        "packages",
        "package",
        "platform",
        "consultation",
        "cybersecurity",
        "mitigation",
        "strategies",
        "controls",
        "identity",
        "endpoint",
        "endpoints",
        "vulnerability",
        "vulnerabilities",
        "phishing",
        "backups",
        "backup",
        "tenant",
        "posture",
        "readiness",
        "pricing",
        "implementation",
        "hardening",
        "onboarding",
        "managed",
        "detection",
        "response",
        "monitoring",
        "framework",
        "frameworks",
        "policies",
        "policy",
    ]
)


def correct_typos(text: str, vocabulary: frozenset[str]) -> str:
    """Misspelt service words ("esential eigth", "conditonal acess",
    "securty assesment") -> the domain term (retrieval only). A word the KB
    uses, or shorter than five letters, is never touched."""
    if not vocabulary:
        return text
    targets = sorted(_DOMAIN_TERMS & vocabulary)
    if not targets:
        return text

    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        low = word.lower()
        if low in vocabulary or low in _FILLER:
            return word
        # Typos drop or swap letters ("esential", "eigth"): a shorter term
        # is only a fix for a long word.
        shortest = len(low) - 1 if len(low) >= _TYPO_LONG_WORD else len(low)
        near = [
            w for w in targets if w[0] == low[0] and shortest <= len(w) <= len(low) + 1
        ]
        found = difflib.get_close_matches(low, near, n=1, cutoff=_TYPO_CUTOFF)
        return found[0] if found else word

    return _TYPO_WORD_RE.sub(fix, text)


def expand_retrieval_query(text: str) -> str:
    """Deterministic abbreviation expansion for retrieval only (never shown).

    "e8 ml3" -> "Essential Eight maturity level three"; a "where are you
    based" question also carries the company-fact vocabulary so the about-us
    chunk is retrieved on its own merit.
    """
    expanded = _ML_RE.sub(lambda m: f"maturity level {_LEVEL_WORDS[m.group(1)]}", text)
    if _E8_RE.search(expanded) and not _E8_FULL_RE.search(expanded):
        expanded = _E8_RE.sub("Essential Eight", expanded)
    if _LOCATION_RE.search(expanded) and _LOCATION_TERMS not in expanded:
        expanded = f"{expanded} {_LOCATION_TERMS}"
    for pattern, phrase in _PRODUCT_EXPANSIONS:
        if phrase.lower() not in expanded.lower():
            expanded = pattern.sub(phrase, expanded, count=1)
    expanded = _OWNER_YOU_RE.sub("Deploy IQ", expanded, count=1)
    if _JURISDICTION_RE.search(expanded) and _GOVERNING_LAW not in expanded.lower():
        expanded = f"{expanded} ({_GOVERNING_LAW})"
    if _contact_ask(expanded) and _CONTACT_TERMS not in expanded:
        expanded = f"{expanded} {_CONTACT_TERMS}"
    if _FREE_TRIAL_RE.search(expanded) and _FREE_TRIAL_TERMS not in expanded:
        expanded = f"{expanded} {_FREE_TRIAL_TERMS}"
    return expanded


# ---------------------------------------------------------------------------
# Per-part retrieval and the relevance gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PartRetrieval:  # pylint: disable=too-many-instance-attributes
    """What one ask of the message retrieved on its own."""

    query: str
    max_score: float  # best cosine
    chunk_ids: frozenset[str]
    contents: tuple[str, ...]
    search: str = ""
    max_ce: float | None = None  # best raw cross-encoder logit (None: reranker off)
    document_ids: tuple[str, ...] = ()  # best first
    sources: tuple[SourceChunk, ...] = ()  # this part's chunks, best first
    comparison: bool = False
    bare_topic: bool = False  # a named KB topic whose page was retrieved
    card_evidence: bool = False  # a named tier's card states the asked attribute
    ce_by_chunk: dict[str, float] = field(default_factory=dict)
    embedding: tuple[float, ...] = field(default=(), repr=False, compare=False)
    non_english: bool = False  # gated on cosine (the cross-encoder is English-only)


# Filled by retrieve() with every part (one for a single ask); read by the
# orchestrator.
part_retrieval_sink: ContextVar[list[PartRetrieval] | None] = ContextVar(
    "part_retrieval_sink", default=None
)


_PRICE_ASK_RE = re.compile(
    r"\b(?:how\s{1,3}much|prices?|pricing|costs?|fees?|charge|per\s{1,3}(?:month|user|year)|"
    r"quote|rates?)\b",
    re.IGNORECASE,
)


# Company facts and third-party platforms an ask can name that the pages may
# not mention at all (asked pattern, evidence pattern in the chunk text). The
# cross-encoder scores "who is your CEO" or "Google Workspace" in the middle
# zone (-2.3 .. 0.3) against unrelated chunks of the same page.
_ASKED_ENTITIES: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = tuple(
    (re.compile(asked, re.IGNORECASE), re.compile(evidence, re.IGNORECASE))
    for asked, evidence in (
        (
            r"\b(?:ceo|cto|cfo|coo|chief\s{1,3}\w{1,12}\s{1,3}officer|"
            r"managing\s{1,3}director|chair(?:man|person)|co-?founders?|founders?)\b",
            r"\b(?:ceo|cto|cfo|coo|chief\s{1,3}\w{1,12}\s{1,3}officer|"
            r"managing\s{1,3}director|chair(?:man|person)|co-?founders?|founders?|"
            r"founded\s{1,3}by)\b",
        ),
        (
            r"\bhow\s{1,3}many\s{1,3}(?:employees|staff|people|engineers|consultants)\b|"
            r"\b(?:headcount|employee\s{1,3}count|number\s{1,3}of\s{1,3}(?:employees|staff))\b",
            r"\b(?:employees|staff|headcount|team\s{1,3}of\s{1,3}\d)",
        ),
        (
            r"\bgoogle\s{1,3}workspace\b|\bg[\s-]{0,2}suite\b|\bgmail\b",
            r"\bgoogle\b|\bgmail\b",
        ),
        (r"\bgoogle\s{1,3}cloud\b|\bgcp\b", r"\bgoogle\s{1,3}cloud\b|\bgcp\b"),
        (r"\baws\b|\bamazon\s{1,3}web\s{1,3}services\b", r"\baws\b|\bamazon\b"),
        (
            r"\b(?:salesforce|okta|kubernetes|fortigate|fortinet|crowdstrike|"
            r"sentinelone|splunk|jamf|dropbox|zoom|slack)\b",
            r"\b(?:salesforce|okta|kubernetes|fortigate|fortinet|crowdstrike|"
            r"sentinelone|splunk|jamf|dropbox|zoom|slack)\b",
        ),
    )
)


def asked_entity_missing(part: PartRetrieval) -> bool:
    """A company fact or third-party platform the part asks about that none
    of its retrieved chunks mentions (deterministic lexical check)."""
    query = _scan(part.query)
    for asked, evidence in _ASKED_ENTITIES:
        if asked.search(query) and not any(evidence.search(c) for c in part.contents):
            return True
    return False


def part_covered(part: PartRetrieval, *, multi_part: bool = False) -> bool:
    """Calibrated: the best chunk's cross-encoder logit (cosine if no reranker).

    Off-domain text needs at least the "relevant" logit; so does a price ask
    inside a multi-part message (the pages publish no prices, so it is its
    own uncovered part unless a page really answers it). Inside a multi-part
    message a part asking about a person, headcount or non-Microsoft platform
    no retrieved chunk mentions is uncovered whatever its logit.
    """
    settings = get_settings()
    if multi_part and asked_entity_missing(part):
        return False
    if part.max_ce is None:
        # No cross-encoder logit (reranker off, or a non-English ask scored
        # on the multilingual embedding alone): a non-English ask needs the
        # on-domain cosine (off-domain asks stay below it).
        floor = (
            settings.retrieval_offdomain_max_cosine
            if part.non_english
            else settings.min_retrieval_score
        )
        return part.max_score >= floor
    strict = _low_cosine(part) or (
        multi_part and _PRICE_ASK_RE.search(_scan(part.query)) is not None
    )
    floor = (
        settings.retrieval_ce_relevant_min
        if strict
        else settings.retrieval_ce_covered_min
    )
    return part.max_ce >= floor or (not strict and _borderline(part))


# Borderline band: a colloquial in-KB ask ("where would my data physically be
# stored?" -8.96 / 0.331, "is data encrypted end to end?" -9.21 / 0.397)
# scores a weak logit on the page that answers it but keeps a strong dense
# match. It goes to generation, which covers a part only when a quoted fact
# answers it. Picked on 34 colloquial in-KB asks vs 128 negatives: a -10.5
# floor routed 7 more in-KB asks but live generation answered "do you offer
# penetration testing?" (-9.57 / 0.436) from adjacent service text; -9.4
# keeps 4 and adds no negative (off-domain asks also stay below the cosine:
# time in Sydney -8.88 / 0.315, car settings 0.319, Netflix data 0.301).
_BORDERLINE_CE_MIN = -9.4
_BORDERLINE_COS_MIN = 0.33


def _borderline(part: PartRetrieval) -> bool:
    return (
        part.max_ce is not None
        and part.max_ce >= _BORDERLINE_CE_MIN
        and part.max_score >= _BORDERLINE_COS_MIN
    )


# What a bare-topic ask may add around the topic ("tell me about ISO 27001").
_OVERVIEW_WORDS = frozenset(
    [
        "tell",
        "me",
        "about",
        "what",
        "is",
        "are",
        "the",
        "a",
        "an",
        "explain",
        "describe",
        "overview",
        "info",
        "information",
        "more",
        "on",
        "your",
        "you",
        "do",
        "offer",
        "please",
        "details",
        "detail",
        "give",
    ]
)


def _is_bare_topic(text: str, found: list, vector_store: VectorStore) -> bool:
    """ "ISO 27001", "e8", "tell me about zero trust": a catalogue topic whose
    own page was retrieved is covered by definition (deterministic, never left
    to the logit or the model): the answer is that page's overview."""
    scan = _scan(text)
    mentions = entity_mentions(scan)
    if not mentions:
        return False
    rest = scan
    for start, end, _key in reversed(mentions):
        rest = rest[:start] + " " + rest[end:]
    words = _WORD_RE.findall(rest.lower())
    if any(w not in _OVERVIEW_WORDS for w in words):
        return False
    names = {
        str(doc_id): f"{link.source_name or ''} {link.title or ''}"
        for doc_id, link in getattr(vector_store, "document_links", {}).items()
    }
    retrieved = {str(chunk.document_id) for chunk, _score in found}
    keys = [key for _s, _e, key in mentions]
    return bool(set(entity_documents(keys, names)) & retrieved)


# Tier cards ("Not sure which service fits?" on the home page) label their
# facts; the cross-encoder scores "how long does Strengthen take" against
# "Typical roadmap: Ongoing" far below the gate (-9). (asked, card label)
_TIER_NAME_RE = re.compile(
    r"\b(baseline|foundation|strengthen|frontier|fortify)\b", re.IGNORECASE
)
_CARD_ATTRIBUTES: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = tuple(
    (re.compile(asked, re.IGNORECASE), re.compile(label, re.IGNORECASE))
    for asked, label in (
        (
            r"\bhow\s{1,3}long\b|\b(?:takes?|timelines?|duration|timeframe|weeks?)\b",
            r"\btypical\s{1,3}roadmap\s{0,3}:",
        ),
        (
            r"\b(?:frameworks?|aligned|standards?)\b",
            r"\baligned\s{1,3}framework\s{0,3}:",
        ),
        (r"\b(?:outcomes?|results?)\b", r"\bexpected\s{1,3}outcome\s{0,3}:"),
        (
            r"\bwhat\s{1,3}(?:do\s{1,3}(?:i|we)\s{1,3})?get\b|\bdeliverables?\b",
            r"\bwhat\s{1,3}you\s{1,3}get\b",
        ),
    )
)


def _card_evidence(text: str, found: list) -> bool:
    """A tier the ask names whose own card (section heading = tier name) among
    the retrieved chunks states the attribute asked about (deterministic)."""
    scan = _scan(text)
    tiers = {m.lower() for m in _TIER_NAME_RE.findall(scan)}
    labels = [label for asked, label in _CARD_ATTRIBUTES if asked.search(scan)]
    if not tiers or not labels:
        return False
    for chunk, _score in found:
        heading = str((chunk.chunk_metadata or {}).get("section_heading") or "")
        if heading.strip().lower() in tiers and any(
            label.search(chunk.content) for label in labels
        ):
            return True
    return False


_COUNT_ASK_RE = re.compile(r"\bhow\s{1,3}many\s{1,3}([a-z][a-z-]{2,30})", re.IGNORECASE)


def _count_values(noun: str, content: str) -> set[str]:
    """Numbers stated for ``noun`` ("42 controls", "39 controls across ...")."""
    stem = noun.lower().rstrip("s")
    pattern = re.compile(
        rf"\b(\d{{1,4}})\+?\s{{1,3}}(?:[a-z&-]{{1,20}}\s{{1,3}}){{0,2}}{re.escape(stem)}s?\b",
        re.IGNORECASE,
    )
    return set(pattern.findall(content))


def _named_count_wins(text: str, found: list, vector_store: VectorStore) -> list:
    """ "How many controls does the Security Baseline Assessment cover?": when
    the named page states the count, another page's different count for the
    same noun (the Baseline package card's 39 vs the page's 42) is dropped so
    the named page's value is the one answered and cited."""
    scan = _scan(text)
    ask = _COUNT_ASK_RE.search(scan)
    keys = list(dict.fromkeys(key for _s, _e, key in entity_mentions(scan)))
    if ask is None or not keys:
        return found
    names = {
        str(doc_id): f"{link.source_name or ''} {link.title or ''}"
        for doc_id, link in getattr(vector_store, "document_links", {}).items()
    }
    named = set(entity_documents(keys, names))
    noun = ask.group(1)
    stated: set[str] = set()
    for chunk, _score in found:
        if str(chunk.document_id) in named:
            stated |= _count_values(noun, chunk.content)
    if not stated:
        return found
    kept = [
        (chunk, score)
        for chunk, score in found
        if str(chunk.document_id) in named
        or not (values := _count_values(noun, chunk.content))
        or values & stated
    ]
    if len(kept) != len(found):
        logger.info(
            "named_count_conflict_dropped", noun=noun, dropped=len(found) - len(kept)
        )
    return kept


_ORDINAL = r"(?:first|second|third|fourth|last|other|previous|next|latter|former)"
_THAT = r"(?:that|this|it|them|those|these)"
# A whole message that only points back ("what about the second one?", "can
# you explain that again?", "which one is better?"): never off-topic, it is a
# reference to something not (or no longer) in view.
_BARE_REFERENCE_RE = re.compile(
    r"^(?:(?:and|so|ok|okay|but|also|hmm)\s{1,3}){0,2}(?:"
    rf"(?:what|how)\s{{1,3}}about\s{{1,3}}(?:the\s{{1,3}})?{_ORDINAL}(?:\s{{1,3}}ones?)?"
    rf"|(?:the\s{{1,3}})?{_ORDINAL}\s{{1,3}}ones?"
    rf"|(?:(?:please\s{{1,3}})?tell\s{{1,3}}me\s{{1,3}})?more"
    rf"(?:\s{{1,3}}(?:about|on)\s{{1,3}}{_THAT})?(?:\s{{1,3}}please)?"
    r"|(?:(?:can|could|would)\s{1,3}you\s{1,3}(?:please\s{1,3})?)?"
    r"(?:explain|repeat|say|clarify|go\s{1,3}over|elaborate\s{1,3}on)\s{1,3}"
    rf"{_THAT}(?:\s{{1,3}}again)?(?:\s{{1,3}}(?:please|for\s{{1,3}}me))?"
    r"|which\s{1,3}(?:one|option|of\s{1,3}(?:them|those|these|the\s{1,3}two))"
    r"(?:\s{1,3}(?:is|would\s{1,3}be|should\s{1,3}(?:i|we)\s{1,3}(?:choose|pick|get)))?"
    r"(?:\s{1,3}(?:better|best|right|recommended|cheaper|faster))?"
    r"(?:\s{1,3}for\s{1,3}(?:me|us))?"
    r")\s{0,3}[?.!]{0,3}$",
    re.IGNORECASE,
)


def is_bare_reference(text: str) -> bool:
    """The message only points back at something ("the second one?")."""
    return _BARE_REFERENCE_RE.match(_scan(text).strip()) is not None


# Commercial asks ("plans?", "quote pls", "can I pay by credit card?") are about
# doing business with us: never off-topic, at worst not covered.
_COMMERCIAL_RE = re.compile(
    r"\b(?:plans?|packages?|pric(?:e|es|ing)|costs?|quotes?|quotation|buy|buying|"
    r"purchas(?:e|ing)|pay|paying|payments?|invoices?|invoicing|billing|billed|"
    r"credit\s{1,3}cards?|debit\s{1,3}cards?|bank\s{1,3}transfer|subscri(?:be|ption)s?|"
    r"demo|trial|discounts?|refunds?|contracts?|fees?)\b",
    re.IGNORECASE,
)


# "Which laptop should I buy?", "buy a new phone": the bought thing is named
# and it is not one of ours (plans, packages, services, assessments).
_BUY_OBJECT_RE = re.compile(
    r"\b(?:which|what)\s{1,3}([a-z][\w-]{1,30})\s{1,3}(?:should|shall|do|can|could|would|to)\b"
    r"[^?.]{0,20}\b(?:buy|purchase)\b|"
    r"\b(?:buy|buying|purchase|purchasing)\s{1,3}(?:a|an|the|some|new|me\s{1,3}a)\s{1,3}"
    r"(?:new\s{1,3})?([a-z][\w-]{1,30})",
    re.IGNORECASE,
)
_OUR_OFFERINGS = frozenset(
    [
        "plan",
        "plans",
        "package",
        "packages",
        "service",
        "services",
        "assessment",
        "assessments",
        "subscription",
        "subscriptions",
        "licence",
        "license",
        "licenses",
        "licences",
        "tier",
        "snapshot",
        "one",
        "deployiq",
        "deploy",
        "option",
        "offering",
        "product",
        "tool",
        "platform",
    ]
)


def _third_party_purchase(scan: str) -> bool:
    match = _BUY_OBJECT_RE.search(scan)
    if match is None:
        return False
    obj = (match.group(1) or match.group(2) or "").lower()
    return obj not in _OUR_OFFERINGS and obj not in _FILLER


def is_commercial(text: str) -> bool:
    """A price / plan / payment / purchase ask about doing business with us
    (never off-topic): "the price of a burger" names another party's thing."""
    scan = _scan(text)
    if _COMMERCIAL_RE.search(scan) is None:
        return False
    if not entity_mentions(scan) and _third_party_purchase(scan):
        return False
    return bool(
        entity_mentions(scan)
        or _CONTACT_YOU_RE.search(scan)
        or _OTHER_PARTY_RE.search(scan) is None
    )


def _low_cosine(part: PartRetrieval) -> bool:
    return part.max_score < get_settings().retrieval_offdomain_max_cosine


def part_off_domain(part: PartRetrieval) -> bool:
    return _low_cosine(part) and not is_commercial(part.query)


def decide_route(
    parts: Iterable[PartRetrieval], *, buying: bool = False
) -> RouteDecision:
    """ANSWER / PARTIAL / NOT_COVERED / OFF_TOPIC from per-part verdicts."""
    verdicts: list[PartVerdict] = []
    off_domain: list[bool] = []
    parts = list(parts)
    for part in parts:
        covered = (
            part.bare_topic
            or part.card_evidence
            or part_covered(part, multi_part=len(parts) > 1)
        )
        verdicts.append(
            PartVerdict(
                text=part.query,
                covered=covered,
                score=round(
                    part.max_ce if part.max_ce is not None else part.max_score, 3
                ),
                document_ids=part.document_ids if covered else (),
            )
        )
        off_domain.append(
            not covered and part_off_domain(part) and not is_bare_reference(part.query)
        )
    if not verdicts:
        return RouteDecision(Route.NOT_COVERED, reason="no_parts", buying=buying)
    covered_count = sum(v.covered for v in verdicts)
    if covered_count == len(verdicts):
        bare = all(p.bare_topic for p in parts)
        route, reason = Route.ANSWER, "bare_topic" if bare else "all_parts_covered"
    elif covered_count:
        route, reason = Route.PARTIAL, f"{covered_count}/{len(verdicts)}_covered"
    elif all(off_domain):
        route, reason = Route.OFF_TOPIC, "off_domain"
    else:
        route, reason = Route.NOT_COVERED, "below_relevance_gate"
    return RouteDecision(route, parts=tuple(verdicts), reason=reason, buying=buying)


# How far below the covered logit a reply to a KB-built card is still trusted:
# real option picks lead with the option's own name and score well above it;
# nonsense glued onto the card's query ("plans? plorb wibble", CE -11.0) does not.
_CLARIFIED_CE_MARGIN = 1.5


def trust_clarified(decision: RouteDecision) -> RouteDecision:
    """A reply to a KB-built card: NOT_COVERED becomes ANSWER (never OFF_TOPIC),
    unless its best logit is far below the covered floor."""
    if decision.route is not Route.NOT_COVERED:
        return decision
    floor = get_settings().retrieval_ce_covered_min - _CLARIFIED_CE_MARGIN
    if any(v.score < floor for v in decision.parts):
        return decision
    parts = tuple(
        PartVerdict(v.text, True, v.score, v.document_ids) for v in decision.parts
    )
    return RouteDecision(
        Route.ANSWER, parts=parts, reason="clarification_reply", buying=decision.buying
    )


@dataclass(frozen=True)
class RetrievalResult:
    """Merged context plus each part's own retrieval."""

    sources: list[SourceChunk]
    parts: list[PartRetrieval]

    @property
    def max_ce(self) -> float | None:
        values = [p.max_ce for p in self.parts if p.max_ce is not None]
        return max(values) if values else None


async def embed_query(query: str) -> list[float]:
    """Generate a query embedding with the async Azure client."""

    return await embed_text(query)


def _part_budget(top_k: int, parts: int) -> tuple[int, int]:
    """(chunks per part, merged cap): every part keeps at least the minimum."""
    settings = get_settings()
    if parts <= 1:
        return top_k, top_k
    total = min(
        settings.retrieval_multi_part_max_chunks,
        max(
            top_k,
            settings.compound_query_top_k,
            settings.retrieval_part_min_chunks * parts,
        ),
    )
    per_part = max(settings.retrieval_part_min_chunks, math.ceil(total / parts))
    cap = max(total, settings.retrieval_part_min_chunks * parts)
    return per_part, cap


def _to_source(
    chunk, score: float, vector_store: VectorStore, allowed_hosts
) -> SourceChunk:  # noqa: ANN001
    found = getattr(vector_store, "document_links", {}).get(chunk.document_id)
    url = title = None
    if found is not None:
        url = citation_url(
            found.source_url,
            section_links=found.section_links,
            metadata=chunk.chunk_metadata,
            allowed_hosts=allowed_hosts,
        )
        title = found.title
    return SourceChunk(
        chunk_id=str(chunk.id),
        document_id=str(chunk.document_id),
        content=chunk.content,
        score=score,
        metadata=chunk.chunk_metadata,
        url=url,
        title=title,
    )


async def route_sources(vector_store: VectorStore, route: str) -> list[SourceChunk]:
    """The chunks of the KB page at ``route`` as citable sources (score 0:
    fetched by route, not by relevance)."""
    allowed_hosts = current_knowledge_settings().allowed_hosts
    return [
        _to_source(chunk, 0.0, vector_store, allowed_hosts)
        for chunk in await vector_store.chunks_for_route(route)
    ]


async def retrieve_parts(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    vector_store: VectorStore,
    query: str,
    top_k: int = 5,
    filters: dict | None = None,
    history: list[ChatTurn] | None = None,
    max_documents: int | None = None,
    *,
    split: bool = True,
    buying: bool = False,
    prefer_documents: list[str] | None = None,
) -> RetrievalResult:
    """Retrieve every ask of the message on its own; merge with fair budgets.

    ``split=False`` treats the message as one ask (a reply to a clarification
    is the original question plus the answer, never several asks).
    ``prefer_documents`` (the clarification options' pages) adds their best
    chunks for the ask, scored by the cross-encoder, ahead of the rest.
    """
    settings = get_settings()
    query = resolve_page_urls(query)
    # Split from the user's own current message only, never from history.
    parts = (
        split_query(query)
        if split and settings.enable_query_decomposition
        else [QueryPart(_scan(query), _scan(query))]
    )
    parts = [_standalone(p, history) for p in parts]
    if buying and len(parts) == 1:
        # A whole-message purchase ask about a named service is answered from
        # that service's page; inside a multi-part ask the price part stays
        # its own (uncovered) part.
        parts = [_service_search(parts[0])]
    if len(parts) > 1:
        logger.info(
            "subquery_decomposition",
            subqueries_count=len(parts),
            subqueries=[log_safe_text(p.text) for p in parts],
            searches=[log_safe_text(p.search) for p in parts],
        )
    vocabulary_of = getattr(vector_store, "corpus_vocabulary", None)
    vocabulary = await vocabulary_of() if callable(vocabulary_of) else frozenset()
    if not isinstance(vocabulary, frozenset):
        vocabulary = frozenset()
    searches = [
        expand_retrieval_query(
            correct_typos(
                distil_long_ask(strip_leading_interjections(p.search or p.text)),
                vocabulary,
            )
        )
        for p in parts
    ]
    per_part_k, cap = _part_budget(top_k, len(parts))

    embed_started = time.perf_counter()
    # One embedding request for every ask (cached texts are not re-sent).
    embeddings = (
        [await embed_query(searches[0])]
        if len(searches) == 1
        else await embed_texts(searches)
    )
    embed_sink = embedding_timing_sink.get()
    if embed_sink is not None:
        embed_sink.append((time.perf_counter() - embed_started) * 1000)

    allowed_hosts = current_knowledge_settings().allowed_hosts
    results: list[PartRetrieval] = []
    # The DB searches share one AsyncSession (no concurrent operations).
    for part, search, embedding in zip(parts, searches, embeddings, strict=True):
        found = await vector_store.similarity_search(
            embedding,
            top_k=per_part_k,
            filters=filters,
            query_text=search,
            **({"max_documents": max_documents} if max_documents else {}),
        )
        logits: dict[uuid.UUID, float] = dict(
            getattr(vector_store, "last_ce_scores", {}) or {}
        )
        if prefer_documents and len(parts) == 1:
            found = await _with_preferred(
                vector_store, embedding, search, found, logits, prefer_documents, top_k
            )
        found = _named_count_wins(part.text, found, vector_store)
        sources = tuple(_to_source(c, s, vector_store, allowed_hosts) for c, s in found)
        non_english = not looks_english(search)
        if non_english:
            logits = {}
        ce_by_chunk = {str(k): v for k, v in logits.items()}
        results.append(
            PartRetrieval(
                query=part.text,
                search=search,
                max_score=max((s for _c, s in found), default=0.0),
                max_ce=max(logits.values()) if logits else None,
                chunk_ids=frozenset(str(c.id) for c, _s in found),
                contents=tuple(c.content for c, _s in found),
                document_ids=tuple(
                    dict.fromkeys(str(c.document_id) for c, _s in found)
                ),
                sources=sources,
                comparison=part.comparison,
                bare_topic=_is_bare_topic(
                    correct_typos(part.text, vocabulary), found, vector_store
                ),
                card_evidence=_card_evidence(part.text, found),
                ce_by_chunk=ce_by_chunk,
                embedding=tuple(embedding),
                non_english=non_english,
            )
        )

    merged = _round_robin([list(p.sources) for p in results], cap)
    embeddings_by_id = await vector_store.fetch_embeddings(
        [uuid.UUID(s.chunk_id) for s in merged]
    )
    for source in merged:
        source.embedding = embeddings_by_id.get(uuid.UUID(source.chunk_id))
    return RetrievalResult(sources=merged, parts=results)


async def _with_preferred(  # noqa: PLR0913  # pylint: disable=too-many-arguments,too-many-positional-arguments  # one retrieval step's full context
    vector_store: VectorStore,
    embedding: list[float],
    search: str,
    found: list,
    logits: dict[uuid.UUID, float],
    document_ids: list[str],
    top_k: int,
) -> list:
    """The preferred pages' best chunks first (cross-encoder order), then the rest."""
    fetch = getattr(vector_store, "chunks_in_documents", None)
    if fetch is None:
        return found
    extra = [
        (c, sc)
        for c, sc in await fetch(embedding, document_ids)
        if c.id not in {chunk.id for chunk, _s in found}
    ]
    if extra:
        extra_logits: dict[uuid.UUID, float] = {}
        extra = await rerank_async(search, extra, None, extra_logits)
        logits.update(extra_logits)
    preferred = set(document_ids)
    ranked = extra + [item for item in found if str(item[0].document_id) in preferred]
    ranked.sort(key=lambda item: logits.get(item[0].id, float("-inf")), reverse=True)
    rest = [item for item in found if str(item[0].document_id) not in preferred]
    return (ranked + rest)[: max(top_k, len(found))]


def _round_robin(lists: list[list[SourceChunk]], cap: int) -> list[SourceChunk]:
    """Each part's best chunk first, then each part's second, ... (deduped)."""
    if len(lists) == 1:
        return lists[0][:cap]
    final: list[SourceChunk] = []
    seen: set[str] = set()
    index = 0
    while len(final) < cap and any(index < len(lst) for lst in lists):
        for lst in lists:
            if len(final) >= cap:
                break
            if index < len(lst) and lst[index].chunk_id not in seen:
                final.append(lst[index])
                seen.add(lst[index].chunk_id)
        index += 1
    return final


def covered_sources(
    result: RetrievalResult, decision: RouteDecision
) -> list[SourceChunk]:
    """The merged context restricted to the covered parts' chunks (order kept)."""
    keep = {
        s.chunk_id
        for part, verdict in zip(result.parts, decision.parts, strict=False)
        if verdict.covered
        for s in part.sources
    }
    kept = [s for s in result.sources if s.chunk_id in keep]
    return kept or result.sources


async def retrieve(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    vector_store: VectorStore,
    query: str,
    top_k: int = 5,
    filters: dict | None = None,
    history: list[ChatTurn] | None = None,
    max_documents: int | None = None,
) -> list[SourceChunk]:
    """Merged sources for a message; every part's retrieval goes to the sink."""
    result = await retrieve_parts(
        vector_store,
        query,
        top_k=top_k,
        filters=filters,
        history=history,
        max_documents=max_documents,
    )
    sink = part_retrieval_sink.get()
    if sink is not None:
        sink[:] = result.parts
    return result.sources
