# pylint: disable=too-many-lines
"""Rule-detected ambiguity: questions that cannot be answered well as asked.

Complements the short-question gate in app.harness.clarify (where the answer
model itself decides) with signals that are decided before generation, and
before the low-confidence fallback, from the retrieval already done:

- ``REFERENT``: "How much does it cost?" with no prior conversation: "it"
  points at nothing, so the question is which service is meant.
- ``COMPARISON``: "Is it better than the other one?" without two named
  subjects (in the question or in the conversation): ask what to compare.
- ``SLOTS``: a compliance/audit planning request ("we have an audit coming up,
  what should we do and how long will it take?") that names no framework and
  leaves several details open (framework, current maturity, deadline,
  environment): ask a short series of questions, at most
  ``clarify_max_questions``.

With prior turns that name a subject the referent is resolved from history
(retrieval folds the conversation in), so nothing is asked. A question that
names a service or framework (the over-asking guard), a reply to a
clarification, and the consecutive-clarification cap never ask.

Every option is grounded in the retrieved knowledge base: service/framework
options are the retrieved documents (by title), and detail options are kept
only when a retrieved chunk covers them. An option whose best retrieval score
clearly leads is tagged ``recommended`` (``clarify.recommend_option``). A borderline request (only two open
details) is confirmed by one cheap LLM call with a timeout; on timeout or any
error the question is answered as usual.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.core.circuit import get_llm_breaker
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.evaluation.quality import injection_in_chunks
from app.guardrails.conversational import (
    detect_contact_request,
    detect_conversational_intent,
)
from app.harness.clarify import (
    BRAND_RE,
    COMPANY_FACT_RE,
    GOAL_AUDIT,
    GOAL_AUDIT_QUERY,
    GOAL_HEADER,
    GOAL_OVERVIEW,
    GOAL_PRICING,
    GOAL_QUESTION,
    GOAL_SECURITY,
    INCIDENT_RE,
    NOT_SURE_LABEL,
    clarification_streak,
    content_word_count,
    humanize_source_name,
    is_generic_product_question,
    metadata_text,
    recommend_option,
)
from app.harness.prompts import get_chat_client
from app.model_settings.service import current_model_settings
from app.schemas.query import (
    ChatTurn,
    Clarification,
    ClarificationOption,
    ClarificationQuestion,
    SourceChunk,
)

logger = get_logger(__name__)

_MAX_OPTION_DOCS = 4
_DETERMINISTIC_SLOTS = 3  # this many open details need no LLM confirmation
_MIN_SLOTS = 2
_MIN_COMPARED = 2
_ANSWERED = "answered"


class AmbiguityKind(StrEnum):
    NONE = "none"
    REFERENT = "referent"
    COMPARISON = "comparison"
    SLOTS = "slots"
    GOAL = "goal"


@dataclass(frozen=True, slots=True)
class Assessment:
    """Rule result (no I/O), computed before retrieval."""

    kind: AmbiguityKind = AmbiguityKind.NONE
    missing_slots: tuple[str, ...] = ()
    cue: str = ""  # GOAL: which opener the visitor used (picks the preamble)
    deterministic: bool = False  # SLOTS: no LLM confirmation needed

    @property
    def detected(self) -> bool:
        return self.kind is not AmbiguityKind.NONE


@dataclass(frozen=True, slots=True)
class RuleClarification:
    """A clarification to return (or ``None``) plus the decision call's usage."""

    clarification: Clarification | None = None
    usage: dict[str, int] | None = None
    decision: str = "skipped"


# ---------------------------------------------------------------------------
# Knowledge-base catalogue (documents -> services / frameworks)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    label: str
    pattern: re.Pattern[str]
    description: str
    framework: bool = False
    # Breaks a recommendation tie between close options (see recommend_option).
    default: bool = False
    # A real service/framework (not the generic "platform" entry).
    offering: bool = True


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# User text is collapsed to single spaces and bounded before any pattern runs.
_MAX_SCAN_CHARS = 2000


def _scan_text(text: str) -> str:
    return " ".join(text.split())[:_MAX_SCAN_CHARS]


def _hit(pattern: re.Pattern[str], text: str) -> bool:
    return pattern.search(text) is not None


