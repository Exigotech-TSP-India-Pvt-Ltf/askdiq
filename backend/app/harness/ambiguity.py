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
only when a retrieved chunk covers them. A borderline request (only two open
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
from app.harness.clarify import (
    clarification_streak,
    humanize_source_name,
    metadata_text,
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


@dataclass(frozen=True, slots=True)
class Assessment:
    """Rule result (no I/O), computed before retrieval."""

    kind: AmbiguityKind = AmbiguityKind.NONE
    missing_slots: tuple[str, ...] = ()

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


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _hit(pattern: re.Pattern[str], text: str) -> bool:
    return pattern.search(text) is not None


def _strip(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(" ", text)


# Matched against document names (and the question, to tell a named subject).
CATALOGUE: tuple[CatalogueEntry, ...] = (
    CatalogueEntry(
        "Essential Eight",
        _re(r"\b(essential[\s_-]*(eight|8)|e8|acsc)\b"),
        "ACSC Essential Eight implementation and maturity uplift",
        framework=True,
    ),
    CatalogueEntry(
        "ISO 27001",
        _re(r"\biso(?:[\s/_-]*iec)?[\s_-]*27001\b|\biso\b"),
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
        _re(r"\bzero[\s_-]*trust\b"),
        "Zero Trust assessment and implementation",
    ),
    CatalogueEntry(
        "Security Baseline",
        _re(r"\bsecurity[\s_-]*baseline\b"),
        "Microsoft 365 Security Baseline Assessment",
    ),
    CatalogueEntry(
        "DeployIQ platform",
        _re(r"\bdeploy[\s_-]*iq[\s_-]*platform\b|\bplatform\b"),
        "The DeployIQ orchestration platform",
    ),
)


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

    if len(grouped) >= settings.clarify_min_options:
        items = [(e.label, e.description, docs) for e, docs in grouped.values()]
    else:
        by_doc: dict[str, list[SourceChunk]] = {}
        for source in ranked:
            if _source_name(source):
                by_doc.setdefault(source.document_id, []).append(source)
        items = [
            (humanize_source_name(_source_name(docs[0]) or ""), "", docs)
            for docs in by_doc.values()
        ]
    return [
        _option(i, label, description, docs)
        for i, (label, description, docs) in enumerate(
            items[: settings.clarify_max_options], 1
        )
    ]


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
    r"(get|become|be) compliant|pass (the|an|our) audit|prepare for (the |an |our )?"
    r"audit|how long (will|would|does) it take|roadmap|plan of action)\b"
)
_COMPLIANCE_RE = _re(
    r"\b(audit\w*|complian\w*|certif\w*|accredit\w*|maturity|framework|uplift|"
    r"regulat\w*|assessment)\b"
)
_AUDIT_RE = _re(r"\baudit\w*\b")

SLOTS: tuple[SlotSpec, ...] = (
    SlotSpec(
        id="maturity",
        header="Maturity",
        question="Where are your controls today?",
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
                _re(r"evidence|audit[\s-]*ready|validat|verif"),
            ),
        ),
    ),
    SlotSpec(
        id="deadline",
        header="Deadline",
        question="When is the deadline?",
        needed=_re(
            r"\b(fast|quick(ly)?|asap|urgent(ly)?|deadline|coming up|soon|upcoming|"
            r"tight|rush)\b"
        ),
        filled=_re(
            r"\b\d+\s*(day|week|month|year)s?\b|\b(next|this) (week|month|quarter)\b|"
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
        question="Which environment is in scope?",
        filled=_re(
            r"\b(microsoft 365|m365|office 365|o365|azure|entra|intune|aws|gcp|google|"
            r"on[\s-]?prem\w*|hybrid|windows|tenant)\b"
        ),
        evidence=_re(r"microsoft 365|entra|intune|hybrid|on[\s-]?prem"),
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
                _re(r"hybrid|on[\s-]?prem"),
            ),
        ),
        multi_select=True,
    ),
)
_SLOTS_BY_ID = {s.id: s for s in SLOTS}


def _missing_slots(query: str) -> tuple[str, ...]:
    """Open details of a compliance planning request; () when it is not one."""
    if not (_PLANNING_RE.search(query) and _COMPLIANCE_RE.search(query)):
        return ()
    if any(_hit(e.pattern, query) for e in CATALOGUE):
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
    r"\bit\s+(is|was|be)?\s*(possible|necessary|true|ok|okay|worth|required|"
    r"mandatory|important|hard|easy|difficult)\b"
)
_COMPARISON_RE = _re(
    r"\b(the other( one| option| service| framework| product)?|that one|this one|"
    r"than (it|that|this|them|those)|compared? (to|with) (it|that|this|them)|"
    r"which one|both of them)\b"
)
_PROPER_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*[A-Z0-9][A-Za-z0-9]*\b|\b[A-Z][a-z]+\b")


_COMMON = _SCAFFOLD | {"i", "we", "our", "you", "your", "my", "hi", "hello", "please"}


