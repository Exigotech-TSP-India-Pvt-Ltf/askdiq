"""Insight taxonomy: stable codes for intents, bot quality and issues.

Codes are stable identifiers stored on ``turn_insights``; labels, colours and
groups are editable in ``insight_taxonomy`` (F7). ``DEFAULT_TAXONOMY`` mirrors
the seed in migration ``4c1e9a7d2b65`` and is used when the table is empty and
by pure unit tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class TaxonomyKind(StrEnum):
    INTENT = "intent"
    BOT_QUALITY = "bot_quality"
    ISSUE = "issue"


class Intent(StrEnum):
    """Intent codes the rule stage emits (others come from the taxonomy table)."""

    GREETING = "GRE"
    CLOSING = "ACK"
    OFF_SCOPE = "OOS"
    INJECTION = "INJ"
    AMBIGUOUS = "AMB"
    OTHER = "OTHER"


class BotQuality(StrEnum):
    """Domain-neutral bot-quality codes (kept from the reference HTML)."""

    OK = "OK"
    CORR = "CORR"
    SCOPE = "SCOPE"
    PART = "PART"
    FB = "FB"
    CLAR = "CLAR"
    OFF = "OFF"
    FALSE = "FALSE"
    ACK = "ACK"


class Issue(StrEnum):
    LLM_FAIL = "LLM_FAIL"
    OTHER = "OTHER"


class Friction(StrEnum):
    HOSTILE = "hostile"
    ABANDONED = "abandoned"


class ClassifiedBy(StrEnum):
    RULE = "rule"
    LLM = "llm"
    MANUAL = "manual"


NON_SUBSTANTIVE_INTENTS = frozenset({Intent.GREETING, Intent.CLOSING})


@dataclass(frozen=True, slots=True)
class TaxonomyEntry:
    kind: TaxonomyKind
    code: str
    label: str
    group: str | None = None
    substantive: bool = True
    handled: bool = False
    color: str | None = None


@dataclass(frozen=True)
class Taxonomy:
    """Active taxonomy values, used to constrain and validate labels."""

    intents: dict[str, TaxonomyEntry] = field(default_factory=dict)
    qualities: dict[str, TaxonomyEntry] = field(default_factory=dict)
    issues: dict[str, TaxonomyEntry] = field(default_factory=dict)

    @classmethod
    def from_entries(cls, entries: Iterable[TaxonomyEntry]) -> Taxonomy:
        by_kind: dict[TaxonomyKind, dict[str, TaxonomyEntry]] = {
            kind: {} for kind in TaxonomyKind
        }
        for entry in entries:
            by_kind[entry.kind][entry.code] = entry
        return cls(
            intents=by_kind[TaxonomyKind.INTENT],
            qualities=by_kind[TaxonomyKind.BOT_QUALITY],
            issues=by_kind[TaxonomyKind.ISSUE],
        )

    def is_substantive(self, intent: str) -> bool:
        entry = self.intents.get(intent)
        if entry is None:
            return intent not in NON_SUBSTANTIVE_INTENTS
        return entry.substantive

    def handled_qualities(self) -> frozenset[str]:
        return frozenset(code for code, e in self.qualities.items() if e.handled)


_CUSTOMER = "Customer / prospect"
_OFF_SCOPE = "Off-scope request"


def _intent(
    code: str, label: str, group: str | None, *, substantive: bool = True
) -> TaxonomyEntry:
    return TaxonomyEntry(
        TaxonomyKind.INTENT, code, label, group=group, substantive=substantive
    )


def _quality(code: str, label: str, color: str, *, handled: bool) -> TaxonomyEntry:
    return TaxonomyEntry(
        TaxonomyKind.BOT_QUALITY,
        code,
        label,
        substantive=code != BotQuality.ACK,
        handled=handled,
        color=color,
    )


def _issue(code: str, label: str) -> TaxonomyEntry:
    return TaxonomyEntry(TaxonomyKind.ISSUE, code, label)


DEFAULT_ENTRIES: tuple[TaxonomyEntry, ...] = (
    # DeployIQ intents (newplan 6.3, confirmed by the owner).
    _intent("SVC", "Platform and services", _CUSTOMER),
    _intent("SEC", "Security and compliance", _CUSTOMER),
    _intent("RAI", "Responsible AI and trust", _CUSTOMER),
    _intent("CO", "Company facts", _CUSTOMER),
    _intent("CON", "Contact and pricing", _CUSTOMER),
    _intent("BIZ", "Partner or quote request", _CUSTOMER),
    _intent("SUP", "Support", _CUSTOMER),
    _intent("OOS", "Off-scope", _OFF_SCOPE),
    _intent("INJ", "Prompt injection", _OFF_SCOPE),
    _intent("AMB", "Ambiguous", _OFF_SCOPE),
    _intent("OTHER", "Other", _OFF_SCOPE),
    _intent("GRE", "Greeting", None, substantive=False),
    _intent("ACK", "Closing", None, substantive=False),
    # Bot quality (HTML renderer codes, domain-neutral).
    _quality("OK", "Answered correctly", "#008080", handled=True),
    _quality("CORR", "Correct refusal", "#2E8B8B", handled=True),
    _quality("SCOPE", "Out of scope", "#6E7C7D", handled=True),
    _quality("PART", "Partly answered", "#C8A039", handled=False),
    _quality("FB", "No-info reply", "#8A8F73", handled=False),
    _quality("CLAR", "Clarifying question", "#5B8FA8", handled=False),
    _quality("OFF", "Off-target", "#C0362C", handled=False),
    _quality("FALSE", "False action claim", "#9A3B45", handled=False),
    _quality("ACK", "Closing", "#B7C4C5", handled=True),
    # Issues.
    _issue("KB_GAP", "Info not in knowledge base"),
    _issue("NARROW", "Answer too narrow"),
    _issue("WRONG", "Wrong answer"),
    _issue("GENERIC", "Generic answer"),
    _issue("NO_ROUTE", "Support request, no route"),
    _issue("FALSE_ACTION", "Claimed an action it cannot do"),
    _issue("CLAR_LEFT", "Clarifying question, user left"),
    _issue("PRICING", "Pricing or quote needs follow-up"),
    _issue("LLM_FAIL", "Model error"),
    _issue("OTHER", "Other"),
)

DEFAULT_TAXONOMY = Taxonomy.from_entries(DEFAULT_ENTRIES)