def _strip(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(" ", text)


# Matched against document names (and the question, to tell a named subject).
CATALOGUE: tuple[CatalogueEntry, ...] = (
    CatalogueEntry(
        "Essential Eight",
        _re(r"\b(essent\w{1,3}l[ _-]{0,3}(eight|eigth|eigh|8)|e8|acsc)\b"),
        "ACSC Essential Eight implementation and maturity uplift",
        framework=True,
    ),
    CatalogueEntry(
        "ISO 27001",
        _re(r"\biso(?:[ /_-]{0,3}iec)?[ _-]{0,3}27001\b|\biso\b"),
        "ISO/IEC 27001 technical controls in Microsoft 365",
        framework=True,
    ),
    CatalogueEntry(
        "CIS Benchmark",
        _re(r"\bcis\b"),
        "CIS Microsoft 365 Foundations Benchmark assessment",
        framework=True,
    ),
    CatalogueEntry(
        "Zero Trust",
        _re(r"\bzero[ _-]{0,3}tr(ust|sut|uts)\b"),
        "Zero Trust assessment and implementation",
    ),
    CatalogueEntry(
        "Security Baseline",
        _re(r"\bsecur(it|ti|t)y[ _-]{0,3}baseline\b"),
        "Microsoft 365 Security Baseline Assessment",
    ),
    CatalogueEntry(
        "DeployIQ platform",
        _re(r"\bdeploy[ _-]{0,3}iq[ _-]{0,3}platform\b|\bplatform\b"),
        "The DeployIQ orchestration platform",
        offering=False,
    ),
)


def offering_labels() -> list[str]:
    """Labels of every service/framework in the catalogue, in catalogue order."""
    return [e.label for e in CATALOGUE if e.offering]


def strip_offerings(text: str) -> str:
    """``text`` with every named service/framework removed (bounded first)."""
    text = _scan_text(text)
    for entry in CATALOGUE:
        if entry.offering:
            text = _strip(entry.pattern, text)
    return " ".join(text.split())


def _entry_for(source: SourceChunk) -> CatalogueEntry | None:
    metadata = source.metadata or {}
    name = metadata_text(metadata, "source_name", "file_name", "title") or ""
    return next((e for e in CATALOGUE if _hit(e.pattern, name)), None)


def _source_name(source: SourceChunk) -> str | None:
    return metadata_text(source.metadata or {}, "source_name", "file_name", "title")


def _option(
    index: int, label: str, description: str, sources: list[SourceChunk]
) -> ClarificationOption:
    doc_ids = list(dict.fromkeys(s.document_id for s in sources))
    names = list(dict.fromkeys(n for s in sources if (n := _source_name(s))))
    return ClarificationOption(
        id=f"O{index}",
        label=label,
        description=description,
        document_ids=doc_ids[:_MAX_OPTION_DOCS],
        sources=names[:_MAX_OPTION_DOCS],
    )


def _best_score(sources: list[SourceChunk]) -> float:
    return max((s.score for s in sources), default=0.0)


def service_options(
    sources: list[SourceChunk], *, frameworks_only: bool = False
) -> list[ClarificationOption]:
    """The retrieved documents as services/frameworks, best-scoring first.

    Falls back to the retrieved documents' titles when fewer than
    ``clarify_min_options`` catalogue services were retrieved.
    """
    settings = get_settings()
    ranked = sorted(sources, key=lambda s: s.score, reverse=True)
    grouped: dict[str, tuple[CatalogueEntry, list[SourceChunk]]] = {}
    for source in ranked:
        entry = _entry_for(source)
        if entry is None or (frameworks_only and not entry.framework):
            continue
        grouped.setdefault(entry.label, (entry, []))[1].append(source)
    if frameworks_only and len(grouped) < settings.clarify_min_options:
        return service_options(sources)

    defaults: list[bool] | None = None
    if len(grouped) >= settings.clarify_min_options:
        items = [(e.label, e.description, docs) for e, docs in grouped.values()]
        defaults = [e.default for e, _ in grouped.values()]
    else:
        by_label: dict[str, list[SourceChunk]] = {}
        for source in ranked:
            name = _source_name(source)
            if name:
                # A known catalogue page is offered by its service name, never
                # as a raw page title ("E8 service page").
                entry = _entry_for(source)
                label = entry.label if entry else humanize_source_name(name)
                by_label.setdefault(label, []).append(source)
        items = [(label, "", docs) for label, docs in by_label.items()]
    shown = items[: settings.clarify_max_options]
    options = [
        _option(i, label, description, docs)
        for i, (label, description, docs) in enumerate(shown, 1)
    ]
    return recommend_option(
        options,
        [_best_score(docs) for _, _, docs in shown],
        defaults[: len(shown)] if defaults is not None else None,
    )


# What a request implies beyond naming the framework ("certification" -> ISO).
_IMPLIED: dict[str, re.Pattern[str]] = {
    "Essential Eight": _re(r"\b(acsc|asd|maturity level|ml ?[1-3]|government)\b"),
    "ISO 27001": _re(r"\b(isms|certif\w{0,8}|27001|accredit\w{0,8})\b"),
    "CIS Benchmark": _re(r"\b(benchmarks?|hardening)\b"),
    "Zero Trust": _re(r"\b(never trust|least privilege)\b"),
    "Security Baseline": _re(r"\b(baselines?)\b"),
}


def _named_labels(query: str) -> list[str]:
    """Catalogue labels the query names or implies, in fixed catalogue order."""
    text = _scan_text(query)
    return [
        e.label
        for e in CATALOGUE
        if e.offering
        and (_hit(e.pattern, text) or _hit(_IMPLIED.get(e.label, e.pattern), text))
    ]


def framework_options(
    sources: list[SourceChunk], query: str = ""
) -> list[ClarificationOption]:
    """Catalogue services/frameworks in a fixed order, then "Not sure".

    Deterministic (never retrieval-score order): whatever the query names or
    implies comes first, then the catalogue's priority order (Essential Eight,
    ISO 27001, CIS Benchmark, Zero Trust, Security Baseline). Retrieved
    documents are attached to their option. The card shows at most
    ``clarify_max_options`` options, the last being "Not sure, help me choose".
    """
    settings = get_settings()
    ranked = sorted(sources, key=lambda s: s.score, reverse=True)
    grouped: dict[str, list[SourceChunk]] = {}
    for source in ranked:
        entry = _entry_for(source)
        if entry is not None and entry.offering:
            grouped.setdefault(entry.label, []).append(source)
    entries = {e.label: e for e in CATALOGUE if e.offering}
    named = _named_labels(query)
    order = [*named, *(label for label in entries if label not in named)]
    shown = order[: max(settings.clarify_max_options - 1, 1)]
    built = [
        _option(i, label, entries[label].description, grouped.get(label, []))
        for i, label in enumerate(shown, 1)
    ]
    base = recommend_option(
        built,
        [_best_score(grouped.get(label, [])) for label in shown],
        [entries[label].default for label in shown],
    )
    base = base[: max(settings.clarify_max_options - 1, 1)]
    not_sure = ClarificationOption(
        id=f"O{len(base) + 1}",
        label=NOT_SURE_LABEL,
        description="Get a recommendation based on your goal and where you are today",
    )
    return [*base, not_sure]


# ---------------------------------------------------------------------------
# Detail slots of an underspecified compliance request
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SlotOption:
    label: str
    description: str
    evidence: re.Pattern[str] | None = None  # None: the slot's evidence


@dataclass(frozen=True, slots=True)
class SlotSpec:  # pylint: disable=too-many-instance-attributes
    id: str
    header: str
    question: str
    filled: re.Pattern[str]
    evidence: re.Pattern[str]
    options: tuple[SlotOption, ...]
    needed: re.Pattern[str] | None = None  # None: always needed
    multi_select: bool = False


FRAMEWORK_SLOT = "framework"

_PLANNING_RE = _re(
    r"\b(what should (we|i) do|what do (we|i) need( to do)?|where (do|should) (we|i) "
    r"start|how (do|can|should) (we|i) (get|become|achieve|prepare|pass)|"
    r"(get|become|be) (compliant|complaint)|pass (the|an|our) audit|"
    r"prepare for (the |an |our )?(audit|audti)|"
    r"how long (will|would|does) it take|roadmap|plan of action)\b"
)
# "complience" / "complaince" / "audti" typos are tolerated (bounded literals).
_COMPLIANCE_RE = _re(
    r"\b(audit\w*|audti\w*|complian\w*|complience\w*|complaince\w*|certif\w*|"
    r"accredit\w*|maturity|framework|uplift|regulat\w*|assessment|"
    r"(get|become|be) complaint)\b"
)
# Vague goals with no framework named ("I need compliance", "we want to get
# certified", "how do I secure my company", "help us get ready for our audit").
_GOAL_RE = _re(
    r"\b((we|i) (need|want|have|must)( to)? (get |be |become |achieve |meet )?"
    r"(compl(?:ian|ien|ain)\w{1,3}|complaint|certified|certification|accredited)|"
    r"(improve|strengthen|lift|uplift|harden) (our|my|the) (security|cyber|posture)|"
    r"how (do|can|should) (i|we) (secure|protect|harden) (my|our|the) "
    r"(company|business|organi[sz]ation|tenant|environment|data)|"
    r"(we|i)( have| 've| got)? an? (upcoming |external |internal )?(audit|audti)( coming up| soon| next \w{1,10})?|"
    r"help (us|me) (to )?(get ready|prepare|be ready)|"
    r"(get|be) ready for (the |an? |our |my )?(audit|audti)|"
    r"(get|be|become|getting|being) audit[ -]{0,3}ready)\b"
)
_AUDIT_RE = _re(r"\b(audit|audti)\w*\b")

SLOTS: tuple[SlotSpec, ...] = (
    SlotSpec(
        id="maturity",
        header="Maturity",
        question="Where are your controls at today?",
        filled=_re(
            r"\b(maturity level|ml ?[0-3]|level [0-3]|from scratch|greenfield|"
            r"already|partly|partially|mostly|in place|currently at)\b"
        ),
        evidence=_re(r"maturity|current state|assess|baseline|gap"),
        options=(
            SlotOption(
                "Not assessed yet",
                "Start with an assessment of your current state and gaps",
                _re(r"assess|current state|gap"),
            ),
            SlotOption(
                "Partly implemented",
                "Some controls in place; prioritise and uplift the gaps",
                _re(r"uplift|priorit|phased|gap"),
            ),
            SlotOption(
                "Mostly in place",
                "Controls largely in place; validate and evidence them",
                _re(r"evidence|audit[ -]{0,3}ready|validat|verif"),
            ),
        ),
    ),
    SlotSpec(
        id="deadline",
        header="Deadline",
        question="Do you have a deadline in mind?",
        needed=_re(
            r"\b(fast|quick(ly)?|asap|urgent(ly)?|deadline|coming up|soon|upcoming|"
            r"tight|rush)\b"
        ),
        filled=_re(
            r"\b\d{1,4} ?(day|week|month|year)s?\b|\b(next|this) (week|month|quarter)\b|"
            r"\bq[1-4]\b|\bend of\b|\b(by|before|in) (jan|feb|mar|apr|may|jun|jul|aug|"
            r"sep|oct|nov|dec)\w*\b"
        ),
        evidence=_re(r"\bdays\b|\bweeks\b|\bmonths\b|timeline|phased|rapid"),
        options=(
            SlotOption("Within a month", "A tight timeline; focus on priority gaps"),
            SlotOption("In 1-3 months", "Time for a phased uplift"),
            SlotOption("More than 3 months", "Room for a full, staged programme"),
        ),
    ),
    SlotSpec(
        id="environment",
        header="Environment",
        question="Which environment are you working in?",
        filled=_re(
            r"\b(microsoft 365|m365|office 365|o365|azure|entra|intune|aws|gcp|google|"
            r"on[ -]?prem\w*|hybrid|windows|tenant)\b"
        ),
        evidence=_re(r"microsoft 365|entra|intune|hybrid|on[ -]?prem"),
        options=(
            SlotOption(
                "Microsoft 365",
                "Your Microsoft 365 tenant and its configuration",
                _re(r"microsoft 365|\bm365\b"),
            ),
            SlotOption(
                "Entra ID and Intune",
                "Identity and device management",
                _re(r"\bentra\b|\bintune\b"),
            ),
            SlotOption(
                "Hybrid or on-premises",
                "Cloud plus on-premises systems",
                _re(r"hybrid|on[ -]?prem"),
            ),
        ),
        multi_select=True,
    ),
)
_SLOTS_BY_ID = {s.id: s for s in SLOTS}


def _missing_slots(query: str) -> tuple[str, ...]:
    """Open details of a compliance planning request; () when it is not one."""
    query = _scan_text(query)
    planning = _PLANNING_RE.search(query) and _COMPLIANCE_RE.search(query)
    if not (planning or _GOAL_RE.search(query)):
        return ()
    # A named platform (Microsoft 365, "the platform") is not a named framework.
    if any(e.offering and _hit(e.pattern, query) for e in CATALOGUE):
        return ()
    missing = [FRAMEWORK_SLOT]
    for slot in SLOTS:
        needed = slot.needed is None or _hit(slot.needed, query)
        if needed and not _hit(slot.filled, query):
            missing.append(slot.id)
    return tuple(missing) if len(missing) >= _MIN_SLOTS else ()


# ---------------------------------------------------------------------------
# Referents
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'&+-]*")
_REFERENTS = frozenset({"it", "its", "this", "that", "these", "those", "they", "them"})
# Words that carry no subject: "how much does IT cost" has nothing before "it".
_SCAFFOLD = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "but",
        "can",
        "could",
        "did",
        "do",
        "does",
        "how",
        "is",
        "long",
        "many",
        "much",
        "ok",
        "okay",
        "so",
        "the",
        "then",
        "was",
        "were",
        "what",
        "whats",
        "what's",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "would",
        "should",
        "also",
    }
)
# "Is it possible to ...": a dummy "it", not a reference.
_EXPLETIVE_RE = _re(
    r"\bit (?:(?:is|was|be) )?(possible|necessary|true|ok|okay|worth|required|"
    r"mandatory|important|hard|easy|difficult)\b"
)
_COMPARISON_RE = _re(
    r"\b(the other( one| option| service| framework| product)?|that one|this one|"
    r"than (it|that|this|them|those)|compared? (to|with) (it|that|this|them)|"
    r"which one|both of them)\b"
)
# "The one you mentioned above", "the previous one", "the second one", "that
# product", "earlier": a pointer back at the previous answer (literals only).
_BACKREF_RE = _re(
    r"\b(the one (you )?(mentioned|above|before|earlier)|"
    r"the (previous|last|former|latter|first|second|third) "
    r"(one|option|service|product|framework)|"
    r"that (product|service|framework|option)|"
    r"the (product|service|framework|option) (above|you mentioned)|"
    r"(mentioned|said|discussed) (above|earlier|before)|earlier|above)\b"
)
# "that one" / "this one" point back too, unless the question compares things.
_POINTER_RE = _re(r"\b(that|this) one\b")
_COMPARING_RE = _re(r"\b(than|compar\w*|versus|vs|better|worse|differen\w*)\b")
# A bare "tell me more": the whole message asks for more on the previous answer.
_MORE_RE = _re(
    r"^(?:(?:ok|okay|so|and|please) )?(?:(?:can|could) you )?"
    r"(?:tell me more|more (?:details?|info\w{0,8})|elaborate|go on|"
    r"expand on (?:that|it)|say more)\W{0,5}$"
)
# Capitalised word of 2+ characters (acronyms, product names, Titlecase).
_PROPER_RE = re.compile(r"\b[A-Z][A-Za-z0-9]+\b")


