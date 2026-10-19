"""Clarifying questions (outcome ``clarify``): decision rules, multi-question
generation and server-side validation, answer rewriting, the orchestrator
path and the /query round trip."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.api.routes import query as query_route
from app.core import sessions
from app.core.config import get_settings
from app.governance.outcomes import ALL_OUTCOMES, GENERATION_OUTCOMES, derive_outcome
from app.harness import orchestrator
from app.harness.clarify import (
    ANSWER_OR_CLARIFY,
    CLARIFY_OUTCOME,
    boost_documents,
    clarification_streak,
    clarify_eligible,
    content_word_count,
    parse_decision,
    plan_clarification,
    render_clarification_text,
    resolve_clarification_reply,
    select_candidates,
)
from app.insights.rules import map_outcome
from app.insights.taxonomy import BotQuality
from app.schemas.query import (
    ChatTurn,
    Clarification,
    ClarificationAnswer,
    QueryResponse,
    SourceChunk,
)
from tests.clarify_eval_cases import AMBIGUOUS_QUERIES, CLEAR_QUERIES
from tests.conftest import shared_session_factory

settings = get_settings()

USAGE = {
    "prompt_tokens": 120,
    "completion_tokens": 30,
    "total_tokens": 150,
    "reasoning_tokens": 0,
}


def _src(
    doc: str, score: float, *, name: str, section: str | None = None
) -> SourceChunk:
    metadata: dict[str, Any] = {"source_name": name}
    if section:
        metadata["section_heading"] = section
    return SourceChunk(
        chunk_id=str(uuid.uuid4()),
        document_id=doc,
        content=f"{name} {section or ''} content about the offering",
        score=score,
        metadata=metadata,
        embedding=[0.05] * 8,
    )


def _spread_sources() -> list[SourceChunk]:
    return [
        _src("doc-svc", 0.62, name="services.md", section="Zero Trust Professional"),
        _src("doc-price", 0.60, name="pricing.md", section="Plans"),
        _src("doc-partner", 0.59, name="partner-program.md"),
        _src("doc-svc", 0.40, name="services.md", section="Billing FAQ"),
    ]


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "clarify",
        "answer": "",
        "preamble": "DeployIQ offers several services; which would you like?",
        "questions": [
            {
                "header": "Topic",
                "question": "What would you like to know about buying from DeployIQ",
                "multi_select": False,
                "options": [
                    {
                        "label": "Buy a DeployIQ service",
                        "description": "Zero Trust Professional and what it includes",
                        "source_ids": ["T1"],
                    },
                    {
                        "label": "Pricing and plans",
                        "description": "The published plans",
                        "source_ids": ["T2"],
                    },
                    {
                        "label": "Become a partner",
                        "description": "The partner program",
                        "source_ids": ["T3"],
                    },
                ],
            },
            {
                "header": "Region",
                "question": "Which regions apply?",
                "multi_select": True,
                "options": [
                    {"label": "Australia", "description": "AU", "source_ids": ["T2"]},
                    {"label": "New Zealand", "description": "NZ", "source_ids": ["t1"]},
                ],
            },
        ],
    }
    payload.update(overrides)
    return payload


def _clarification() -> Clarification:
    decision = parse_decision(
        json.dumps(_payload()), select_candidates(_spread_sources()), "do you buy?"
    )
    assert decision.clarification is not None
    return decision.clarification


def _clarify_history() -> list[ChatTurn]:
    clar = _clarification()
    return [
        ChatTurn(role="user", content="do you buy?"),
        ChatTurn(
            role="assistant",
            content=render_clarification_text(clar),
            outcome=CLARIFY_OUTCOME,
            clarification=clar,
        ),
    ]


# ---------------------------------------------------------------------------
# Decision rules + evaluation set
# ---------------------------------------------------------------------------


def test_content_words_ignore_question_scaffolding() -> None:
    assert content_word_count("What is the pricing?") == 1
    assert content_word_count("How do I configure SSO for Entra ID tenants") == 5


def test_streak_counts_trailing_clarifications_only() -> None:
    assert clarification_streak(None) == 0
    history = _clarify_history()
    assert clarification_streak(history) == 1
    answered = [
        *history,
        ChatTurn(role="user", content="1"),
        ChatTurn(role="assistant", content="ok", outcome="answered"),
    ]
    assert clarification_streak(answered) == 0


@pytest.mark.parametrize("query", AMBIGUOUS_QUERIES)
def test_eval_ambiguous_queries_are_eligible(query: str) -> None:
    assert clarify_eligible(query, None, follow_up=False)
    assert plan_clarification(query, _spread_sources(), None, follow_up=False)


@pytest.mark.parametrize("query", CLEAR_QUERIES)
def test_eval_clear_queries_never_clarify(query: str) -> None:
    assert plan_clarification(query, _spread_sources(), None, follow_up=False) == []


def test_eval_sets_have_the_agreed_size() -> None:
    assert 15 <= len(AMBIGUOUS_QUERIES) <= 20
    assert len(CLEAR_QUERIES) >= 10


def test_candidates_are_distinct_topics_near_the_top_score() -> None:
    candidates = select_candidates(_spread_sources())
    assert [c.label for c in candidates] == [
        "Services / Zero Trust Professional",
        "Pricing / Plans",
        "Partner program",
    ]
    assert [c.id for c in candidates] == ["T1", "T2", "T3"]
    assert all(c.excerpt for c in candidates)


def test_one_document_is_not_ambiguity() -> None:
    same_doc = [
        _src("d1", 0.7, name="a.md", section="Overview"),
        _src("d1", 0.69, name="a.md", section="Setup"),
    ]
    assert plan_clarification("setup", same_doc, None, follow_up=False) == []


def test_plan_respects_consecutive_cap_follow_up_and_flag(monkeypatch) -> None:
    history = _clarify_history()
    monkeypatch.setattr(settings, "clarify_max_consecutive", 1)
    assert not clarify_eligible("plans", history, follow_up=False)
    monkeypatch.setattr(settings, "clarify_max_consecutive", 2)
    assert not clarify_eligible("pricing", history, follow_up=True)
    monkeypatch.setattr(settings, "clarify_enabled", False)
    assert not clarify_eligible("pricing", None, follow_up=False)


def test_schema_is_strict_json() -> None:
    schema = ANSWER_OR_CLARIFY["json_schema"]
    assert schema["strict"] is True
    option = schema["schema"]["properties"]["questions"]["items"]["properties"][
        "options"
    ]["items"]
    assert option["required"] == ["label", "description", "source_ids"]
    assert option["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Generation + server-side validation
# ---------------------------------------------------------------------------


def test_multi_question_payload_is_validated_and_grounded() -> None:
    clar = _clarification()
    assert clar.preamble.startswith("DeployIQ offers several services")
    assert [q.id for q in clar.questions] == ["Q1", "Q2"]
    first, second = clar.questions
    assert first.header == "Topic"
    assert first.question.endswith("?")
    assert first.multi_select is False
    assert [o.id for o in first.options] == ["O1", "O2", "O3"]
    assert first.options[0].document_ids == ["doc-svc"]
    assert first.options[0].sources == ["services.md"]
    assert second.multi_select is True
    # Lower-case topic ids are accepted.
    assert second.options[1].document_ids == ["doc-svc"]


def test_ungrounded_options_and_questions_are_dropped(monkeypatch) -> None:
    monkeypatch.setattr(settings, "clarify_max_questions", 3)
    payload = _payload()
    payload["questions"][0]["options"][1]["source_ids"] = ["T9"]  # not retrieved
    payload["questions"][1]["options"][0]["source_ids"] = []  # no source
    payload["questions"][0]["header"] = "A very long header"
    payload["questions"].append(dict(payload["questions"][0]))  # duplicate
    decision = parse_decision(
        json.dumps(payload), select_candidates(_spread_sources()), "do you buy?"
    )
    clar = decision.clarification
    assert clar is not None
    assert len(clar.questions) == 1  # Q2 left with one option, duplicate dropped
    assert [o.label for o in clar.questions[0].options] == [
        "Buy a DeployIQ service",
        "Become a partner",
    ]
    assert len(clar.questions[0].header) <= 12


def test_question_cap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "clarify_max_questions", 1)
    clar = _clarification()
    assert len(clar.questions) == 1


def test_nothing_grounded_means_answer_directly() -> None:
    payload = _payload()
    for question in payload["questions"]:
        for option in question["options"]:
            option["source_ids"] = ["T9"]
    decision = parse_decision(
        json.dumps(payload), select_candidates(_spread_sources()), "do you buy?"
    )
    assert decision.clarification is None
    assert decision.answer == ""


def test_answer_action_and_plain_text() -> None:
    candidates = select_candidates(_spread_sources())
    answer = json.dumps(
        {
            "action": "answer",
            "answer": "Plans start at $10.",
            "preamble": "",
            "questions": [],
        }
    )
    assert parse_decision(answer, candidates, "q").answer == "Plans start at $10."
    assert parse_decision("Plain text answer.", candidates, "q").answer == (
        "Plain text answer."
    )


def test_text_fallback_lists_every_question() -> None:
    text = render_clarification_text(_clarification())
    assert text.startswith("DeployIQ offers several services")
    assert "1. What would you like to know about buying from DeployIQ?" in text
    assert "- Buy a DeployIQ service: Zero Trust Professional" in text
    assert "2. Which regions apply?" in text


def test_legacy_single_question_rows_still_load() -> None:
    legacy = Clarification.model_validate(
        {
            "question": "Which one?",
            "original_query": "pricing",
            "options": [
                {"id": "T1", "label": "A", "document_id": "d1", "source_name": "a.md"},
                {"id": "T2", "label": "B"},
            ],
        }
    )
    assert legacy.questions[0].question == "Which one?"
    assert legacy.questions[0].options[0].document_ids == ["d1"]


# ---------------------------------------------------------------------------
# Answers -> one explicit query
# ---------------------------------------------------------------------------


def test_structured_answers_are_rewritten_into_one_query() -> None:
    answers = [
        ClarificationAnswer(question_id="Q1", option_ids=["O2", "O3"]),
        ClarificationAnswer(question_id="Q2", option_ids=["O1"], other_text="Fiji"),
        ClarificationAnswer(question_id="Q9", option_ids=["O1"]),
    ]
    resolved = resolve_clarification_reply("Pricing", _clarify_history(), answers)
    assert resolved is not None
    # Single select keeps one option; multi select keeps all plus "Other".
    assert resolved.query == (
        "do you buy? (Topic: Pricing and plans; Region: Australia and Fiji)"
    )
    assert resolved.document_ids == ["doc-price"]
    assert [a["question_id"] for a in resolved.answers] == ["Q1", "Q2"]


def test_single_select_other_replaces_the_option() -> None:
    answers = [
        ClarificationAnswer(question_id="Q1", option_ids=["O1"], other_text="Audits")
    ]
    resolved = resolve_clarification_reply("x", _clarify_history(), answers)
    assert resolved is not None
    assert resolved.query == "do you buy? (Topic: Audits)"


def test_plain_text_reply_matches_option_names() -> None:
    resolved = resolve_clarification_reply(
        "Become a partner, Australia", _clarify_history()
    )
    assert resolved is not None
    assert resolved.query == (
        "do you buy? (Topic: Become a partner; Region: Australia)"
    )


def test_short_free_text_refines_but_new_questions_do_not() -> None:
    history = _clarify_history()
    refined = resolve_clarification_reply("for MSP partners", history)
    assert refined is not None and refined.free_text is True
    assert resolve_clarification_reply("What is Zero Trust?", history) is None
    assert resolve_clarification_reply("thanks", history) is None
    assert resolve_clarification_reply("2", None) is None


def test_boost_puts_chosen_documents_first() -> None:
    sources = _spread_sources()
    boosted = boost_documents(sources, ["doc-partner"])
    assert boosted[0].document_id == "doc-partner"
    assert boost_documents(sources, None) is sources


# ---------------------------------------------------------------------------
# Outcome plumbing
# ---------------------------------------------------------------------------


def test_clarify_is_a_known_non_generation_outcome() -> None:
    assert "clarify" in ALL_OUTCOMES
    assert "clarify" not in GENERATION_OUTCOMES
    assert derive_outcome(outcome_hint="clarify", prompt_tokens=100) == "clarify"
    mapping = map_outcome("clarify", True, frozenset())
    assert mapping.bot_quality == BotQuality.CLAR
    assert mapping.handled is False


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def _run(monkeypatch, query: str, replies: list[str], **kwargs: Any):
    calls: list[dict[str, Any]] = []
    retrieve_calls: list[dict[str, Any]] = []
    answers = iter(replies)

    async def fake_retrieve(*_a, **k):
        retrieve_calls.append(k)
        return _spread_sources()

    async def fake_call_llm(q, chunks, _history=None, **kw):
        calls.append({"query": q, "chunks": chunks, **kw})
        return next(answers), dict(USAGE)

    async def fake_post_checks(*_a, **_k):
        return True, 0.9, []

    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])
    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post_checks)
    response = await orchestrator.run_query(
        query=query,
        top_k=5,
        filters=None,
        vector_store=None,
        request_id="req-clarify",
        stage_timings={},
        **kwargs,
    )
    return response, calls, retrieve_calls


async def test_ambiguous_query_returns_questions(monkeypatch) -> None:
    response, calls, retrieve_calls = await _run(
        monkeypatch, "do you buy?", [json.dumps(_payload())]
    )
    assert response.outcome == "clarify"
    assert response.clarification is not None
    assert len(response.clarification.questions) == 2
    assert "Buy a DeployIQ service" in response.answer
    assert response.grounded is True
    assert response.prompt_tokens == USAGE["prompt_tokens"]
    assert response.token_details is not None
    assert response.token_details["clarification"]["original_query"] == "do you buy?"
    # Dedicated brief + strict schema, and a wider retrieval.
    assert "Clarification brief" in calls[0]["clarify_block"]
    assert calls[0]["response_format"] is ANSWER_OR_CLARIFY
    assert retrieve_calls[0]["top_k"] == settings.clarify_retrieval_top_k
    assert (
        retrieve_calls[0]["max_documents"] == settings.clarify_retrieval_max_documents
    )


async def test_model_may_answer_a_gated_query(monkeypatch) -> None:
    reply = json.dumps(
        {
            "action": "answer",
            "answer": "We sell services.",
            "preamble": "",
            "questions": [],
        }
    )
    response, calls, _ = await _run(monkeypatch, "do you buy?", [reply])
    assert response.outcome == "answered"
    assert response.answer == "We sell services."
    assert len(calls) == 1


async def test_ungrounded_clarification_falls_back_to_an_answer(monkeypatch) -> None:
    payload = _payload()
    for question in payload["questions"]:
        for option in question["options"]:
            option["source_ids"] = []
    response, calls, _ = await _run(
        monkeypatch, "do you buy?", [json.dumps(payload), "Direct answer."]
    )
    assert response.outcome == "answered"
    assert response.answer == "Direct answer."
    assert "clarify_block" not in calls[1]


async def test_clear_query_is_never_gated(monkeypatch) -> None:
    response, calls, retrieve_calls = await _run(
        monkeypatch,
        "How are the Platform plans priced for annual enterprise contracts",
        ["Annual contracts are priced per seat."],
    )
    assert response.outcome == "answered"
    assert "clarify_block" not in calls[0]
    assert "max_documents" not in retrieve_calls[0]


async def test_boosted_documents_lead_the_context(monkeypatch) -> None:
    _, calls, _ = await _run(
        monkeypatch,
        "do you buy? (Topic: Become a partner and the reseller programme)",
        ["Partners resell DeployIQ."],
        boost_document_ids=["doc-partner"],
    )
    assert calls[0]["chunks"][0].document_id == "doc-partner"


async def test_greetings_are_never_clarified(monkeypatch) -> None:
    response, calls, _ = await _run(monkeypatch, "hello", ["unused"])
    assert response.outcome == "conversational"
    assert calls == []


# ---------------------------------------------------------------------------
# /query round trip
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_query_round_trip_clarify_then_answer(
    client, db_session, fake_llm, monkeypatch
) -> None:
    actor = f"clarify-{uuid.uuid4().hex[:8]}"
    headers = {"X-Client-Id": actor}

    async def fake_retrieve(*_a, **_k):
        return _spread_sources()

    async def fake_post_checks(*_a, **_k):
        return True, 0.9, []

    evaluated: list[str] = []
    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post_checks)
    monkeypatch.setattr(
        sessions, "AsyncSessionLocal", shared_session_factory(db_session)
    )
    monkeypatch.setattr(
        query_route,
        "schedule_online_evaluation",
        lambda **k: evaluated.append(k["outcome"]),
    )
    monkeypatch.setattr(settings, "rate_limit_exempt_actors", [actor])

    fake_llm["set_response"](json.dumps(_payload()), USAGE)
    first = await client.post("/query", json={"query": "do you buy?"}, headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["outcome"] == "clarify"
    questions = body["clarification"]["questions"]
    assert [q["header"] for q in questions] == ["Topic", "Region"]
    assert questions[0]["options"][0]["sources"] == ["services.md"]
    assert body["prompt_tokens"] == USAGE["prompt_tokens"]
    assert body["cost_usd"] is not None
    assert evaluated == []  # questions back are not judged as an answer
    session_id = body["session_id"]

    await sessions.drain_pending_writes(actor)
    detail = await client.get(f"/sessions/{session_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    stored = [m for m in detail.json()["messages"] if m["role"] == "assistant"]
    assert stored[-1]["outcome"] == "clarify"
    assert len(stored[-1]["clarification"]["questions"]) == 2

    fake_llm["set_response"]("Partners resell DeployIQ services.", USAGE)
    second = await client.post(
        "/query",
        json={
            "query": "Topic: Become a partner · Region: Australia",
            "session_id": session_id,
            "clarification_answers": [
                {"question_id": "Q1", "option_ids": ["O3"]},
                {"question_id": "Q2", "option_ids": ["O1"]},
            ],
        },
        headers=headers,
    )
    assert second.status_code == 200, second.text
    answer = second.json()
    assert answer["outcome"] == "answered"
    assert answer["clarification"] is None
    stored_answers = answer["token_details"]["clarification_answers"]
    assert [a["labels"] for a in stored_answers["answers"]] == [
        ["Become a partner"],
        ["Australia"],
    ]
    assert fake_llm["calls"][-1]["prompt"] == (
        "do you buy? (Topic: Become a partner; Region: Australia)"
    )
    assert evaluated == ["answered"]

    # A plain-text client gets the questions as text and can reply by name.
    assert "Become a partner" in body["answer"]


@pytest.mark.db
async def test_query_without_a_clarification_ignores_answers(
    client, monkeypatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_query(**kwargs: Any) -> QueryResponse:
        captured.update(kwargs)
        return QueryResponse(
            answer="ok",
            sources=[],
            grounded=True,
            confidence=0.9,
            request_id=kwargs["request_id"],
            latency_ms=10.0,
        )

    monkeypatch.setattr(query_route, "run_query", fake_run_query)
    monkeypatch.setattr(
        query_route,
        "schedule_exchange_write",
        lambda **_k: (str(uuid.uuid4()), str(uuid.uuid4()), "t"),
    )
    monkeypatch.setattr(query_route, "exchange_write_task", lambda _mid: None)
    monkeypatch.setattr(query_route, "schedule_online_evaluation", lambda **_k: None)
    resp = await client.post(
        "/query",
        json={
            "query": "Pricing and plans",
            "clarification_answers": [{"question_id": "Q1", "option_ids": ["O2"]}],
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["query"] == "Pricing and plans"
    assert captured["boost_document_ids"] is None


@pytest.mark.db
async def test_query_rejects_oversized_answers(client) -> None:
    resp = await client.post(
        "/query",
        json={
            "query": "x",
            "clarification_answers": [{"question_id": "Q1", "other_text": "y" * 500}],
        },
    )
    assert resp.status_code == 422
