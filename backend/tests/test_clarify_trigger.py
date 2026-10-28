"""Rule-detected clarification (app.harness.ambiguity): the owner's vague
questions ask back (one question or a short series) before the low-confidence
fallback, clear questions are answered, and the answers flow back in."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from app.api.routes import query as query_route
from app.core import sessions
from app.core.config import get_settings
from app.harness import ambiguity, orchestrator
from app.harness.ambiguity import AmbiguityKind, assess
from app.harness.clarify import (
    CLARIFY_OUTCOME,
    render_clarification_text,
    resolve_clarification_reply,
)
from app.schemas.query import ChatTurn, ClarificationAnswer, SourceChunk
from tests.clarify_eval_cases import CLEAR_QUERIES
from tests.conftest import shared_session_factory

settings = get_settings()

USAGE = {
    "prompt_tokens": 120,
    "completion_tokens": 30,
    "total_tokens": 150,
    "reasoning_tokens": 0,
}

COST = "How much does it cost?"
AUDIT = (
    "We have an audit coming up and need to get compliant fast. "
    "What should we do and how long will it take?"
)
COMPARE = "Is it better than the other one for our cloud setup?"
CONTROL = "What does Essential Eight maturity level 2 require?"
BORDERLINE = "How long will it take to get compliant on Microsoft 365?"

# Real knowledge-base documents (backend/data/real_docs) with the low, close
# cosine scores a vague question gets: evidence spread over many documents.
_DOCS: tuple[tuple[str, str, float, str], ...] = (
    (
        "doc-e8",
        "e8-service-page.md",
        0.31,
        "DeployIQ delivers the ACSC Essential Eight through secure, phased "
        "implementation across all maturity levels, producing audit-ready outcomes.",
    ),
    (
        "doc-cis",
        "cis-benchmark-assessment.md",
        0.30,
        "The CIS Microsoft 365 Foundations Benchmark covers identity in Entra ID "
        "and devices in Intune, with evidence for audit.",
    ),
    (
        "doc-iso",
        "iso-27001-technical-controls.md",
        0.29,
        "We apply the technical aspects of ISO 27001 within your Microsoft 365 "
        "environment and prioritise the gaps.",
    ),
    (
        "doc-zt",
        "zero-trust-learn-more.md",
        0.29,
        "Zero Trust across cloud, hybrid and on-premises environments.",
    ),
    (
        "doc-baseline",
        "security-baseline-assessment.md",
        0.28,
        "Understand the current state of your Microsoft 365 environment; the "
        "assessment gives a roadmap within weeks.",
    ),
    (
        "doc-e8-more",
        "e8-learn-more.md",
        0.27,
        "A phased implementation of the Essential Eight delivered in days, not "
        "months, to uplift maturity.",
    ),
    (
        "doc-home",
        "home-page.md",
        0.26,
        "DeployIQ is Exigo Tech's deployment methodology for Microsoft 365 security.",
    ),
)


def _sources(scale: float = 1.0) -> list[SourceChunk]:
    return [
        SourceChunk(
            chunk_id=str(uuid.uuid4()),
            document_id=doc,
            content=content,
            score=round(score * scale, 4),
            metadata={"source_name": name},
            embedding=[0.05] * 8,
        )
        for doc, name, score, content in _DOCS
    ]


_RETRIEVED = {d[0] for d in _DOCS}


async def _run(
    monkeypatch,
    query: str,
    replies: list[str] | None = None,
    *,
    sources: list[SourceChunk] | None = None,
    grounded: bool = True,
    **kwargs: Any,
):
    calls: list[dict[str, Any]] = []
    retrieve_calls: list[dict[str, Any]] = []
    answers = iter(replies or [])

    async def fake_retrieve(*_a, **k):
        retrieve_calls.append(k)
        return sources if sources is not None else _sources()

    async def fake_call_llm(q, chunks, _history=None, **kw):
        calls.append({"query": q, "chunks": chunks, **kw})
        return next(answers), dict(USAGE)

    async def fake_post_checks(*_a, **_k):
        return grounded, 0.9 if grounded else 0.1, [] if grounded else ["ungrounded"]

    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])
    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post_checks)
    response = await orchestrator.run_query(
        query=query,
        top_k=5,
        filters=None,
        vector_store=None,
        request_id="req-trigger",
        stage_timings={},
        **kwargs,
    )
    return response, calls, retrieve_calls


def _no_decision_call(monkeypatch) -> None:
    async def fail(*_a, **_k):
        raise AssertionError("no decision LLM call expected")

    monkeypatch.setattr(ambiguity, "_complete", fail)


def _assert_grounded(question) -> None:
    for option in question.options:
        if option.label == "Not sure, help me choose":
            continue  # a deliberate no-document escape hatch
        assert option.document_ids
        assert set(option.document_ids) <= _RETRIEVED


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def test_owner_cases_are_detected() -> None:
    assert assess(COST, None).kind is AmbiguityKind.REFERENT
    audit = assess(AUDIT, None)
    assert audit.kind is AmbiguityKind.SLOTS
    assert audit.missing_slots == ("framework", "maturity", "deadline", "environment")
    assert assess(COMPARE, None).kind is AmbiguityKind.COMPARISON


@pytest.mark.parametrize(
    "query",
    [
        CONTROL,
        "do you? sell?",
        "Is it possible to integrate Sentinel with DeployIQ?",
        "We have an Essential Eight audit next month, what should we do?",
        "Is Essential Eight better than ISO 27001 for us?",
        "How much does the CIS Benchmark assessment cost?",
        *CLEAR_QUERIES,
    ],
)
def test_clear_questions_are_not_asked_back(query: str) -> None:
    assert assess(query, None).kind is AmbiguityKind.NONE


def test_referent_is_resolved_from_history() -> None:
    history = [
        ChatTurn(role="user", content="Tell me about Essential Eight"),
        ChatTurn(role="assistant", content="Essential Eight is...", outcome="answered"),
    ]
    assert assess(COST, history).kind is AmbiguityKind.NONE
    # A fallback that merely lists topics names no subject of the visitor's.
    fallback = [
        ChatTurn(role="user", content="hmm"),
        ChatTurn(
            role="assistant",
            content="I can help with Zero Trust and Essential Eight.",
            outcome="ungrounded",
        ),
    ]
    assert assess(COST, fallback).kind is AmbiguityKind.REFERENT
    # One named subject is not enough to compare against "the other one".
    assert assess(COMPARE, history).kind is AmbiguityKind.COMPARISON


def test_guards_reply_streak_and_flag(monkeypatch) -> None:
    assert assess(COST, None, clarification_reply=True).kind is AmbiguityKind.NONE
    streak = [
        ChatTurn(role="user", content="x"),
        ChatTurn(role="assistant", content="q?", outcome=CLARIFY_OUTCOME),
        ChatTurn(role="user", content="y"),
        ChatTurn(role="assistant", content="q?", outcome=CLARIFY_OUTCOME),
    ]
    assert assess(COST, streak).kind is AmbiguityKind.NONE
    monkeypatch.setattr(settings, "clarify_enabled", False)
    assert assess(COST, None).kind is AmbiguityKind.NONE


# ---------------------------------------------------------------------------
# Orchestrator: the owner's cases
# ---------------------------------------------------------------------------


async def test_cost_asks_one_service_question(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, retrieve_calls = await _run(monkeypatch, COST)
    assert response.outcome == CLARIFY_OUTCOME
    assert calls == []  # no generation call at all
    clarification = response.clarification
    assert clarification is not None
    assert len(clarification.questions) == 1
    question = clarification.questions[0]
    assert question.header == "Service"
    assert "pricing" in question.question
    labels = [o.label for o in question.options]
    assert labels == ["Essential Eight", "CIS Benchmark", "ISO 27001", "Zero Trust"]
    _assert_grounded(question)
    assert set(question.options[0].document_ids) == {"doc-e8", "doc-e8-more"}
    assert (
        retrieve_calls[0]["max_documents"] == settings.clarify_retrieval_max_documents
    )


async def test_cost_asks_before_the_low_confidence_fallback(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    # Best score 0.14 < min_retrieval_score: still asked, not "couldn't find".
    response, calls, _ = await _run(monkeypatch, COST, sources=_sources(0.45))
    assert max(s.score for s in _sources(0.45)) < settings.min_retrieval_score
    assert response.outcome == CLARIFY_OUTCOME
    assert calls == []


async def test_below_the_floor_is_still_off_topic(monkeypatch) -> None:
    response, calls, _ = await _run(monkeypatch, COST, sources=_sources(0.2))
    assert response.outcome == "blocked"
    assert response.guardrail_flags == ["off_topic"]
    assert calls == []


async def test_audit_request_asks_a_grounded_series(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(monkeypatch, AUDIT)
    assert response.outcome == CLARIFY_OUTCOME
    assert calls == []
    clarification = response.clarification
    assert clarification is not None
    questions = clarification.questions
    assert 2 <= len(questions) <= 4  # noqa: PLR2004
    assert [q.header for q in questions] == [
        "Framework",
        "Maturity",
        "Deadline",
        "Environment",
    ]
    assert [q.id for q in questions] == ["Q1", "Q2", "Q3", "Q4"]
    assert questions[0].question == "Which framework is the audit against?"
    # Built from the whole catalogue (3 shown + "Not sure": the card holds 4).
    assert [o.label for o in questions[0].options] == [
        "Essential Eight",
        "ISO 27001",
        "CIS Benchmark",
        "Not sure, help me choose",
    ]
    environment = questions[3]
    assert environment.multi_select is True
    assert [o.label for o in environment.options] == [
        "Microsoft 365",
        "Entra ID and Intune",
        "Hybrid or on-premises",
    ]
    for question in questions:
        _assert_grounded(question)
    # The preamble lists the whole catalogue and never claims it is all.
    assert "DeployIQ helps with Essential Eight, ISO 27001, CIS Benchmark" in (
        clarification.preamble
    )
    assert "Security Baseline" in clarification.preamble
    assert "covers" not in clarification.preamble
    # Plain-text clients get every question.
    assert "4. Which environment is in scope?" in response.answer


async def test_question_cap_bounds_the_series(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    monkeypatch.setattr(settings, "clarify_max_questions", 2)
    response, _, _ = await _run(monkeypatch, AUDIT)
    assert response.clarification is not None
    assert [q.header for q in response.clarification.questions] == [
        "Framework",
        "Maturity",
    ]


async def test_slot_without_evidence_is_not_asked(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    plain = [s.model_copy(update={"content": "DeployIQ services."}) for s in _sources()]
    response, _, _ = await _run(monkeypatch, AUDIT, sources=plain)
    assert response.clarification is not None
    # Slot questions are intent choices: they need no document evidence.
    assert response.clarification.questions[0].header == "Framework"
    assert len(response.clarification.questions) > 1


async def test_comparison_asks_what_to_compare(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(monkeypatch, COMPARE)
    assert response.outcome == CLARIFY_OUTCOME
    assert calls == []
    assert response.clarification is not None
    (question,) = response.clarification.questions
    assert question.header == "Compare"
    assert question.multi_select is True
    assert "compare" in question.question
    assert len(question.options) >= 2  # noqa: PLR2004
    _assert_grounded(question)


# ---------------------------------------------------------------------------
# Answered directly
# ---------------------------------------------------------------------------


async def test_control_is_answered_directly(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, retrieve_calls = await _run(
        monkeypatch, CONTROL, ["ML2 requires..."]
    )
    assert response.outcome == "answered"
    assert response.clarification is None
    assert "clarify_block" not in calls[0]
    assert "max_documents" not in retrieve_calls[0]


async def test_do_you_sell_stays_direct(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(monkeypatch, "do you? sell?", ["We sell services."])
    assert response.outcome == "answered"
    assert "clarify_block" not in calls[0]


async def test_referent_with_history_is_answered(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    history = [
        ChatTurn(role="user", content="Tell me about Essential Eight"),
        ChatTurn(role="assistant", content="Essential Eight is...", outcome="answered"),
    ]
    response, calls, retrieve_calls = await _run(
        monkeypatch, COST, ["Pricing is not published."], history=history
    )
    assert response.outcome == "answered"
    assert "clarify_block" not in calls[0]
    assert retrieve_calls[0]["history"] == history


async def test_vague_short_ungrounded_answer_asks_instead(monkeypatch) -> None:
    reply = json.dumps(
        {"action": "answer", "answer": "Plans vary.", "preamble": "", "questions": []}
    )
    # ("plans?" and "pricing?" are rule-path questions; a bare topic still
    # goes through the model gate and, naming nothing, falls back to the goal
    # card rather than "which service?".)
    response, calls, _ = await _run(monkeypatch, "security?", [reply], grounded=False)
    assert len(calls) == 1
    assert response.outcome == CLARIFY_OUTCOME
    assert response.clarification is not None
    question = response.clarification.questions[0]
    assert question.header == "Goal"
    assert question.question == "What are you hoping to get done?"
    assert response.prompt_tokens == USAGE["prompt_tokens"]


# ---------------------------------------------------------------------------
# The decision call (borderline requests)
# ---------------------------------------------------------------------------


async def test_borderline_request_is_confirmed_by_one_cheap_call(monkeypatch) -> None:
    assert assess(BORDERLINE, None).missing_slots == ("framework", "maturity")
    seen: list[list[dict[str, str]]] = []

    async def complete(messages):
        seen.append(messages)
        payload = {"action": "clarify", "slots": ["framework", "maturity", "bogus"]}
        return json.dumps(payload), {"prompt_tokens": 80, "completion_tokens": 10}

    monkeypatch.setattr(ambiguity, "_complete", complete)
    response, calls, _ = await _run(monkeypatch, BORDERLINE)
    assert len(seen) == 1
    assert calls == []
    assert response.outcome == CLARIFY_OUTCOME
    assert response.clarification is not None
    assert [q.header for q in response.clarification.questions] == [
        "Framework",
        "Maturity",
    ]
    assert response.prompt_tokens == 80  # noqa: PLR2004
    assert response.token_details is not None
    assert response.token_details["clarify_decision"]["completion_tokens"] == 10  # noqa: PLR2004


async def test_decision_may_answer(monkeypatch) -> None:
    async def complete(_messages):
        usage = {"prompt_tokens": 80, "completion_tokens": 10}
        return json.dumps({"action": "answer", "slots": []}), usage

    monkeypatch.setattr(ambiguity, "_complete", complete)
    response, calls, _ = await _run(monkeypatch, BORDERLINE, ["Typically weeks."])
    assert response.outcome == "answered"
    assert len(calls) == 1
    # The decision call is billed with the answer.
    assert response.prompt_tokens == USAGE["prompt_tokens"] + 80
    assert response.token_details is not None
    assert response.token_details["clarify_decision"]["prompt_tokens"] == 80  # noqa: PLR2004


async def test_decision_timeout_falls_back_to_an_answer(monkeypatch) -> None:
    async def slow(_messages):
        await asyncio.sleep(5)
        return json.dumps({"action": "clarify", "slots": ["framework"]}), None

    monkeypatch.setattr(ambiguity, "_complete", slow)
    monkeypatch.setattr(settings, "clarify_decision_timeout_seconds", 0.05)
    response, calls, _ = await _run(monkeypatch, BORDERLINE, ["Typically weeks."])
    assert response.outcome == "answered"
    assert response.answer == "Typically weeks."
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Answering the clarification
# ---------------------------------------------------------------------------


async def test_follow_up_after_the_series_is_a_grounded_answer(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    first, _, _ = await _run(monkeypatch, AUDIT)
    clarification = first.clarification
    assert clarification is not None
    history = [
        ChatTurn(role="user", content=AUDIT),
        ChatTurn(
            role="assistant",
            content=render_clarification_text(clarification),
            outcome=CLARIFY_OUTCOME,
            clarification=clarification,
        ),
    ]
    answers = [
        ClarificationAnswer(question_id="Q1", option_ids=["O1"]),
        ClarificationAnswer(question_id="Q2", option_ids=["O2"]),
        ClarificationAnswer(question_id="Q3", option_ids=["O1"]),
        ClarificationAnswer(question_id="Q4", option_ids=["O1", "O2"]),
    ]
    resolved = resolve_clarification_reply("ignored", history, answers)
    assert resolved is not None
    assert resolved.query.endswith(
        "(Framework: Essential Eight; Maturity: Partly implemented; "
        "Deadline: Within a month; Environment: Microsoft 365 and Entra ID and Intune)"
    )
    response, calls, _ = await _run(
        monkeypatch,
        resolved.query,
        ["Start with a gap assessment, then a phased uplift over days."],
        history=history,
        boost_document_ids=resolved.document_ids,
        clarification_reply=True,
    )
    assert response.outcome == "answered"
    assert response.grounded is True
    assert response.clarification is None
    assert "clarify_block" not in calls[0]
    assert calls[0]["chunks"][0].document_id in {"doc-e8", "doc-e8-more"}


@pytest.mark.db
async def test_round_trip_cost_then_pick_a_service(
    client, db_session, fake_llm, monkeypatch
) -> None:
    actor = f"trigger-{uuid.uuid4().hex[:8]}"
    headers = {"X-Client-Id": actor}

    async def fake_retrieve(*_a, **_k):
        return _sources()

    async def fake_post_checks(*_a, **_k):
        return True, 0.9, []

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post_checks)
    monkeypatch.setattr(
        sessions, "AsyncSessionLocal", shared_session_factory(db_session)
    )
    monkeypatch.setattr(query_route, "schedule_online_evaluation", lambda **_k: None)
    monkeypatch.setattr(settings, "rate_limit_exempt_actors", [actor])

    first = await client.post("/query", json={"query": COST}, headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["outcome"] == CLARIFY_OUTCOME
    assert fake_llm["calls"] == []
    options = body["clarification"]["questions"][0]["options"]
    assert options[0]["label"] == "Essential Eight"

    await sessions.drain_pending_writes(actor)
    fake_llm["set_response"]("Essential Eight pricing is quoted per engagement.", USAGE)
    second = await client.post(
        "/query",
        json={
            "query": "Service: Essential Eight",
            "session_id": body["session_id"],
            "clarification_answers": [{"question_id": "Q1", "option_ids": ["O1"]}],
        },
        headers=headers,
    )
    assert second.status_code == 200, second.text
    answer = second.json()
    assert answer["outcome"] == "answered"
    assert fake_llm["calls"][-1]["prompt"] == f"{COST} (Service: Essential Eight)"
    assert fake_llm["calls"][-1]["context"][0].document_id in {"doc-e8", "doc-e8-more"}