_COMMON = (
    _SCAFFOLD
    | _REFERENTS
    | {
        "i",
        "we",
        "our",
        "you",
        "your",
        "my",
        "hi",
        "hello",
        "please",
        "he",
        "she",
        "his",
        "her",
        "there",
        "here",
        "yes",
        "no",
        "sure",
        "thanks",
        "thank",
        "cheers",
        "great",
        "good",
        "if",
        "or",
        "not",
        "for",
        "to",
        "of",
        "in",
        "on",
        "with",
        "any",
        "some",
        "all",
        "hey",
    }
)


def _brand_subjects(text: str) -> tuple[set[str], str]:
    """Brand names found (case-insensitive) and ``text`` with them removed."""
    found = {
        "exigo" if m.group(0).lower().startswith("exigo") else "deployiq"
        for m in BRAND_RE.finditer(text)
    }
    return found, BRAND_RE.sub(" ", text)


def _subjects(text: str, *, brand: bool = True) -> set[str]:
    """Named subjects: catalogue services/frameworks, brands and proper nouns.

    Catalogue and brand terms match case-insensitively ("exigo", "e8", "iso").
    ``brand=False`` leaves brand names to the proper-noun rule (answer shape).
    """
    text = _scan_text(text)
    found: set[str] = set()
    for entry in CATALOGUE:
        if _hit(entry.pattern, text):
            found.add(entry.label)
            text = _strip(entry.pattern, text)
    if brand:
        brands, text = _brand_subjects(text)
        found |= brands
    # Proper nouns after the first word (sentence case is not a name).
    rest = " ".join(text.split()[1:])
    if rest.isupper():  # SHOUTING is not a list of names
        rest = rest.lower()
    found |= {m.lower() for m in _PROPER_RE.findall(rest) if m.lower() not in _COMMON}
    return found


def _catalogue_subjects(text: str) -> set[str]:
    """Services/frameworks and brand names only (no capitalised-word guesses)."""
    text = _scan_text(text)
    found = {e.label for e in CATALOGUE if e.offering and _hit(e.pattern, text)}
    return found | _brand_subjects(text)[0]


