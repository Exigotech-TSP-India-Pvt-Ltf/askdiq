"""Deterministic rule stage of the insights classifier (planchanges4 B6.2.1).

Pure functions only: no I/O. Turns these rules can label never reach the LLM
(greetings and closings, prompt injection, spam, blocked turns and model
failures); they get ``classified_by=rule`` and ``confidence=1.0``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.governance.outcomes import LEGACY_OUTCOME_MAP
from app.guardrails.conversational import (
    detect_conversational_intent,
    get_farewell_response,
    get_thanks_response,
)
from app.guardrails.pre_checks import check_unsafe_input
from app.insights.taxonomy import (
    BotQuality,
    ClassifiedBy,
    Intent,
    Issue,
    Taxonomy,
)
from app.insights.types import TurnInput, TurnLabel

RULE_CONFIDENCE = 1.0

INJECTION_FLAGS = frozenset({"unsafe_input_detected", "blocked_unsafe"})
# Refusals required by policy (as opposed to off-topic deflections).
POLICY_FLAGS = INJECTION_FLAGS | {"secret_leak_detected", "pii_leak_detected"}
SPAM_FLAGS = frozenset({"spam_detected"})
UNCLEAR_FLAGS = frozenset({"unclear_input", "empty_query", "query_too_long"})
# The reply had little or no evidence behind it ("no-info reply").
WEAK_EVIDENCE_FLAGS = frozenset(
    {"no_retrieved_sources", "low_retrieval_confidence", "off_topic"}
)


@dataclass(frozen=True, slots=True)
class QualityMapping:
    bot_quality: BotQuality
    handled: bool


def normalize_outcome(outcome: str | None) -> str | None:
    if not outcome:
        return None
    lowered = outcome.lower()
    return LEGACY_OUTCOME_MAP.get(lowered, lowered)


def map_outcome(
    outcome: str | None, grounded: bool | None, flags: frozenset[str]
) -> QualityMapping:
    """Deterministic outcome -> (bot_quality, handled) table (newplan 6.2).

    | outcome                          | bot_quality          | handled |
    | answered and grounded            | OK                   | yes     |
    | answered/ungrounded, weak sources| FB                   | no      |
    | answered/ungrounded otherwise    | PART                 | no      |
    | blocked                          | CORR (policy)/SCOPE  | yes     |
    | conversational                   | ACK                  | yes     |
    | llm_failed                       | OFF                  | no      |
    """
    normalized = normalize_outcome(outcome)
    if normalized == "conversational":
        return QualityMapping(BotQuality.ACK, handled=True)
    if normalized == "llm_failed":
        return QualityMapping(BotQuality.OFF, handled=False)
    if normalized == "blocked":
        quality = BotQuality.CORR if flags & POLICY_FLAGS else BotQuality.SCOPE
        return QualityMapping(quality, handled=True)
    if normalized == "answered" and grounded is True:
        return QualityMapping(BotQuality.OK, handled=True)
    if flags & WEAK_EVIDENCE_FLAGS:
        return QualityMapping(BotQuality.FB, handled=False)
    return QualityMapping(BotQuality.PART, handled=False)


def derive_handled(
    bot_quality: str, grounded: bool | None, handled_qualities: frozenset[str]
) -> bool:
    """``handled`` = quality counts as handled and, for ``OK``, the answer was grounded."""
    if bot_quality not in handled_qualities:
        return False
    return bot_quality != BotQuality.OK or grounded is True


def conversational_intent(user_text: str) -> Intent | None:
    """GRE for greetings/capability asks, ACK for thanks and goodbyes."""
    reply = detect_conversational_intent(user_text)
    if reply is None:
        return None
    if reply in (get_thanks_response(), get_farewell_response()):
        return Intent.CLOSING
    return Intent.GREETING


def is_injection(user_text: str, flags: frozenset[str]) -> bool:
    if flags & INJECTION_FLAGS:
        return True
    passed, _ = check_unsafe_input(user_text)
    return not passed


def _rule(
    turn: TurnInput,
    taxonomy: Taxonomy,
    intent: str,
    quality: BotQuality,
    *,
    handled: bool,
    issue: str | None = None,
) -> TurnLabel:
    return TurnLabel(
        message_id=turn.message_id,
        session_id=turn.session_id,
        intent=intent,
        bot_quality=quality,
        issue=issue,
        substantive=taxonomy.is_substantive(intent),
        handled=handled,
        friction=None,
        confidence=RULE_CONFIDENCE,
        classified_by=ClassifiedBy.RULE,
    )


def classify_by_rule(turn: TurnInput, taxonomy: Taxonomy) -> TurnLabel | None:
    """Return a deterministic label, or None when the turn needs the LLM."""
    flags = frozenset(turn.guardrail_flags)
    outcome = normalize_outcome(turn.outcome)

    small_talk = conversational_intent(turn.user_text)
    if small_talk is not None or outcome == "conversational":
        return _rule(
            turn,
            taxonomy,
            small_talk or Intent.GREETING,
            BotQuality.ACK,
            handled=True,
        )
    if is_injection(turn.user_text, flags):
        return _rule(turn, taxonomy, Intent.INJECTION, BotQuality.CORR, handled=True)
    if flags & SPAM_FLAGS:
        return _rule(turn, taxonomy, Intent.OTHER, BotQuality.SCOPE, handled=True)
    if flags & UNCLEAR_FLAGS:
        return _rule(turn, taxonomy, Intent.AMBIGUOUS, BotQuality.CLAR, handled=False)
    if outcome == "llm_failed":
        return _rule(
            turn,
            taxonomy,
            Intent.OTHER,
            BotQuality.OFF,
            handled=False,
            issue=Issue.LLM_FAIL,
        )
    if outcome == "blocked":
        mapping = map_outcome(outcome, turn.grounded, flags)
        intent = (
            Intent.OTHER if mapping.bot_quality == BotQuality.CORR else Intent.OFF_SCOPE
        )
        return _rule(
            turn, taxonomy, intent, mapping.bot_quality, handled=mapping.handled
        )
    return None
