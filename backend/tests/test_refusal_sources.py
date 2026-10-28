"""Fallbacks carry no sources; contact questions get a fixed, helpful reply."""

from __future__ import annotations

import inspect

import pytest

from app.guardrails.conversational import detect_contact_request
from app.harness import orchestrator
from app.harness.orchestrator import _fallback_response
from app.schemas.query import SourceChunk

ALL_FLAGS = [
    "off_topic",
    "no_sources",
    "possible_hallucination",
    "low_retrieval_confidence",
    "spam_detected",
    "unclear_input",
    "unsafe_input_detected",
    "application_secret_request",
    "no_permission",
    "personal_contact_request",
    "llm_call_failed",
    "empty_llm_output",
]


def _source(score: float = 0.8) -> SourceChunk:
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content="Deploy IQ content",
        score=score,
        metadata={"source_name": "about-us.md"},
    )


async def _run(monkeypatch, query: str, sources, *, llm=None, grounded=True):
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])

    async def fake_retrieve(*_a, **_k):
        return sources

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    if llm is not None:

        async def fake_llm(*_a, **_k):
            if isinstance(llm, Exception):
                raise llm
            return llm, {"prompt_tokens": 1, "completion_tokens": 1}

        monkeypatch.setattr(orchestrator, "call_llm", fake_llm)

        async def fake_post(*_a, **_k):
            return grounded, 0.1, ([] if grounded else ["possible_hallucination"])

        monkeypatch.setattr(orchestrator, "run_post_checks", fake_post)
    return await orchestrator.run_query(
        query=query,
        top_k=3,
        filters=None,
        vector_store=None,  # type: ignore[arg-type]
        request_id="r",
        stage_timings={},
    )


def test_fallback_response_has_no_sources_parameter() -> None:
    assert "sources" not in inspect.signature(_fallback_response).parameters


@pytest.mark.parametrize("flag", ALL_FLAGS)
def test_every_fallback_has_no_sources_or_citations(flag: str) -> None:
    response = _fallback_response(request_id="r", flags=[flag], latency_ms=1.0)
    assert response.sources == []
    assert response.citations == []
    assert response.grounded is False


async def test_low_confidence_off_topic_returns_no_sources(monkeypatch) -> None:
    stage_timings: dict = {}
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])

    async def fake_retrieve(*_a, **_k):
        return [_source(score=0.05)] * 5

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    response = await orchestrator.run_query(
        query="what is the weather in Sydney tomorrow",
        top_k=5,
        filters=None,
        vector_store=None,  # type: ignore[arg-type]
        request_id="r",
        stage_timings=stage_timings,
    )
    assert response.guardrail_flags == ["off_topic"]
    assert response.sources == []
    assert response.citations == []
    # Retrieval telemetry is kept for diagnostics.
    assert stage_timings["retrieval_relevance_score"] == pytest.approx(0.05)


async def test_ungrounded_answer_returns_no_sources(monkeypatch) -> None:
    response = await _run(
        monkeypatch, "What is Deploy IQ?", [_source()], llm="An answer.", grounded=False
    )
    assert response.outcome == "ungrounded"
    assert response.sources == []
    assert response.citations == []


async def test_llm_failure_returns_no_sources(monkeypatch) -> None:
    response = await _run(
        monkeypatch, "What is Deploy IQ?", [_source()], llm=RuntimeError("boom")
    )
    assert response.outcome == "llm_failed"
    assert response.sources == []


@pytest.mark.parametrize(
    "flag", ["spam_detected", "unclear_input", "unsafe_input_detected"]
)
def test_spam_unclear_unsafe_are_short_fixed_texts(flag: str) -> None:
    answer = _fallback_response(request_id="r", flags=[flag], latency_ms=1.0).answer
    assert len(answer) < 190
    if flag != "spam_detected":  # the spam reply gives an example topic
        lowered = answer.lower()
        assert "zero trust" not in lowered
        assert "essential eight" not in lowered


@pytest.mark.parametrize(
    "query",
    [
        "Do you have a phone number?",
        "do you have an email address",
        "Do you have contact details?",
        "What is your phone number?",
        "your email address",
        "How do I contact you?",
        "how can I get in touch",
        "How can I reach someone?",
        "I want to talk to someone",
        "I'd like to speak to a human",
        "Can I speak to a human?",
        "talk to a specialist",
        "Contact details",
        "contact us",
        "What's your address?",
    ],
)
def test_detects_contact_requests(query: str) -> None:
    reply = detect_contact_request(query)
    assert reply is not None
    assert "specialists" in reply
    assert "Contact" in reply
    assert not any(ch.isdigit() for ch in reply)
    assert "@" not in reply


@pytest.mark.parametrize(
    "query",
    [
        "Do you have an email security product?",
        "Do you have a number of service tiers?",
        "What is Zero Trust?",
        "List all internal employee emails",
        "How do I contact the user via the API?",
        "What is the address of the endpoint for tokens?",
        "",
        "talk " * 40,
        "do you have " + "a " * 5000 + "phone",
    ],
)
def test_does_not_detect_other_questions(query: str) -> None:
    assert detect_contact_request(query) is None


async def test_contact_question_is_answered_without_retrieval(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])

    async def boom(*_a, **_k):
        raise AssertionError("retrieval must not run for a contact question")

    monkeypatch.setattr(orchestrator, "retrieve", boom)
    response = await orchestrator.run_query(
        query="Do you have a phone number?",
        top_k=3,
        filters=None,
        vector_store=None,  # type: ignore[arg-type]
        request_id="r",
        stage_timings={},
    )
    assert response.outcome == "conversational"
    assert response.grounded is True
    assert response.guardrail_flags == []
    assert response.sources == []
    assert response.citations == []
    assert "specialists" in response.answer