def _history_subjects(history: list[ChatTurn] | None) -> set[str]:
    """Subjects of the visitor's turns and of real answers (not fallbacks).

    Only catalogue services and brand names count: capitalised sentence-initial
    words ("It", "Thanks") are not subjects, and greetings name nothing.
    """
    found: set[str] = set()
    greeted = False  # the canned reply to a greeting lists topics, not subjects
    for turn in history or []:
        if turn.role == "user":
            greeted = (
                detect_conversational_intent(turn.content[:_MAX_SCAN_CHARS]) is not None
            )
        if greeted or (turn.role != "user" and turn.outcome != _ANSWERED):
            continue
        found |= _catalogue_subjects(turn.content)
    return found


def _last_answer_subject_count(history: list[ChatTurn] | None) -> int:
    """Distinct catalogue services in the last assistant answer (0 if none)."""
    for turn in reversed(history or []):
        if turn.role != "assistant":
            continue
        if turn.outcome not in (None, _ANSWERED):
            return 0
        text = _scan_text(turn.content)
        return sum(1 for e in CATALOGUE if e.offering and _hit(e.pattern, text))
    return 0


def _dangling_pronoun(query: str) -> bool:
    query = _scan_text(query)
    if _EXPLETIVE_RE.search(query):
        return False
    for word in (w.lower() for w in _WORD_RE.findall(query)):
        if word in _REFERENTS:
            return True
        if word not in _SCAFFOLD:
            return False
    return False


# A cost/time question that names nothing at all ("pricing?", "how much?").
_COST_TIME_RE = _re(
    r"\b(cost\w*|price\w*|pricing|fees?|quotes?|charge\w*|pay|expensive|cheap|"
    r"plans?|packages?|tiers?|subscriptions?|licen[cs]\w*|free|trial|"
    r"how long|how much|timeline|how (quickly|fast)|duration)\b"
)
_COST_TIME_WORDS = frozenset(
    {
        "cost",
        "costs",
        "price",
        "prices",
        "pricing",
        "fee",
        "fees",
        "quote",
        "quotes",
        "charge",
        "charges",
        "pay",
        "take",
        "takes",
        "timeline",
        "duration",
        "plan",
        "plans",
        "package",
        "packages",
        "tier",
        "tiers",
        "subscription",
        "subscriptions",
        "free",
        "trial",
        "expensive",
        "cheap",
        "quickly",
        "fast",
        "you",
        "your",
        "there",
        "any",
        "per",
        "for",
        "to",
    }
)


def _bare_cost_or_time(query: str) -> bool:
    """Only cost/time words and scaffolding: nothing says what it is about."""
    scan = _scan_text(query)
    if not _COST_TIME_RE.search(scan):
        return False
    words = (w.lower() for w in _WORD_RE.findall(scan))
    return all(w in _SCAFFOLD or w in _COST_TIME_WORDS for w in words)


# ---------------------------------------------------------------------------
# GOAL: a genuinely vague help / need / situation statement
# ---------------------------------------------------------------------------
# "I need help", "idk", "we are a small business": nothing names a service, so
# retrieval has nothing to go on. Decided by rules before retrieval; the card
# asks what they want to get done (see goal_clarification). Every pattern is
# anchored to the whole (short) message, built from literals and bounded
# repeats only, over input capped at _MAX_GOAL_CHARS.

_MAX_GOAL_CHARS = 300
_STALLED_OUTCOMES = frozenset({"blocked", "ungrounded"})
_STALLED_TURNS = 2
_MIN_GOAL_OPTIONS = 2

GENERIC_PREAMBLE = "Happy to help. Quick question first so I get this right."
CUE_DEFAULT = "default"
CUE_LOST = "lost"
CUE_WORRIED = "worried"
CUE_SMALL = "small"
GOAL_PREAMBLES: dict[str, str] = {
    CUE_LOST: "No worries, plenty of people aren't sure where to start.",
    CUE_WORRIED: "Sounds like something's on your mind.",
    CUE_SMALL: "Happy to help a small business get this right.",
    CUE_DEFAULT: "Happy to point you in the right direction.",
}

_LEAD_RE = _re(r"^(?:(?:hi|hello|hey|ok|okay|so|um|well|please|sorry)[ ,]{1,3}){1,2}")
_END_RE = _re(
    r"^(?: (?:please|pls|thanks|here|today|now|really|lol|honestly|with this|"
    r"with something|on this|from you|from someone|a bit|a lot))?[\s\W]{0,6}$"
)
_I_WE = r"(?:i|we)(?: really| just)?"
_NEED = r"(?:need|want|have|require|would like|'d like)"
_IM = r"(?:i am|i'm|im|we are|we're|were)"
_PROBLEM = r"(?:problems|issues|trouble|errors)"
_SMALL_ORG = (
    r"(?:business|company|firm|startup|organi[sz]ation|team|practice|shop|clinic|"
    r"charity|school|council|agency)"
)
_GOAL_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        CUE_LOST,
        _re(
            rf"^(?:{_IM}(?: a bit| very| so| really| totally)? "
            r"(?:confused|lost|stuck|overwhelmed|unsure|not sure)|confused|idk|dunno|"
            r"no (?:idea|clue)|(?:i |we )?(?:don'?t|do not) know|(?:i |we )?not sure|"
            r"unsure)"
            r"(?: (?:what|where|how|which) (?:to (?:start|begin|do|ask|choose|pick|"
            r"buy|get|go)|i need|we need|i want|we want|one|service|option))?"
        ),
    ),
    (
        CUE_LOST,
        _re(
            r"^(?:where (?:do|should|can|to) (?:(?:i|we) )?(?:start|begin)|"
            r"how (?:do|can|should) (?:i|we) (?:get started|start|begin)|"
            r"how to (?:get started|start|begin))"
        ),
    ),
    (
        CUE_WORRIED,
        _re(
            rf"^(?:{_IM}(?: a bit| very| so| really)? (?:worried|concerned|nervous|"
            r"anxious)(?: about (?:hackers|security|cyber ?(?:security|attacks?)?|"
            r"breaches|ransomware|data|our data|my data|it|this|things|stuff))?|"
            r"worried about (?:hackers|security|cyber ?(?:security|attacks?)?|"
            r"breaches|ransomware|data|our data)|"
            r"(?:our|my) (?:tenant|microsoft 365|m365|office 365|email|network|it|"
            r"security|systems?|setup|environment) (?:has|have|is having|keeps having) "
            rf"(?:some |a few |lots of |many |a lot of )?{_PROBLEM}|"
            rf"(?:i|we) (?:have|am having|are having|'ve got|got) (?:some |a few )?"
            rf"{_PROBLEM})"
        ),
    ),
    (
        CUE_SMALL,
        _re(
            rf"^(?:(?:{_IM}|we run|i run|we own|i own|we have|i have) (?:just )?"
            r"(?:a |an |the )?(?:(?:very |really )?(?:small|medium|tiny|mid-?sized|"
            r"growing|new|local|family|startup)[ -]?)(?:\w{1,15} )?"
            rf"{_SMALL_ORG}|{_IM} (?:a |an )?(?:startup|start-up))"
        ),
    ),
    (
        CUE_DEFAULT,
        _re(
            rf"^(?:(?:{_I_WE} )?{_NEED}(?: to get)? (?:some |a little |a bit of |your |any )?"
            r"(?:help|advice|assistance|a hand|guidance|support|something|ideas|"
            r"some ideas|a recommendation|a suggestion)"
            rf"|{_I_WE} {_NEED} to (?:know|look into|improve|learn|find out|"
            r"understand|get better|do better|get started)(?: (?:more|something|"
            r"stuff|things|a bit|some things|about (?:stuff|something|things|it|"
            r"this|you|everything)))?"
            r"|(?:i have|i've got|we have) (?:a |one |some )?(?:question|questions)"
            r"|(?:can|could) (?:i|we) ask (?:you )?(?:a |some )?(?:question|something)"
            r"|what should (?:we|i) (?:do|buy|get|choose|pick|use)(?: first| next)?"
            r"|what(?:'s|s| is) (?:the )?best(?: (?:option|choice|one|thing|for (?:me|us)))?"
            r"|(?:what|which) (?:are|is) (?:my|our|the|your) (?:options?|choices?)"
            r"|(?:what|which) (?:do|would) you recommend"
            r"|(?:i|we) (?:need|want) (?:a )?recommendations?"
            r"|(?:can|could) you recommend (?:me |us )?(?:something|anything)?"
            r"|recommend(?: me| us)?(?: something| anything)?"
            r"|(?:compare|show me|list|give me) (?:all )?(?:your|the) "
            r"(?:options|choices|services|offerings|plans)"
            r"|compare (?:your )?options"
            r"|tell me something(?: interesting| good| useful)?"
            r"|(?:tell me|i want to know|i need to know|give me) something "
            r"about (?:stuff|things|it|this|you|your (?:company|business|services|"
            r"stuff)|the company|security|cyber ?security|compliance|cloud|"
            r"microsoft|it)"
            r"|something about (?:stuff|things|security|cyber ?security|compliance|"
            r"cloud|microsoft)"
            r"|(?:my boss|my manager|my ceo|the boss) (?:asked|told|wants|has asked) "
            r"(?:me )?(?:to )?(?:look into|check|investigate|find out about|sort out)"
            r"(?: this| it| something| security| things)?"
            r"|help(?: me| us)? out"
            r"|(?:i|we) (?:am |are |'m |'re )?(?:looking for|after) (?:some )?"
            r"(?:help|advice|guidance|something|a recommendation))"
        ),
    ),
)


