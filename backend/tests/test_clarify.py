"""Clarifying questions (outcome ``clarify``): decision rules, option
generation, the orchestrator path and the /query round trip."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.api.routes import query as query_route
from app.core import sessions
from app.core.config import get_settings
from app.governance.outcomes import ALL_OUTCOMES, GENERATION_OUTCOMES, derive_outcome
from app.harness import clarify, orchestrator
from app.harness.clarify import (
    CLARIFY_OUTCOME,
    clarification_streak,
    content_word_count,
    parse_clarification,
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
    ClarificationChoice,
    ClarificationOption,
    QueryResponse,
    SourceChunk,
)
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
        content=f"{name} {section or ''} content",
        score=score,
        metadata=metadata,
        embedding=[0.05] * 8,
    )


def _spread_sources() -> list[SourceChunk]:
    return [
        _src("doc-a", 0.62, name="platform-pricing.md", section="Platform plans"),
        _src("doc-b", 0.60, name="support.md", section="Support tiers"),
        _src("doc-c", 0.59, name="partner-program.md"),
        _src("doc-a", 0.40, name="platform-pricing.md", section="Billing FAQ"),
    ]


def _clarify_reply(*options: dict[str, str], question: str = "Which one?") -> str:
    return "CLARIFY: " + json.dumps({"question": question, "options": list(options)})


def _clarification() -> Clarification:
    return Clarification(
        question="Which pricing do you mean?",
        options=[
            ClarificationOption(id="T1", label="Platform plans", document_id="doc-a"),
            ClarificationOption(id="T2", label="Support tiers", document_id="doc-b"),
        ],
        original_query="pricing",
    )


def _clarify_history() -> list[ChatTurn]:
    clar = _clarification()
    return [
        ChatTurn(role="user", content="pricing"),
        ChatTurn(
            role="assistant",
            content=render_clarification_text(clar),
            outcome=CLARIFY_OUTCOME,
            clarification=clar,
        ),
    ]


# ---------------------------------------------------------------------------
# Decision rules
# ---------------------------------------------------------------------------


def test_content_words_ignore_question_scaffolding() -> None:
    assert content_word_count("What is the pricing?") == 1
    assert content_word_count("How do I configure SSO for Entra ID tenants") == 5


def test_streak_counts_trailing_clarifications_only() -> None:
    assert clarification_streak(None) == 0
    history = _clarify_history()
    assert clarification_streak(history) == 1
    answered = [*history, ChatTurn(role="user", content="1")]
    answered.append(ChatTurn(role="assistant", content="ok", outcome="answered"))
    assert clarification_streak(answered) == 0


def test_candidates_are_distinct_topics_near_the_top_score() -> None:
    candidates = select_candidates(_spread_sources())
    assert [c.label for c in candidates] == [
        "Platform plans",
        "Support tiers",
        "Partner program",  # no section: humanized document name
    ]
    assert [c.id for c in candidates] == ["T1", "T2", "T3"]
    # The 0.40 chunk is far below the 0.62 top score: not a competing topic.
    assert all(c.score >= 0.59 for c in candidates)


def test_candidates_capped_and_deduplicated(monkeypatch) -> None:
    monkeypatch.setattr(settings, "clarify_max_options", 2)
    sources = [
        _src("d1", 0.7, name="a.md", section="Overview"),
        _src("d1", 0.7, name="a.md", section="Overview"),
        _src("d2", 0.69, name="b.md", section="Setup"),
        _src("d3", 0.69, name="c.md", section="Limits"),
    ]
    assert [c.label for c in select_candidates(sources)] == ["Overview", "Setup"]


def test_plan_offers_topics_for_short_spread_query() -> None:
    candidates = plan_clarification("pricing", _spread_sources(), None, follow_up=False)
    assert len(candidates) == 3


@pytest.mark.parametrize(
    ("query", "sources", "history", "follow_up"),
    [
        # Clear, specific question: never clarified.
        (
            "How are the Platform plans priced for annual enterprise contracts",
            _spread_sources(),
            None,
            False,
        ),
        # Evidence points at one topic only.
        ("pricing", [_src("doc-a", 0.8, name="pricing.md")], None, False),
        # A follow-up is resolved by the conversation.
        (
            "pricing",
            _spread_sources(),
            [ChatTurn(role="user", content="Tell me about support")],
            True,
        ),
        # Compound question: decomposition answers both parts.
        ("pricing? and support?", _spread_sources(), None, False),
    ],
)
def test_plan_skips_when_not_ambiguous(query, sources, history, follow_up) -> None:
    assert plan_clarification(query, sources, history, follow_up=follow_up) == []


def test_plan_respects_consecutive_cap_and_flag(monkeypatch) -> None:
    monkeypatch.setattr(settings, "clarify_max_consecutive", 1)
    history = _clarify_history()
    assert (
        plan_clarification("plans", _spread_sources(), history, follow_up=False) == []
    )

    monkeypatch.setattr(settings, "clarify_max_consecutive", 2)
    monkeypatch.setattr(settings, "clarify_enabled", False)
    assert plan_clarification("pricing", _spread_sources(), None, follow_up=False) == []


# ---------------------------------------------------------------------------
# Option generation
# ---------------------------------------------------------------------------


def test_parse_keeps_only_retrieved_topics() -> None:
    candidates = select_candidates(_spread_sources())
    reply = _clarify_reply(
        {"id": "T2", "label": "Support plans"},
        {"id": "T9", "label": "Invented topic"},
        {"id": "t1", "label": "Platform"},
        question="Which pricing do you mean?",
    )
    clar = parse_clarification(reply, candidates, "pricing")
    assert clar is not None
    assert clar.question == "Which pricing do you mean?"
    assert [(o.id, o.label) for o in clar.options] == [
        ("T2", "Support plans"),
        ("T1", "Platform"),
    ]
    assert clar.options[0].document_id == "doc-b"
    assert clar.original_query == "pricing"


def test_parse_falls_back_to_candidates_on_bad_payload() -> None:
    candidates = select_candidates(_spread_sources())
    clar = parse_clarification("CLARIFY: not json", candidates, "pricing")
    assert clar is not None
    assert clar.question == clarify.DEFAULT_QUESTION
    assert [o.label for o in clar.options] == [c.label for c in candidates]


def test_parse_ignores_normal_answers() -> None:
    candidates = select_candidates(_spread_sources())
    assert parse_clarification("Plans start at $10.", candidates, "pricing") is None


def test_text_fallback_lists_numbered_options() -> None:
    text = render_clarification_text(_clarification())
    assert text.startswith("Which pricing do you mean?")
    assert "1. Platform plans" in text
    assert "2. Support tiers" in text
    assert "number or name" in text


# ---------------------------------------------------------------------------
# Resolving the reply
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reply", ["2", "2.", "support tiers", "T2"])
def test_plain_reply_matches_an_option(reply: str) -> None:
    resolved = resolve_clarification_reply(reply, _clarify_history())
    assert resolved is not None
    assert resolved.query == "pricing (specifically: Support tiers)"
    assert resolved.option_id == "T2"
    assert resolved.free_text is False


def test_structured_choice_uses_the_stored_question() -> None:
    choice = ClarificationChoice(
        original_query="tampered", option_id="T1", option_label="Platform plans"
    )
    resolved = resolve_clarification_reply("Platform plans", _clarify_history(), choice)
    assert resolved is not None
    assert resolved.original_query == "pricing"
    assert resolved.query == "pricing (specifically: Platform plans)"


def test_structured_choice_without_history_uses_the_request() -> None:
    choice = ClarificationChoice(original_query="pricing", option_label="Support")
    resolved = resolve_clarification_reply("Support", None, choice)
    assert resolved is not None
    assert resolved.query == "pricing (specifically: Support)"


def test_short_free_text_refines_but_new_questions_do_not() -> None:
    history = _clarify_history()
    refined = resolve_clarification_reply("for MSP partners", history)
    assert refined is not None
    assert refined.free_text is True
    assert refined.query == "pricing (specifically: for MSP partners)"
    assert resolve_clarification_reply("What is Zero Trust?", history) is None
    assert resolve_clarification_reply("thanks", history) is None
    assert resolve_clarification_reply("2", None) is None


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


async def _run(monkeypatch, query: str, replies: list[str], history=None):
    calls: list[dict[str, Any]] = []
    answers = iter(replies)

    async def fake_retrieve(*_a, **_k):
        return _spread_sources()

    async def fake_call_llm(q, _chunks, _history=None, **kwargs):
        calls.append({"query": q, **kwargs})
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
        history=history,
    )
    return response, calls


async def test_ambiguous_query_returns_a_clarification(monkeypatch) -> None:
    reply = _clarify_reply({"id": "T1"}, {"id": "T2"}, question="Which pricing?")
    response, calls = await _run(monkeypatch, "pricing", [reply])

    assert response.outcome == "clarify"
    assert response.clarification is not None
    assert [o.label for o in response.clarification.options] == [
        "Platform plans",
        "Support tiers",
    ]
    assert "1. Platform plans" in response.answer
    assert response.grounded is True
    # Tokens of the (single) LLM call are reported like any other answer.
    assert response.prompt_tokens == USAGE["prompt_tokens"]
    assert response.token_details is not None
    assert response.token_details["outcome"] == "clarify"
    assert response.token_details["clarification"]["original_query"] == "pricing"
    assert "T1: Platform plans" in calls[0]["clarify_block"]
    assert len(calls) == 1


async def test_model_may_still_answer_a_gated_query(monkeypatch) -> None:
    response, calls = await _run(monkeypatch, "pricing", ["Plans start at $10."])
    assert response.outcome == "answered"
    assert response.clarification is None
    assert "clarify_block" in calls[0]


async def test_clear_query_never_offers_clarification(monkeypatch) -> None:
    response, calls = await _run(
        monkeypatch,
        "How are the Platform plans priced for annual enterprise contracts",
        ["Annual contracts are priced per seat."],
    )
    assert response.outcome == "answered"
    assert "clarify_block" not in calls[0]


async def test_greetings_are_never_clarified(monkeypatch) -> None:
    response, calls = await _run(monkeypatch, "hello", ["unused"])
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

    fake_llm["set_response"](
        _clarify_reply(
            {"id": "T1", "label": "Platform plans"},
            {"id": "T2", "label": "Support tiers"},
            question="Which pricing do you mean?",
        ),
        USAGE,
    )
    first = await client.post("/query", json={"query": "pricing"}, headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["outcome"] == "clarify"
    assert body["clarification"]["question"] == "Which pricing do you mean?"
    assert [o["id"] for o in body["clarification"]["options"]] == ["T1", "T2"]
    assert body["prompt_tokens"] == USAGE["prompt_tokens"]
    assert body["cost_usd"] is not None
    assert evaluated == []  # a question back is not judged as an answer
    session_id = body["session_id"]

    await sessions.drain_pending_writes(actor)
    detail = await client.get(f"/sessions/{session_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    stored = [m for m in detail.json()["messages"] if m["role"] == "assistant"]
    assert stored[-1]["outcome"] == "clarify"
    assert stored[-1]["clarification"]["options"][1]["label"] == "Support tiers"

    # A client that ignores the new fields replies with the option number.
    fake_llm["set_response"]("Support tiers start at the Standard plan.", USAGE)
    second = await client.post(
        "/query", json={"query": "2", "session_id": session_id}, headers=headers
    )
    assert second.status_code == 200, second.text
    answer = second.json()
    assert answer["outcome"] == "answered"
    assert answer["clarification"] is None
    assert answer["token_details"]["clarification_choice"]["option_id"] == "T2"
    assert fake_llm["calls"][-1]["prompt"] == "pricing (specifically: Support tiers)"
    assert evaluated == ["answered"]


@pytest.mark.db
async def test_query_accepts_a_structured_choice(client, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_query(**kwargs: Any) -> QueryResponse:
        captured.update(kwargs)
        return QueryResponse(
            answer="Support tiers start at Standard.",
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
            "query": "Support tiers",
            "clarification_choice": {
                "original_query": "pricing",
                "option_id": "T2",
                "option_label": "Support tiers",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["query"] == "pricing (specifically: Support tiers)"
    assert resp.json()["token_details"]["clarification_choice"]["label"] == (
        "Support tiers"
    )


@pytest.mark.db
async def test_query_rejects_an_oversized_choice(client) -> None:
    resp = await client.post(
        "/query",
        json={
            "query": "x",
            "clarification_choice": {
                "original_query": "pricing",
                "option_label": "y" * 500,
            },
        },
    )
    assert resp.status_code == 422
