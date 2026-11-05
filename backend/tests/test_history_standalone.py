"""Follow-up resolution: a self-contained ask never inherits the previous topic,
a real follow-up still carries it (turns taken from the KB pages' wording)."""

from __future__ import annotations

import pytest

from app.retrieval.retriever import QueryPart, _standalone
from app.schemas.query import ChatTurn


def _history(user: str, assistant: str) -> list[ChatTurn]:
    return [
        ChatTurn(role="user", content=user),
        ChatTurn(role="assistant", content=assistant),
    ]


_ZERO_TRUST = _history(
    "what is zero trust?",
    "Zero Trust is a security model that removes implicit trust from technology "
    "environments.",
)
_CIS = _history(
    "what is the CIS benchmark assessment?",
    "We assess Microsoft 365 configuration across identity, threat protection, "
    "collaboration, tenant governance and data controls.",
)
_STARTER = _history(
    "Tell me about the Zero Trust Starter plan",
    "The Starter plan gives access to the Microsoft Zero Trust Assessment tool.",
)
_BASELINE = _history(
    "What areas does the Security Baseline Assessment cover?",
    "The five focus areas are Identity & Access, Email & Collaboration, Endpoint "
    "Management, Endpoint Protection and Backup & Recovery.",
)
_ISO = _history(
    "What is ISO 42001?",
    "ISO/IEC 42001 is the international standard for managing AI responsibly.",
)


def _search(text: str, history: list[ChatTurn]) -> str:
    return _standalone(QueryPart(text, text), history).search


@pytest.mark.parametrize(
    ("ask", "history"),
    [
        ("whats your address?", _ZERO_TRUST),
        ("what's your phone number?", _ZERO_TRUST),
        ("what's the phone number?", _BASELINE),
        ("what is your email address?", _CIS),
        ("how can I contact you?", _STARTER),
        ("can I call you?", _CIS),
        ("how do I get in touch with you?", _ISO),
        ("where are you based?", _STARTER),
        ("where is your office?", _ISO),
        ("who owns you guys?", _ZERO_TRUST),
        ("what does your privacy policy say about cookies?", _ZERO_TRUST),
        ("what is the essential eight?", _ZERO_TRUST),
        ("What is ISO 42001?", _STARTER),
        ("do you sell my data?", _ZERO_TRUST),
        ("who funds you?", _ZERO_TRUST),
        ("is my data stored in Australia?", _ZERO_TRUST),
        ("how do I book a consultation?", _CIS),
        ("will you certify us?", _ZERO_TRUST),
        ("can I cancel my subscription?", _BASELINE),
    ],
)
def test_self_contained_ask_keeps_its_own_subject(
    ask: str, history: list[ChatTurn]
) -> None:
    assert _search(ask, history) == ask


@pytest.mark.parametrize(
    ("ask", "history", "subject"),
    [
        ("which version do you use?", _CIS, "CIS"),
        ("how do I get the tool?", _STARTER, "Zero Trust"),
        ("is it self-service?", _STARTER, "Zero Trust"),
        ("which one is self-service?", _STARTER, "Zero Trust"),
        ("what about pricing for that?", _STARTER, "Zero Trust"),
        ("pricing?", _ISO, "42001"),
        ("what about email?", _BASELINE, "Security Baseline Assessment"),
        ("how long does it take?", _ZERO_TRUST, "Zero Trust"),
    ],
)
def test_real_follow_up_carries_the_conversation_subject(
    ask: str, history: list[ChatTurn], subject: str
) -> None:
    assert subject in _search(ask, history)


def test_referential_follow_up_folds_the_previous_ask() -> None:
    history = _history(
        "Which law governs your terms?",
        "These Terms are governed by the laws of New South Wales, Australia.",
    )
    search = _search("and when did they take effect?", history)
    assert "terms" in search.lower()


def test_definite_noun_from_two_turns_back_is_a_follow_up() -> None:
    history = [
        *_STARTER,
        ChatTurn(role="user", content="is it self-service?"),
        ChatTurn(
            role="assistant",
            content="The Starter plan is self-service with no service engagement required.",
        ),
    ]
    assert "Zero Trust" in _search("how do I get the tool?", history)