def _goal_cue(scan: str) -> str | None:
    """The cue of a vague help/need/situation statement, or ``None``."""
    text = _LEAD_RE.sub("", scan.lower().strip(), count=1)
    for cue, pattern in _GOAL_CUES:
        match = pattern.match(text)
        if match is not None and _END_RE.match(text[match.end() :]):
            return cue
    return None


# --- Run-ons: several vague cues in one message -----------------------------
# "i need help, tell me something, i am confused, idk, what should we buy" is
# not one anchored cue but a run of them. The prompt is split into clauses
# (bounded: input capped, clause count capped, one linear pass each); it is
# vague when at least one clause is a vague cue and none is concrete.

_MAX_CLAUSES = 24
_CLAUSE_SPLIT_RE = re.compile(
    r"[,;.?!:/|()\n]+|[^\w\s,;.?!:/|()'’&+-]+|"  # noqa: RUF001
    r"\s(?:and|but|also|plus|then|or)\s|\s[-–—]{1,3}\s"  # noqa: RUF001
)
_NOT_WORD_RE = re.compile(r"[^a-z0-9' -]+")
_REPEAT_RE = re.compile(r"(.)\1{2,}")
_FILLER_LEAD = (
    r"(?:ok|okay|so|um+|uh+|er|erm|hm+|well|please|pls|hi|hello|hey|yeah|yes|yep|"
    r"like|just|lol|haha|oh|and|but|also|then|actually|basically|sorry|anyway|"
    r"anyways|ugh|right|alright)"
)
_LEAD_FILLER_RE = re.compile(rf"^(?:{_FILLER_LEAD}(?: |$)){{1,4}}")
_TRAIL_FILLER_RE = re.compile(
    r"(?: (?:please|pls|lol|haha|thanks|thx|now|today|again|though|really|honestly|"
    r"mate|ok|okay|hm+|then|guys|team|so|um+|uh+|er|erm|oh|well|ugh)){1,4}$"
)
# Filler tokens that are themselves hesitation (a bare "hmm" / "ok so" is vague).
_HESITATION_RE = re.compile(r"so|um+|uh+|er|erm|hm+|please|pls|oh|well")
_VAGUE_TAIL = (
    r"(?:(?: (?:with|about|on|for|of|at|in|regarding))? (?:all |all of |any )?"
    r"(?:this|that|it|these|those|everything|anything|something|stuff|things?|"
    r"you|here|your (?:company|business|services|stuff)))?"
)
_VAGUE_CORE = (
    r"help(?: (?:me|us))?(?: out)?"
    r"|(?:can|could|will|would) you (?:please )?help(?: (?:me|us))?"
    r"|(?:(?:i|we) )?(?:really |just |also )?(?:need|want|require)"
    r"(?: (?:some|a little|a bit of|your))? (?:help|advice|assistance|guidance|"
    r"support|ideas?|suggestions?|recommendations?|a hand|a pointer|tips)"
    r"|(?:tell|show|give) (?:me|us) (?:something|anything|stuff|more|everything|all)"
    r"(?: (?:interesting|good|useful))?"
    r"|(?:any|some) (?:suggestions?|ideas?|thoughts?|advice|tips?|"
    r"recommendations?|pointers?)"
    r"|(?:suggestions?|ideas?|thoughts?|advice|tips|pointers?|anything|something|"
    r"whatever|everything|stuff|things?|hm+|um+|uh+)"
    r"|what do you (?:recommend|suggest|think)"
    r"|what(?:'s| is| are|s) (?:the )?best(?: (?:option|choice|one|thing|for (?:me|us)))?"
    r"|what(?:'s| is| are|s) (?:good|new)"
    r"|(?:i |we )?(?:dunno|idk|don'?t know|do not know|no idea|no clue|clueless)"
    r"|(?:(?:i|we) )?(?:am |are |'m |'re |im )?(?:so |very |a bit |really |totally )?"
    r"(?:not sure|unsure|confused|lost|stuck|overwhelmed)"
    r"|(?:where|how) (?:do|should|can|to) (?:(?:i|we) )?(?:start|begin|get started)"
    r"|how to (?:get started|start|begin)"
    r"|what (?:should|do|can|could|would|shall) (?:i|we) (?:do|try|look at|go for)"
    r"(?: first| next)?"
    r"|(?:i|we) (?:have|got|had) (?:a |one |an? |some )?(?:few |several |many |"
    r"lots of )?(?:questions?|queries|something to ask)"
    r"|(?:can|could|may) (?:i|we) ask (?:you )?(?:a |some )?(?:questions?|something)"
    r"|(?:my|our|the) (?:boss|manager|ceo|director|cto|team|it guy|client) "
    r"(?:told|asked|wants|said|has asked)(?: me| us)?(?: to)? "
    r"(?:look (?:into|at)|check(?: out)?|investigate|find out about|sort out|"
    r"figure out|do)"
    r"|(?:i|we) (?:am |are |'m |'re )?(?:looking for|after) (?:some )?"
    r"(?:help|advice|guidance|something|anything|ideas|suggestions)"
    r"|(?:i|we) (?:need|want) to (?:know|learn|understand|find out|look into)"
    r"(?: (?:something|stuff|things|more|a bit))?"
    r"|(?:i|we) (?:am |are |'m |'re )?(?:new|just looking|just browsing)"
    r"|(?:just )?(?:looking|browsing)(?: around)?"
    r"|recommend(?:ations?)?(?: (?:me|us))?(?: something| anything)?"
    r"|(?:i|we) (?:need|want) (?:a )?recommendations?"
    r"|(?:what|which) (?:are|is) (?:my|our|the|your) (?:options?|choices?)"
    r"|(?:i|we) (?:am |are |'m |'re )?(?:worried|concerned|nervous|anxious)"
    r"|(?:i|we) (?:need|want) (?:something|anything)"
    r"|how (?:does|do|would) (?:this|that|it) (?:all )?work"
)
_VAGUE_CLAUSE_RE = re.compile(rf"(?:{_VAGUE_CORE}){_VAGUE_TAIL}")
# "what should we buy (from google)": a buy/choose cue with a short tail. The
# tail names another vendor or product at most; we cannot advise on those.
_BUY_CORE = (
    r"(?:what|which) (?:should|do|can|could|would|shall) (?:i|we) "
    r"(?:buy|get|choose|pick|use|purchase|go with)"
    r"|(?:what|which) (?:to|should i|should we) (?:buy|get|choose|pick|purchase)"
    r"|(?:i|we) (?:need|want|would like) to (?:buy|get|choose|purchase)"
)
_BUY_CLAUSE_RE = re.compile(
    rf"(?:{_BUY_CORE})(?: first| next)?(?: (?:from|at|on|for|of|by|in|with|to|about))?"
    r"(?: [a-z0-9'&+-]{1,20}){0,3}"
)
_BUY_PREFIX_RE = re.compile(rf"(?:{_BUY_CORE})(?: first| next)?")
_MAX_TOLERATED_WORDS = 3


