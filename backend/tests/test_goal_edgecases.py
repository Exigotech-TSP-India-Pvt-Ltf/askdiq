"""Edge cases of the goal card: concrete off-topic questions are declined (no
goal card), the pricing pick and pricing asks get a fixed reply, and a live
security incident gets a warm fixed reply instead of an off-topic block."""

from __future__ import annotations

import time

import pytest

from app.guardrails.conversational import (
    INCIDENT_REPLY,
    PRICING_REPLY,
    detect_incident_request,
    detect_pricing_request,
)
from app.harness import orchestrator
from app.harness.ambiguity import is_concrete_off_scope, vague_fallback_clarification
from app.harness.clarify import (
    CLARIFY_OUTCOME,
    GOAL_HEADER,
    GOAL_PRICING,
    GOAL_QUERIES,
    is_pricing_pick,
)
from tests.test_clarify_trigger import _no_decision_call, _run, _sources

OFF_TOPIC_MARKER = "outside what I can help with"

CONCRETE_OFF_TOPIC = [
    "What is a cat?",
    "what is the weather today",
    "can you write me a python script",
]
VAGUE = ["i am confused", "idk"]
INCIDENTS = [
    "we got hacked what should we do",
    "we've been breached",
    "ransomware",
    "our email was compromised",
    "phishing attack happened",
    "we are under attack",
]
INFORMATIONAL = [
    "how does DeployIQ help after a breach?",
    "What is a data breach?",
    "does Zero Trust stop ransomware?",
]


@pytest.mark.parametrize("query", CONCRETE_OFF_TOPIC)
def test_concrete_questions_are_off_scope_not_vague(query: str) -> None:
    assert is_concrete_off_scope(query, None)


@pytest.mark.parametrize("query", [*VAGUE, "how much does it cost?"])
def test_vague_prompts_are_not_off_scope(query: str) -> None:
    assert not is_concrete_off_scope(query, None)


@pytest.mark.parametrize("query", CONCRETE_OFF_TOPIC)
async def test_concrete_off_topic_gets_the_off_topic_fallback(
    monkeypatch, query: str
) -> None:
    _no_decision_call(monkeypatch)
    response, _, _ = await _run(
        monkeypatch, query, ["I think it is an animal."], grounded=False
    )
    assert response.clarification is None
    assert response.outcome != CLARIFY_OUTCOME
    assert OFF_TOPIC_MARKER in response.answer
    assert response.sources == []
    assert response.citations == []
    assert response.grounded is False


@pytest.mark.parametrize("query", VAGUE)
async def test_vague_prompts_still_get_the_goal_card(monkeypatch, query: str) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(monkeypatch, query, sources=_sources(scale=0.2))
    assert calls == []
    assert response.clarification is not None
    assert response.clarification.questions[0].header == GOAL_HEADER


def test_fallback_card_is_goal_only_for_vague_prompts() -> None:
    card = vague_fallback_clarification("what's best", None, _sources())
    assert card is not None
    assert card.questions[0].header == GOAL_HEADER
    assert vague_fallback_clarification("What is a cat?", None, _sources()) is None


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def test_pricing_pick_is_recognised() -> None:
    assert is_pricing_pick(GOAL_QUERIES[GOAL_PRICING])
    assert is_pricing_pick(f"{GOAL_QUERIES[GOAL_PRICING]} (a small business)")
    assert not is_pricing_pick("How much does the CIS assessment cost?")


async def test_pricing_pick_is_answered_without_the_llm(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, retrieve_calls = await _run(
        monkeypatch, GOAL_QUERIES[GOAL_PRICING], [], clarification_reply=True
    )
    assert calls == []
    assert retrieve_calls == []
    assert response.answer == PRICING_REPLY
    assert response.sources == []
    assert response.citations == []
    assert response.outcome == "conversational"
    assert "Starter plan is free" in response.answer


@pytest.mark.parametrize(
    "query",
    [
        "can i get a quote",
        "Can we get a free quote?",
        "how much does it cost for my company",
        "what are your prices",
    ],
)
def test_direct_pricing_asks_get_the_fixed_reply(query: str) -> None:
    assert detect_pricing_request(query) == PRICING_REPLY


@pytest.mark.parametrize(
    "query",
    ["how much does it cost?", "how much does the CIS assessment cost", "pricing?"],
)
def test_bare_or_named_pricing_is_not_intercepted(query: str) -> None:
    assert detect_pricing_request(query) is None


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", INCIDENTS)
def test_incident_statements_get_the_incident_reply(query: str) -> None:
    assert detect_incident_request(query) == INCIDENT_REPLY


@pytest.mark.parametrize("query", INFORMATIONAL)
def test_informational_breach_questions_are_not_incidents(query: str) -> None:
    assert detect_incident_request(query) is None


def test_incident_detector_is_bounded() -> None:
    start = time.perf_counter()
    for text in ("we " * 40 + "x", "our " + "a " * 49 + "hacked!", " " * 99 + "x"):
        detect_incident_request(text)
    assert time.perf_counter() - start < 0.5


@pytest.mark.parametrize("query", INCIDENTS)
async def test_orchestrator_answers_an_incident_even_if_pre_checks_block_it(
    monkeypatch, query: str
) -> None:
    _no_decision_call(monkeypatch)
    response, calls, retrieve_calls = await _run(monkeypatch, query, [])
    # Simulate the pre-check flagging it off-topic (the live behaviour).
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: ["off_topic"])
    blocked = await orchestrator.run_query(
        query=query,
        top_k=5,
        filters=None,
        vector_store=None,  # type: ignore[arg-type]
        request_id="r",
        stage_timings={},
    )
    for result in (response, blocked):
        assert result.answer == INCIDENT_REPLY
        assert result.sources == []
        assert result.citations == []
        assert result.outcome == "conversational"
        assert result.grounded is True
    assert calls == []
    assert retrieve_calls == []
