"""Rule-detected clarification (app.harness.ambiguity): the owner's vague
questions ask back (one question or a short series) before the low-confidence
fallback, clear questions are answered, and the answers flow back in."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.config import get_settings
from app.harness import ambiguity, orchestrator
from app.harness.ambiguity import AmbiguityKind, assess
from app.harness.clarify import (
    CLARIFY_OUTCOME,
)
from app.schemas.query import ChatTurn, SourceChunk
from tests.clarify_eval_cases import CLEAR_QUERIES

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


# ---------------------------------------------------------------------------
# Answered directly
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The decision call (borderline requests)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Answering the clarification
# ---------------------------------------------------------------------------