def _norm_clause(raw: str) -> str:
    text = _NOT_WORD_RE.sub(" ", raw.replace("’", "'").lower())  # noqa: RUF001
    text = " ".join(_REPEAT_RE.sub(r"\1", text).split())
    return text.strip("-' ")


def _strip_filler(clause: str) -> str:
    lead = _LEAD_FILLER_RE.match(clause)
    rest = clause[lead.end() :] if lead else clause
    return _TRAIL_FILLER_RE.sub("", rest).strip()


def _clause_kind(clause: str) -> str:
    """``vague`` / ``buy`` / ``concrete`` / ``filler`` for one normalised clause."""
    rest = _strip_filler(clause)
    if not rest:
        hesitant = any(_HESITATION_RE.fullmatch(t) for t in clause.split())
        return "vague" if hesitant else "filler"
    if _BUY_CLAUSE_RE.fullmatch(rest):
        return "buy"
    if _VAGUE_CLAUSE_RE.fullmatch(rest) or _goal_cue(rest) is not None:
        return "vague"
    words = (w for w in _WORD_RE.findall(rest) if len(w) > 1)
    return "concrete" if any(w not in _NON_CONTENT for w in words) else "filler"


def vague_runon_cue(query: str) -> str | None:
    """The preamble cue when ``query`` is only vague cues (and filler), else ``None``.

    Vague = at least one vague-cue clause and no concrete clause. A buy/choose
    cue tolerates a short tail or clause (another vendor's product, "from
    google"): we do not advise on those, so the card steers to what we do.
    """
    text = _scan_text(re.sub(r"[\r\n]+", ", ", query[:_MAX_GOAL_CHARS])).lower()
    clauses = [c for c in (_norm_clause(p) for p in _CLAUSE_SPLIT_RE.split(text)) if c]
    if not clauses or len(clauses) > _MAX_CLAUSES:
        return None
    kinds = [_clause_kind(c) for c in clauses]
    buying = "buy" in kinds
    third_party = False
    for clause, kind in zip(clauses, kinds, strict=True):
        if kind == "concrete":
            if not buying or len(clause.split()) > _MAX_TOLERATED_WORDS:
                return None
            third_party = True
        elif kind == "buy" and not _BUY_PREFIX_RE.fullmatch(_strip_filler(clause)):
            third_party = True
    if not (buying or "vague" in kinds):
        return None
    if third_party:
        return CUE_DEFAULT
    first = next(
        _strip_filler(c) for c, k in zip(clauses, kinds, strict=True) if k != "filler"
    )
    return _goal_cue(first) or CUE_DEFAULT


def _stalled(history: list[ChatTurn] | None) -> bool:
    """Two refusals / dead ends in a row (the latest assistant turns)."""
    count = 0
    for turn in reversed(history or []):
        if turn.role != "assistant":
            continue
        if turn.outcome not in _STALLED_OUTCOMES:
            break
        count += 1
    return count >= _STALLED_TURNS


def _goal_assessment(
    query: str, history: list[ChatTurn] | None, raw: str = ""
) -> Assessment | None:
    """A vague, subject-less help statement (or a third dead end): ask the goal."""
    if len(query) > _MAX_GOAL_CHARS:
        return None
    if (
        detect_conversational_intent(query) is not None
        or detect_contact_request(query) is not None
        or INCIDENT_RE.search(query)
        or is_generic_product_question(query)
        or _history_subjects(history)
    ):
        return None
    named = bool(_subjects(query))
    cue = None if named else _goal_cue(query)
    if cue is None and not _catalogue_subjects(query):
        # Run-ons of vague cues; a lone third-party name ("from Google") is not
        # a subject we cover, so it does not stop the card.
        cue = vague_runon_cue(raw or query)
    if cue is None:
        if named or COMPANY_FACT_RE.search(query) or not _stalled(history):
            return None
        cue = CUE_DEFAULT
    return Assessment(AmbiguityKind.GOAL, cue=cue)


def _last_was_goal_card(history: list[ChatTurn] | None) -> bool:
    for turn in reversed(history or []):
        if turn.role != "assistant":
            continue
        card = turn.clarification if turn.outcome == "clarify" else None
        return card is not None and any(q.header == GOAL_HEADER for q in card.questions)
    return False


def assess(
    query: str,
    history: list[ChatTurn] | None,
    *,
    clarification_reply: bool = False,
) -> Assessment:
    """Rule gate (no I/O): does this question need asking back?"""
    raw, query = query[:_MAX_GOAL_CHARS], _scan_text(query)
    settings = get_settings()
    if (
        not settings.clarify_enabled
        or clarification_streak(history) >= settings.clarify_max_consecutive
    ):
        return Assessment()
    if clarification_reply:
        # "Get audit ready" is the one Goal pick that chains into the
        # framework / maturity questions (two cards in a row is the cap).
        if query.startswith(GOAL_AUDIT_QUERY) and _last_was_goal_card(history):
            return Assessment(
                AmbiguityKind.SLOTS, (FRAMEWORK_SLOT, "maturity"), deterministic=True
            )
        return Assessment()
    missing = _missing_slots(query)
    if missing:
        return Assessment(AmbiguityKind.SLOTS, missing)
    goal = _goal_assessment(query, history, raw)
    if goal is not None:
        return goal
    # Company facts ("where are they located?") and generic one-product
    # questions ("how does it work?") are answered, not asked back.
    if COMPANY_FACT_RE.search(query) or is_generic_product_question(query):
        return Assessment()
    return Assessment(_reference_kind(query, history))


def _reference_kind(query: str, history: list[ChatTurn] | None) -> AmbiguityKind:
    """Referent / comparison ambiguity of a question that names no framework."""
    named = _subjects(query)
    pointer = _hit(_POINTER_RE, query) and not _hit(_COMPARING_RE, query)
    if (_BACKREF_RE.search(query) or pointer or _MORE_RE.match(query)) and not named:
        # "the one you mentioned above": resolved only when the last answer
        # named exactly one service; otherwise ask which one is meant.
        one = _last_answer_subject_count(history) == 1
        return AmbiguityKind.NONE if one else AmbiguityKind.REFERENT
    known = named | _history_subjects(history)
    if _COMPARISON_RE.search(query) and len(known) < _MIN_COMPARED:
        return AmbiguityKind.COMPARISON
    if (_dangling_pronoun(query) or _bare_cost_or_time(query)) and not known:
        return AmbiguityKind.REFERENT
    return AmbiguityKind.NONE


