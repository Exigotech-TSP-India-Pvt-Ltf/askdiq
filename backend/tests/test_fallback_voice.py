"""Visitor-facing fallback and card wording reads like a person, not a form."""

from __future__ import annotations

import re

import pytest

from app.core.config import get_settings
from app.harness import ambiguity
from app.harness.clarify import REPLY_HINT
from app.harness.orchestrator import _fallback_answer
from app.schemas.query import SourceChunk

_ALL_FLAGS = [
    "unsafe_input_detected",
    "no_permission",
    "application_secret_request",
    "personal_contact_request",
    "spam_detected",
    "unclear_input",
    "query_too_long",
    "empty_query",
    "off_topic",
    "no_sources",
    "llm_call_failed",
    "empty_llm_output",
    "low_retrieval_confidence",
    "possible_hallucination",
]
_SPECIALIST_FLAGS = [
    "unsafe_input_detected",
    "no_permission",
    "application_secret_request",
    "personal_contact_request",
    "off_topic",
    "no_sources",
    "llm_call_failed",
    "empty_llm_output",
    "low_retrieval_confidence",
    "possible_hallucination",
]
_BANNED = ("knowledge base", "supplied context", "the documents", "materials")
_DASHES = re.compile(r"[-\u2013\u2014]")
_BRAND = "DeployIQ"


@pytest.mark.parametrize("flag", _ALL_FLAGS)
def test_fallback_text_is_clean_and_deterministic(flag: str) -> None:
    text = _fallback_answer([flag], _BRAND)
    assert text == _fallback_answer([flag], _BRAND)
    lowered = text.lower()
    assert not any(word in lowered for word in _BANNED)
    assert not _DASHES.search(text)
    assert "I want to give you an accurate answer" not in text


@pytest.mark.parametrize("flag", _SPECIALIST_FLAGS)
def test_help_fallbacks_hand_over_to_a_specialist(flag: str) -> None:
    assert "specialist" in _fallback_answer([flag], _BRAND)


def test_off_topic_is_templated_with_the_brand() -> None:
    text = _fallback_answer(["off_topic"], "Acme")
    assert text.startswith("That one's outside what I can help with.")
    assert "I'm here for Acme, Zero Trust" in text


def test_default_ungrounded_wording() -> None:
    text = _fallback_answer(["low_retrieval_confidence"], _BRAND)
    assert text.startswith("I don't want to guess on that one.")


def test_unsafe_wins_over_other_flags() -> None:
    text = _fallback_answer(["spam_detected", "unsafe_input_detected"], _BRAND)
    assert text.startswith("I can't help with that, sorry.")


def _sources(*names: str) -> list[SourceChunk]:
    return [
        SourceChunk(
            document_id=f"d{i}",
            chunk_id=f"c{i}",
            content="service overview",
            score=0.9 - i / 100,
            metadata={"source_name": name},
        )
        for i, name in enumerate(names)
    ]


def test_service_card_does_not_repeat_its_question() -> None:
    card = ambiguity.service_clarification(
        "tell me more", _sources("essential-eight.md", "zero-trust.md")
    )
    assert card is not None
    question = card.questions[0]
    assert card.preamble == "Happy to help. Quick question first so I get this right."
    assert question.question == "Which service are you after?"
    assert card.preamble != question.question
    assert len(card.preamble) <= 300
    assert len(question.question) <= 300


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("how much does it cost?", "Which service do you want pricing for?"),
        ("how long does it take?", "Which service do you want the timeline for?"),
        ("tell me about it", "Which service are you after?"),
    ],
)
def test_service_question_variants(query: str, expected: str) -> None:
    card = ambiguity.service_clarification(
        query, _sources("essential-eight.md", "zero-trust.md")
    )
    assert card is not None
    assert card.questions[0].question == expected


def test_catalogue_pages_are_offered_by_service_name() -> None:
    card = ambiguity.service_clarification(
        "what does it cost?", _sources("E8 service page.md", "Home page.md")
    )
    assert card is not None
    labels = [o.label for o in card.questions[0].options]
    assert "E8 service page" not in labels
    assert "Essential Eight" in labels


def test_reply_hint() -> None:
    assert REPLY_HINT == "Pick one, or tell me a bit more."


def test_slot_questions_and_preamble_make_no_plan_promise() -> None:
    card = ambiguity.slot_clarification(
        "we need to be ready fast",
        ("maturity", "deadline", "environment"),
        [],
    )
    assert card is not None
    questions = [q.question for q in card.questions]
    assert questions == [
        "Where are your controls at today?",
        "Do you have a deadline in mind?",
        "Which environment are you working in?",
    ]
    assert "plan" not in card.preamble.lower()
    assert "timeline" not in card.preamble.lower()
    assert "A couple of quick questions will point me the right way." in card.preamble
    assert len(card.preamble) <= 300
    assert all(len(q) <= 300 for q in questions)


def test_two_slot_preamble_says_two() -> None:
    card = ambiguity.slot_clarification("ready?", ("maturity", "deadline"), [])
    assert card is not None
    assert "Two quick questions will point me the right way." in card.preamble


def test_goal_preambles_fit_the_limit() -> None:
    assert get_settings().brand_name
    assert all(0 < len(p) <= 300 for p in ambiguity.GOAL_PREAMBLES.values())