def _subjects(text: str) -> set[str]:
    """Named subjects: catalogue services/frameworks and proper nouns."""
    found: set[str] = set()
    for entry in CATALOGUE:
        if _hit(entry.pattern, text):
            found.add(entry.label)
            text = _strip(entry.pattern, text)
    # Proper nouns after the first word (sentence case is not a name).
    rest = " ".join(text.split()[1:])
    found |= {m.lower() for m in _PROPER_RE.findall(rest) if m.lower() not in _COMMON}
    return found


def _history_subjects(history: list[ChatTurn] | None) -> set[str]:
    """Subjects of the visitor's turns and of real answers (not fallbacks)."""
    found: set[str] = set()
    for turn in history or []:
        if turn.role == "user" or turn.outcome == _ANSWERED:
            found |= _subjects(turn.content)
    return found


def _dangling_pronoun(query: str) -> bool:
    if _EXPLETIVE_RE.search(query):
        return False
    for word in (w.lower() for w in _WORD_RE.findall(query)):
        if word in _REFERENTS:
            return True
        if word not in _SCAFFOLD:
            return False
    return False


def assess(
    query: str,
    history: list[ChatTurn] | None,
    *,
    clarification_reply: bool = False,
) -> Assessment:
    """Rule gate (no I/O): does this question need asking back?"""
    settings = get_settings()
    if (
        not settings.clarify_enabled
        or clarification_reply
        or clarification_streak(history) >= settings.clarify_max_consecutive
    ):
        return Assessment()
    missing = _missing_slots(query)
    if missing:
        return Assessment(AmbiguityKind.SLOTS, missing)
    known = _subjects(query) | _history_subjects(history)
    if _COMPARISON_RE.search(query) and len(known) < _MIN_COMPARED:
        return Assessment(AmbiguityKind.COMPARISON)
    if _dangling_pronoun(query) and not known:
        return Assessment(AmbiguityKind.REFERENT)
    return Assessment()


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
    if _COST_RE.search(query):
        text = "Which service would you like pricing for?"
    elif _TIME_RE.search(query):
        text = "Which service would you like the timeline for?"
    else:
        text = f"Which {brand} service do you mean?"
    return (
        f"{brand} offers several services; which one do you mean?",
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


def _slot_question(
    slot: SlotSpec, index: int, sources: list[SourceChunk]
) -> ClarificationQuestion | None:
    covering = [s for s in sources if _hit(slot.evidence, s.content)]
    if not covering:
        return None
    options: list[ClarificationOption] = []
    for spec in slot.options:
        grounded = (
            [s for s in covering if _hit(spec.evidence, s.content)]
            if spec.evidence
            else covering
        )
        if grounded:
            options.append(
                _option(len(options) + 1, spec.label, spec.description, grounded)
            )
    if len(options) < get_settings().clarify_min_options:
        return None
    return ClarificationQuestion(
        id=f"Q{index}",
        header=slot.header,
        question=slot.question,
        multi_select=slot.multi_select,
        options=options,
    )


def slot_clarification(
    query: str, slot_ids: tuple[str, ...], sources: list[SourceChunk]
) -> Clarification | None:
    """A short series of grounded questions (framework first), or ``None``."""
    settings = get_settings()
    questions: list[ClarificationQuestion] = []
    labels: list[str] = []
    if FRAMEWORK_SLOT in slot_ids:
        options = service_options(sources, frameworks_only=True)
        if len(options) >= settings.clarify_min_options:
            labels = [o.label for o in options]
            questions.append(
                ClarificationQuestion(
                    id="Q1",
                    header="Framework",
                    question=(
                        "Which framework is the audit against?"
                        if _AUDIT_RE.search(query)
                        else "Which framework do you need to meet?"
                    ),
                    options=options,
                )
            )
    for slot_id in slot_ids:
        slot = _SLOTS_BY_ID.get(slot_id)
        if slot is None or len(questions) >= settings.clarify_max_questions:
            continue
        question = _slot_question(slot, len(questions) + 1, sources)
        if question is not None:
            questions.append(question)
    if not questions:
        return None
    brand = settings.brand_name
    covers = f" {brand} covers {', '.join(labels)}." if labels else ""
    return Clarification(
        preamble=(
            f"Happy to help you get ready.{covers} A few quick details will let me "
            "give you a specific plan and timeline."
        )[:300],
        questions=questions[: settings.clarify_max_questions],
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
    if max(s.score for s in sources) < settings.clarify_min_retrieval_score:
        return RuleClarification(decision="below_floor")
    poisoned = set(injection_in_chunks([s.content for s in sources]))
    clean = [s for i, s in enumerate(sources) if i not in poisoned]
    if assessment.kind is not AmbiguityKind.SLOTS:
        return RuleClarification(
            service_clarification(query, clean, assessment.kind), decision="rules"
        )
    slots = assessment.missing_slots
    usage: dict[str, int] | None = None
    decision = "rules"
    if len(slots) < _DETERMINISTIC_SLOTS:
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