# ---------------------------------------------------------------------------
# Building the questions
# ---------------------------------------------------------------------------

_COST_RE = _re(
    r"\b(cost\w*|price\w*|pricing|fees?|quote|charge\w*|pay|expensive|cheap)\b"
)
_TIME_RE = _re(r"\b(how long|timeline|how (quickly|fast)|duration)\b")


def _service_question(
    query: str, kind: AmbiguityKind, options: list[ClarificationOption]
) -> tuple[str, ClarificationQuestion]:
    brand = get_settings().brand_name
    if kind is AmbiguityKind.COMPARISON:
        return (
            f"Happy to compare. {brand} covers several services and frameworks.",
            ClarificationQuestion(
                id="Q1",
                header="Compare",
                question="Which services or frameworks would you like to compare?",
                multi_select=True,
                options=options,
            ),
        )
    scan = _scan_text(query)
    if _COST_RE.search(scan) and _TIME_RE.search(scan):
        # Cost AND time in one message: one combined, multi-select question.
        return (
            GENERIC_PREAMBLE,
            ClarificationQuestion(
                id="Q1",
                header="Service",
                question="Which services do you want pricing and timelines for?",
                multi_select=True,
                options=options,
            ),
        )
    if _COST_RE.search(scan):
        text = "Which service do you want pricing for?"
    elif _TIME_RE.search(scan):
        text = "Which service do you want the timeline for?"
    else:
        text = "Which service are you after?"
    return (
        GENERIC_PREAMBLE,
        ClarificationQuestion(
            id="Q1", header="Service", question=text, options=options
        ),
    )


def service_clarification(
    query: str,
    sources: list[SourceChunk],
    kind: AmbiguityKind = AmbiguityKind.REFERENT,
) -> Clarification | None:
    """One "which service?" question over the retrieved services, or ``None``."""
    options = service_options(sources)
    if len(options) < get_settings().clarify_min_options:
        return None
    preamble, question = _service_question(query, kind, options)
    return Clarification(preamble=preamble, questions=[question], original_query=query)


_GOAL_OPTIONS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        GOAL_OVERVIEW,
        "A plain-English overview of the services",
        _re(r"deploy[ _-]?iq|exigo|services?|platform|methodology|microsoft 365"),
    ),
    (
        GOAL_AUDIT,
        "Essential Eight, ISO 27001 and CIS readiness and evidence",
        _re(
            r"audit|evidence|essential eight|iso[ /_-]{0,3}(iec )?27001|\bcis\b|"
            r"compliance|certif"
        ),
    ),
    (
        GOAL_SECURITY,
        "Microsoft 365 tenant assessment, Zero Trust and a security baseline",
        _re(r"tenant|baseline|zero[ _-]?trust|harden|assessment|security posture"),
    ),
    (
        GOAL_PRICING,
        "Costs, timelines and a specialist to talk to",
        _re(
            r"pric(e|ing)|\bcosts?\b|quote|timelines?|\bdays\b|\bweeks\b|"
            r"\bmonths\b|specialist|engagement|contact"
        ),
    ),
)


def goal_clarification(
    query: str, sources: list[SourceChunk], cue: str = CUE_DEFAULT
) -> Clarification | None:
    """The "what are you hoping to get done?" card, or ``None``.

    Options are fixed and kept only when a retrieved chunk covers them (its
    documents are attached so a pick boosts them); at least two must remain.
    """
    ranked = sorted(sources, key=lambda s: s.score, reverse=True)
    options: list[ClarificationOption] = []
    for label, description, evidence in _GOAL_OPTIONS:
        covering = [
            s for s in ranked if _hit(evidence, f"{s.content} {_source_name(s) or ''}")
        ]
        if covering:
            options.append(_option(len(options) + 1, label, description, covering))
    if len(options) < max(get_settings().clarify_min_options, _MIN_GOAL_OPTIONS):
        return None
    return Clarification(
        preamble=GOAL_PREAMBLES.get(cue, GOAL_PREAMBLES[CUE_DEFAULT]),
        questions=[
            ClarificationQuestion(
                id="Q1", header=GOAL_HEADER, question=GOAL_QUESTION, options=options
            )
        ],
        original_query=query[:2000],
    )


_FILLER_WORDS = frozenset(
    {
        "tell",
        "more",
        "about",
        "some",
        "something",
        "thing",
        "things",
        "stuff",
        "info",
        # Broad domain topics: on-topic but naming no particular service.
        "security",
        "cybersecurity",
        "cyber",
        "compliance",
        "cloud",
        "microsoft",
        "help",
        "advice",
        "services",
        "service",
    }
)


# Words that never name a subject (cue scaffolding, pronouns, auxiliaries).
_NON_CONTENT = (
    _SCAFFOLD
    | _REFERENTS
    | _FILLER_WORDS
    | frozenset(
        [
            "i",
            "we",
            "you",
            "me",
            "us",
            "my",
            "our",
            "your",
            "am",
            "are",
            "im",
            "i'm",
            "we're",
            "you're",
            "be",
            "been",
            "have",
            "has",
            "had",
            "will",
            "would",
            "should",
            "shall",
            "need",
            "want",
            "tell",
            "give",
            "show",
            "know",
            "think",
            "get",
            "got",
            "buy",
            "like",
            "please",
            "pls",
            "help",
            "advice",
            "idea",
            "ideas",
            "thing",
            "things",
            "stuff",
            "something",
            "anything",
            "everything",
            "whatever",
            "really",
            "very",
            "just",
            "there",
            "here",
            "now",
            "today",
            "one",
            "ask",
            "question",
            "questions",
            "more",
            "other",
            "if",
            "at",
            "by",
            "from",
            "up",
            "out",
            "as",
            "about",
            "no",
            "yes",
            "ok",
            "hi",
            "hello",
            "lol",
            "idk",
            "dunno",
            "hmm",
            "um",
            "uh",
        ]
    )
)


def _has_own_content(query: str) -> bool:
    """Any word that names something (beyond scaffolding, cost words, fillers)?"""
    for raw in _WORD_RE.findall(_scan_text(query)[:_MAX_GOAL_CHARS]):
        word = raw.lower()
        if not (
            content_word_count(word) == 0
            or word in _SCAFFOLD
            or word in _COST_TIME_WORDS
            or word in _REFERENTS
            or word in _FILLER_WORDS
        ):
            return True
    return False


def _is_vague(query: str, history: list[ChatTurn] | None) -> bool:
    """A vague help / need statement (goal cue), by rule."""
    return (
        _goal_cue(_scan_text(query)) is not None
        or assess(query, history).kind is AmbiguityKind.GOAL
    )


def is_concrete_off_scope(query: str, history: list[ChatTurn] | None) -> bool:
    """A question with its own content words, naming nothing we cover, not vague."""
    return not (_subjects(query) or _history_subjects(history)) and (
        _has_own_content(query) and not _is_vague(query, history)
    )


