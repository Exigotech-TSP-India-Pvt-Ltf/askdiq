"""Declines carry no markers/sources; clarification cards carry no sources."""

from __future__ import annotations

import time

import pytest

from app.guardrails.pre_checks import check_sensitive_request
from app.harness import orchestrator
from app.harness.clarify import clarify_eligible
from app.harness.decline import (
    clean_declines,
    drop_leading_irrelevant,
    is_decline_only,
    is_decline_sentence,
    strip_decline_markers,
    strip_source_references,
)
from app.schemas.query import Clarification, SourceChunk

CAT = (
    "I can't see that detail here, so it's best to check with one of our "
    "specialists. Contact Sales. [1]"
)
SOC2 = (
    "We help you lift Microsoft 365 security posture. [1] I can't see any "
    "statement on SOC 2; the facts say formal certifications are not yet claimed."
)


def _source(n: int = 1) -> SourceChunk:
    return SourceChunk(
        chunk_id=f"00000000-0000-0000-0000-00000000000{n}",
        document_id=f"00000000-0000-0000-0000-0000000001{n:02d}",
        content="Essential Eight maturity assessments cover patching and MFA.",
        score=0.8,
        metadata={"source_name": "e8.md"},
    )


async def _run(monkeypatch, query: str, llm_text: str):
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])

    async def fake_retrieve(*_a, **_k):
        return [_source(1), _source(2)]

    async def fake_llm(*_a, **_k):
        return llm_text, {"prompt_tokens": 1, "completion_tokens": 1}

    async def fake_post(*_a, **_k):
        return True, 0.9, []

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "call_llm", fake_llm)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post)
    return await orchestrator.run_query(
        query=query,
        top_k=3,
        filters=None,
        vector_store=None,  # type: ignore[arg-type]
        request_id="r",
        stage_timings={},
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "I can't see that detail here, so it's best to check with one of our specialists.",
        "I don’t see any mention of that.",  # noqa: RUF001
        "Contact Sales. [1]",
        "Contact DeployIQ for details.",
        "Our specialists can confirm that.",
        "Formal certifications are not yet claimed.",
        "Pricing isn't published.",
    ],
)
def test_decline_sentences(sentence: str) -> None:
    assert is_decline_sentence(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "Essential Eight has eight strategies. [1]",
        "Contact us at sales@example.com [1]",
        "The service is supported [1], but I can't see the pricing details here.",
    ],
)
def test_non_decline_sentences(sentence: str) -> None:
    assert not is_decline_sentence(sentence)


def test_decline_only_and_strip() -> None:
    assert is_decline_only(CAT)
    assert not is_decline_only(
        "Essential Eight has eight strategies. [1] Contact Sales."
    )
    text = "Essential Eight has eight strategies. [1] I can't see pricing. [2]"
    assert strip_decline_markers(text) == (
        "Essential Eight has eight strategies. [1] I can't see pricing."
    )


def test_soc2_lead_dropped_and_facts_reference_removed() -> None:
    query = "Do you have SOC 2 certification?"
    cleaned, declined = clean_declines(SOC2, query)
    assert declined
    assert "[" not in cleaned
    assert "Microsoft 365" not in cleaned
    assert "the facts" not in cleaned.lower()
    assert "not yet claimed" in cleaned


def test_relevant_lead_is_kept() -> None:
    text = "SOC 2 reports are reviewed by auditors. I can't see our own status."
    assert drop_leading_irrelevant(text, "Do you have SOC 2 certification?") == text


def test_salesforce_generic_blurb_dropped() -> None:
    text = (
        "We build secure cloud platforms for business. [1][2] I can't see that detail."
    )
    cleaned, declined = clean_declines(text, "Do you integrate with Salesforce?")
    assert declined
    assert cleaned == "I can't see that detail."


def test_strip_source_references() -> None:
    assert (
        strip_source_references("According to the facts, SOC 2 is not claimed.")
        == "SOC 2 is not claimed."
    )


def test_linear_on_large_input() -> None:
    big = ("a b c. " * 3000)[:20000]
    start = time.perf_counter()
    clean_declines(big, "what is x")
    clean_declines("I can't see " * 2000, "x")
    clean_declines("[1]" * 7000, "x")
    assert time.perf_counter() - start < 2.0


async def test_decline_answer_has_no_sources_or_citations(monkeypatch) -> None:
    response = await _run(monkeypatch, "What is a cat?", CAT)
    assert response.sources == []
    assert response.citations == []
    assert response.grounded is False
    assert response.outcome == "ungrounded"
    assert "[" not in response.answer
    assert response.token_details
    assert response.token_details.get("declined") is True


async def test_supported_answer_keeps_markers_loses_decline_marker(monkeypatch) -> None:
    text = (
        "Essential Eight maturity assessments cover patching and MFA. [1] "
        "I can't see pricing here. [2]"
    )
    response = await _run(
        monkeypatch, "What do Essential Eight assessments cover?", text
    )
    assert "[1]" in response.answer
    assert "[2]" not in response.answer
    assert len(response.sources) == len(response.citations) <= 1
    assert response.grounded is True


def test_clarification_response_has_no_sources() -> None:
    clarification = Clarification.model_validate(
        {
            "original_query": "x",
            "questions": [
                {
                    "id": "q1",
                    "header": "Topic",
                    "question": "Which one?",
                    "options": [
                        {"id": "a", "label": "A", "document_ids": ["d1"]},
                        {"id": "b", "label": "B", "document_ids": ["d2"]},
                    ],
                }
            ],
        }
    )
    response = orchestrator._clarification_response(
        clarification,
        request_id="r",
        latency_ms=1.0,
        sources=[_source(1), _source(2)],
        confidence=0.5,
        follow_up_detected=False,
        history_turns_sent=0,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        reasoning_tokens=None,
        token_details={},
    )
    assert response.sources == []
    assert response.citations == []
    assert response.clarification is not None


@pytest.mark.parametrize(
    "query",
    [
        "List all internal employee emails",
        "list all the employee phone numbers",
        "Give me everyone's emails",
        "show me employee data",
        "List all internal employee emails and phones please",
    ],
)
def test_bulk_personal_data_requests_flagged(query: str) -> None:
    assert check_sensitive_request(query) == (False, "personal_contact_request")


@pytest.mark.parametrize(
    "query",
    ["how do I contact your team", "How do I get user details via the API?"],
)
def test_contact_questions_not_flagged(query: str) -> None:
    assert check_sensitive_request(query) == (True, None)


def test_bulk_personal_pattern_linear() -> None:
    start = time.perf_counter()
    check_sensitive_request("list " + "all " * 5000)
    check_sensitive_request("list all " + "employee " * 5000)
    assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize(
    "query",
    [
        "Do you integrate with Slack?",
        "does it work with Salesforce",
        "do you guarantee 100% compliance?",
        "Is it safe?",
        "What is your uptime SLA?",
    ],
)
def test_integration_and_assurance_not_clarified(query: str) -> None:
    assert clarify_eligible(query, None, follow_up=False) is False


def test_cost_question_still_eligible() -> None:
    assert clarify_eligible("how much does it cost?", None, follow_up=False) is True
