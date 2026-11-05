"""The typed decision each pipeline stage hands to the next.

One contract for the whole answer path (owner rules, 2026-11-04):

- If the answer is in the knowledge base it MUST be given; if it is not, it
  must not be. Not a single unsupported line; no non-public information.
- Partly covered: answer the covered parts with citations, then one fixed
  line naming what is not covered.
- Clarify only when the question matches knowledge-base content but is
  ambiguous across services, or the visitor wants to buy without naming a
  service. Off-domain asks ("price of a burger") get the off-topic reply.
- Buying intent with a known service: answer what the knowledge base says,
  then the public sales contact taken from the knowledge base.

Stages: normalise -> safety -> intent -> retrieve + gate -> clarify ->
generate (quote-backed) -> verify -> render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Intent(StrEnum):
    """What the visitor is doing (``app.guardrails.intent``)."""

    QUESTION = "question"
    GREETING = "greeting"
    THANKS = "thanks"
    FAREWELL = "farewell"
    CAPABILITY = "capability"
    IDENTITY = "identity"
    ACKNOWLEDGEMENT = "acknowledgement"
    CONTACT = "contact"
    BUYING = "buying"  # price, quote, demo, purchase, "sign me up"
    INCIDENT = "incident"  # "we got hacked"
    ACCOUNT_SUPPORT = "account_support"  # cancel/refund/bot is broken
    COMPLAINT = "complaint"


@dataclass(frozen=True, slots=True)
class IntentResult:
    intent: Intent
    score: float = 0.0
    # True only when the message is *only* that intent and names no
    # product/service: then a canned reply is allowed.
    pure: bool = False


class Route(StrEnum):
    """The retrieval gate's verdict for the whole message."""

    ANSWER = "answer"  # every part covered
    PARTIAL = "partial"  # some parts covered, some not
    NOT_COVERED = "not_covered"  # on-domain, nothing in the KB answers it
    OFF_TOPIC = "off_topic"  # not about the business at all
    CLARIFY = "clarify"  # KB matches but the ask is ambiguous / buying w/o service


@dataclass(frozen=True, slots=True)
class PartVerdict:
    """One sub-question of the message and whether the KB covers it."""

    text: str
    covered: bool
    score: float = 0.0  # calibrated relevance (cross-encoder based)
    document_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: Route
    parts: tuple[PartVerdict, ...] = ()
    reason: str = ""
    buying: bool = False  # add the knowledge-base sales contact line

    @property
    def covered_parts(self) -> tuple[PartVerdict, ...]:
        return tuple(p for p in self.parts if p.covered)

    @property
    def uncovered_parts(self) -> tuple[PartVerdict, ...]:
        return tuple(p for p in self.parts if not p.covered)


@dataclass(slots=True)
class StageLog:
    """Per-stage decision record, logged once per request."""

    entries: list[dict] = field(default_factory=list)

    def add(self, stage: str, decision: str, reason: str = "", ms: float = 0.0) -> None:
        self.entries.append(
            {"stage": stage, "decision": decision, "reason": reason, "ms": round(ms, 1)}
        )