def vague_fallback_clarification(
    query: str, history: list[ChatTurn] | None, sources: list[SourceChunk]
) -> Clarification | None:
    """The card for a short question the answer step could not ground.

    Nothing named in the question or the conversation: the goal card (what do
    you want to get done?); otherwise "which service?" over the retrieved ones.
    """
    if not (_subjects(query) or _history_subjects(history)):
        if is_concrete_off_scope(query, history):
            # A concrete question about something else ("what is a cat?") is
            # out of scope, not a vague one: no card, the caller falls back.
            return None
        card = goal_clarification(query, sources)
        if card is not None:
            return card
    return service_clarification(query, sources)


def _slot_question(
    slot: SlotSpec, index: int, sources: list[SourceChunk]
) -> ClarificationQuestion:
    """The slot's own options: intent choices, not claims, so no evidence is needed.

    Documents that do cover an option are attached to it as sources.
    """
    covering = [s for s in sources if _hit(slot.evidence, s.content)]
    options: list[ClarificationOption] = []
    scores: list[float] = []
    for spec in slot.options:
        grounded = [
            s
            for s in covering
            if spec.evidence is None or _hit(spec.evidence, s.content)
        ]
        options.append(
            _option(len(options) + 1, spec.label, spec.description, grounded)
        )
        scores.append(_best_score(grounded))
    return ClarificationQuestion(
        id=f"Q{index}",
        header=slot.header,
        question=slot.question,
        multi_select=slot.multi_select,
        options=recommend_option(options, scores),
    )


def _join_labels(labels: list[str]) -> str:
    """ "A, B and C": never implies the list is everything DeployIQ does."""
    if len(labels) < _MIN_COMPARED:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


_SLOT_INTROS = {1: "One quick question", 2: "Two quick questions"}
_SLOT_INTRO_DEFAULT = "A couple of quick questions"


def slot_clarification(
    query: str, slot_ids: tuple[str, ...], sources: list[SourceChunk]
) -> Clarification | None:
    """A short series of grounded questions (framework first), or ``None``."""
    settings = get_settings()
    questions: list[ClarificationQuestion] = []
    labels: list[str] = []
    if FRAMEWORK_SLOT in slot_ids:
        options = framework_options(sources, query)
        # At least ``clarify_min_options`` real options besides "Not sure".
        if len(options) > settings.clarify_min_options:
            labels = offering_labels()
            questions.append(
                ClarificationQuestion(
                    id="Q1",
                    header="Framework",
                    question=(
                        "Which framework is the audit against?"
                        if _AUDIT_RE.search(_scan_text(query))
                        else "Which framework do you need to meet?"
                    ),
                    options=options,
                )
            )
    for slot_id in slot_ids:
        slot = _SLOTS_BY_ID.get(slot_id)
        if slot is None or len(questions) >= settings.clarify_max_questions:
            continue
        questions.append(_slot_question(slot, len(questions) + 1, sources))
    if not questions:
        return None
    brand = settings.brand_name
    shown = questions[: settings.clarify_max_questions]
    slot_intro = _SLOT_INTROS.get(len(shown), _SLOT_INTRO_DEFAULT)
    covers = f" {brand} helps with {_join_labels(labels)}." if labels else ""
    return Clarification(
        preamble=(
            f"Happy to help you get started.{covers} {slot_intro} will point me the "
            "right way."
        )[:300],
        questions=shown,
        original_query=query[:2000],
    )


# ---------------------------------------------------------------------------
# The one cheap decision call (borderline requests only)
# ---------------------------------------------------------------------------

DECISION_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "clarify_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "slots"],
            "properties": {
                "action": {"type": "string", "enum": ["answer", "clarify"]},
                "slots": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}


def _decision_messages(query: str, slot_ids: tuple[str, ...]) -> list[dict[str, str]]:
    brand = get_settings().brand_name
    details = "\n".join(
        f"- {sid}: "
        + (
            "which compliance framework or service"
            if sid == FRAMEWORK_SLOT
            else _SLOTS_BY_ID[sid].question
        )
        for sid in slot_ids
    )
    return [
        {
            "role": "system",
            "content": (
                f"You decide whether {brand}'s website assistant must ask the "
                "visitor for details before answering. Reply in the required JSON."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Visitor question: {query}\n\nDetails the question leaves open:\n"
                f"{details}\n\n"
                'Set action "clarify" and list the detail ids (from the list '
                "above) only if a useful answer genuinely depends on them. Set "
                'action "answer" and slots [] if a general answer would serve '
                "the visitor well."
            ),
        },
    ]


async def _complete(
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, int] | None]:
    """One small structured completion (patched in tests)."""
    settings = get_settings()
    async with get_llm_breaker().guard():
        response = await get_chat_client().chat.completions.create(
            model=current_model_settings().llm_deployment,
            messages=messages,  # type: ignore[arg-type]
            reasoning_effort=settings.llm_reasoning_effort,  # type: ignore[arg-type]
            max_completion_tokens=settings.clarify_decision_max_tokens,
            response_format=DECISION_FORMAT,  # type: ignore[arg-type]
        )
    usage_obj = getattr(response, "usage", None)
    usage = (
        {
            "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
        }
        if usage_obj is not None
        else None
    )
    content = response.choices[0].message.content if response.choices else None
    return content or "", usage


async def confirm_slots(
    query: str, slot_ids: tuple[str, ...]
) -> tuple[tuple[str, ...] | None, dict[str, int] | None]:
    """Slots the model confirms (``()`` = answer), ``None`` on timeout/error."""
    settings = get_settings()
    try:
        raw, usage = await asyncio.wait_for(
            _complete(_decision_messages(query, slot_ids)),
            timeout=settings.clarify_decision_timeout_seconds,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("clarify_decision_failed", error_type=type(exc).__name__)
        return None, None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None, usage
    if not isinstance(payload, dict) or payload.get("action") != "clarify":
        return (), usage
    raw_slots = payload.get("slots")
    picked = (
        {s for s in raw_slots if isinstance(s, str)}
        if isinstance(raw_slots, list)
        else set()
    )
    return tuple(s for s in slot_ids if s in picked), usage


async def rule_clarification(
    assessment: Assessment, query: str, sources: list[SourceChunk]
) -> RuleClarification:
    """The clarification a rule signal asks for, grounded in ``sources``."""
    if not assessment.detected or not sources:
        return RuleClarification()
    settings = get_settings()
    poisoned = set(injection_in_chunks([s.content for s in sources]))
    clean = [s for i, s in enumerate(sources) if i not in poisoned]
    if assessment.kind is AmbiguityKind.GOAL:
        # A vague statement scores low by nature: no relevance floor, the
        # options are fixed and only need covering chunks.
        return RuleClarification(
            goal_clarification(query, clean, assessment.cue), decision="rules"
        )
    if max(s.score for s in sources) < settings.clarify_min_retrieval_score:
        return RuleClarification(decision="below_floor")
    if assessment.kind is not AmbiguityKind.SLOTS:
        return RuleClarification(
            service_clarification(query, clean, assessment.kind), decision="rules"
        )
    slots = assessment.missing_slots
    usage: dict[str, int] | None = None
    decision = "rules"
    if len(slots) < _DETERMINISTIC_SLOTS and not assessment.deterministic:
        confirmed, usage = await confirm_slots(query, slots)
        if not confirmed:
            return RuleClarification(
                usage=usage,
                decision="llm_failed" if confirmed is None else "llm_answer",
            )
        slots, decision = confirmed, "llm_clarify"
    return RuleClarification(
        slot_clarification(query, slots, clean), usage=usage, decision=decision
    )
