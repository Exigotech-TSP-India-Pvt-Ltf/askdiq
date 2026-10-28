"""Clarifier quality: company facts and named entities are answered, back
references and vague goals are asked back, history does not pollute subjects."""

from __future__ import annotations

import time
import uuid

import pytest

from app.core.config import get_settings
from app.harness import ambiguity
from app.harness.ambiguity import (
    AmbiguityKind,
    assess,
    framework_options,
    slot_clarification,
)
from app.harness.answer_shape import AnswerShape, refine_shape, strip_answers
from app.harness.clarify import (
    CLARIFY_OUTCOME,
    NOT_SURE_LABEL,
    clarify_eligible,
    clarify_instruction,
    plan_clarification,
    resolve_clarification_reply,
)
from app.retrieval.retriever import needs_conversation_context
from app.schemas.query import ChatTurn, SourceChunk
from tests.clarify_eval_cases import (
    AMBIGUOUS_QUERIES,
    BACK_REFERENCE_QUERIES,
    CLEAR_QUERIES,
    COMPANY_FACT_QUERIES,
    GENERIC_PRODUCT_QUERIES,
    VAGUE_GOAL_QUERIES,
)
from tests.test_clarify import _spread_sources
from tests.test_clarify_trigger import (
    _assert_grounded,
    _no_decision_call,
    _run,
    _sources,
)

settings = get_settings()


def _answered(content: str) -> ChatTurn:
    return ChatTurn(role="assistant", content=content, outcome="answered")


def _chunk(doc: str, name: str, score: float, content: str) -> SourceChunk:
    return SourceChunk(
        chunk_id=str(uuid.uuid4()),
        document_id=doc,
        content=content,
        score=score,
        metadata={"source_name": name},
        embedding=[0.05] * 8,
    )


# ---------------------------------------------------------------------------
# 1. Named entities and company facts are never sent down the clarify gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", COMPANY_FACT_QUERIES)
def test_company_facts_and_named_entities_are_not_eligible(query: str) -> None:
    assert not clarify_eligible(query, None, follow_up=False)
    assert plan_clarification(query, _spread_sources(), None, follow_up=False) == []


@pytest.mark.parametrize("query", ["where is exigo tech headquartered?", "EXIGO TECH"])
def test_entity_match_is_case_insensitive(query: str) -> None:
    assert not clarify_eligible(query, None, follow_up=False)


@pytest.mark.parametrize("query", ["E8?", "zero trust?", "ISO?"])
def test_bare_service_topics_may_still_be_clarified(query: str) -> None:
    assert clarify_eligible(query, None, follow_up=False)


def test_service_plus_a_question_is_specific() -> None:
    assert not clarify_eligible("E8 pricing?", None, follow_up=False)


@pytest.mark.parametrize("query", AMBIGUOUS_QUERIES)
def test_ambiguous_eval_queries_stay_eligible(query: str) -> None:
    assert clarify_eligible(query, None, follow_up=False)


# ---------------------------------------------------------------------------
# 2. Relevance / winner check
# ---------------------------------------------------------------------------


def _flat(top: float) -> list[SourceChunk]:
    return [
        _chunk("d1", "a.md", top, "one"),
        _chunk("d2", "b.md", top - 0.01, "two"),
        _chunk("d3", "c.md", top - 0.02, "three"),
    ]


def test_low_scores_are_just_answered() -> None:
    assert plan_clarification("plans", _flat(0.21), None, follow_up=False) == []
    assert plan_clarification("plans", _flat(0.45), None, follow_up=False)


def test_gate_score_is_a_setting(monkeypatch) -> None:
    assert settings.clarify_min_gate_score == pytest.approx(0.30)
    monkeypatch.setattr(settings, "clarify_min_gate_score", 0.1)
    assert plan_clarification("plans", _flat(0.21), None, follow_up=False)


async def test_what_is_a_cat_is_answered_not_clarified(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(
        monkeypatch, "What is a cat?", ["It is an animal."], sources=_sources(0.7)
    )
    assert response.outcome == "answered"
    assert "clarify_block" not in calls[0]


async def test_exigo_headquarters_is_answered_not_clarified(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, retrieve_calls = await _run(
        monkeypatch,
        "Where is Exigo Tech headquartered?",
        ["Exigo Tech is headquartered in Australia."],
    )
    assert response.outcome == "answered"
    assert "clarify_block" not in calls[0]
    assert "max_documents" not in retrieve_calls[0]


def test_instruction_has_no_canned_example_and_answers_entities() -> None:
    brief = clarify_instruction(
        plan_clarification("plans", _spread_sources(), None, follow_up=False)
    )
    assert "offers several services" not in brief
    assert "names a company or entity" in brief
    assert 'set action to "answer"' in brief
    assert "Never offer an option the context cannot answer" in brief


# ---------------------------------------------------------------------------
# 4. Back references
# ---------------------------------------------------------------------------

ONE = [
    ChatTurn(role="user", content="Tell me about Essential Eight"),
    _answered("Essential Eight is a set of mitigation strategies."),
]
TWO = [
    ChatTurn(role="user", content="compare them"),
    _answered("Essential Eight and ISO 27001 differ in scope."),
]


@pytest.mark.parametrize("query", BACK_REFERENCE_QUERIES)
def test_back_reference_with_no_history_asks_which(query: str) -> None:
    assert assess(query, None).kind is AmbiguityKind.REFERENT


@pytest.mark.parametrize("query", BACK_REFERENCE_QUERIES)
def test_back_reference_resolved_by_exactly_one_subject(query: str) -> None:
    assert assess(query, ONE).kind is AmbiguityKind.NONE


@pytest.mark.parametrize("query", BACK_REFERENCE_QUERIES)
def test_back_reference_with_two_subjects_asks_which(query: str) -> None:
    assert assess(query, TWO).kind is AmbiguityKind.REFERENT


def test_back_reference_counts_only_the_last_answer() -> None:
    history = [
        ChatTurn(role="user", content="compare E8 and ISO 27001"),
        _answered("Essential Eight and ISO 27001 differ."),
        ChatTurn(role="user", content="more on E8"),
        _answered("Essential Eight has eight strategies."),
    ]
    assert assess("tell me about the one above", history).kind is AmbiguityKind.NONE


def test_back_reference_after_a_fallback_asks_which() -> None:
    history = [
        ChatTurn(role="user", content="E8?"),
        ChatTurn(role="assistant", content="Essential Eight?", outcome="ungrounded"),
    ]
    assert assess("the previous one?", history).kind is AmbiguityKind.REFERENT


async def test_back_reference_asks_a_grounded_service_question(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(monkeypatch, "What about the previous one?")
    assert response.outcome == CLARIFY_OUTCOME
    assert calls == []
    assert response.clarification is not None
    _assert_grounded(response.clarification.questions[0])


@pytest.mark.parametrize(
    "query", ["Who is Exigo?", "who is exigo", "What is E8?", "Is Zero Trust safe?"]
)
def test_a_named_subject_is_not_a_follow_up(query: str) -> None:
    assert needs_conversation_context(query) is False


@pytest.mark.parametrize("query", ["Why?", "is it safe?", "what about pricing?"])
def test_unnamed_short_questions_are_still_follow_ups(query: str) -> None:
    assert needs_conversation_context(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "what is above",
        "as mentioned earlier, what next",
        "tell me about the one you mentioned",
        "the second one",
        "that one",
        "what does the above mean for our rollout plan",
        "tell me more about the previous one please",
        "compare it with the one above for our rollout",
        "as mentioned earlier, which controls apply to our team",
    ],
)
def test_back_reference_phrases_are_follow_ups(query: str) -> None:
    assert needs_conversation_context(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "What changed in the previous Essential Eight version?",
        "Show earlier maturity level definitions for our offering",
        "Which controls appear above the baseline in a typical rollout?",
    ],
)
def test_bare_previous_above_earlier_are_not_follow_ups(query: str) -> None:
    assert needs_conversation_context(query) is False


# ---------------------------------------------------------------------------
# 5. History pollution
# ---------------------------------------------------------------------------


def test_history_ignores_sentence_initial_words() -> None:
    history = [
        ChatTurn(role="user", content="Thanks for that. It was The Best"),
        _answered("Sure. It depends. The answer is complicated. Thanks."),
    ]
    assert ambiguity._history_subjects(history) == set()
    assert assess("How much does it cost?", history).kind is AmbiguityKind.REFERENT


def test_history_keeps_catalogue_and_brand_names() -> None:
    history = [
        ChatTurn(role="user", content="what is exigo"),
        _answered("We offer ZERO TRUST and e8 assessments."),
    ]
    assert ambiguity._history_subjects(history) == {
        "exigo",
        "Zero Trust",
        "Essential Eight",
    }


def test_greeting_turns_name_no_subject() -> None:
    history = [
        ChatTurn(role="user", content="hi"),
        _answered("Hello! Ask me about platform, Essential Eight, Zero Trust."),
    ]
    assert ambiguity._history_subjects(history) == set()
    assert assess("How much does it cost?", history).kind is AmbiguityKind.REFERENT


def test_pronouns_are_not_proper_nouns() -> None:
    assert ambiguity._subjects("so, That is It. They said Thanks") == set()


def test_brand_terms_match_case_insensitively() -> None:
    assert ambiguity._subjects("what is EXIGO about") == {"exigo"}
    assert ambiguity._subjects("how does deployiq work") == {"deployiq"}
    assert ambiguity._subjects("do you do e8 or cis or ZERO TRUST") == {
        "Essential Eight",
        "CIS Benchmark",
        "Zero Trust",
    }


# ---------------------------------------------------------------------------
# 6. Bare cost / contact questions are decided by the rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query", ["pricing?", "cost", "price", "how much?", "what are your prices?"]
)
def test_bare_cost_is_a_rule_decision(query: str) -> None:
    assert assess(query, None).kind is AmbiguityKind.REFERENT


def test_bare_time_is_a_rule_decision() -> None:
    assert assess("how long?", None).kind is AmbiguityKind.REFERENT


def test_bare_cost_with_a_subject_is_not_asked() -> None:
    assert assess("pricing?", ONE).kind is AmbiguityKind.NONE
    assert assess("E8 pricing?", None).kind is AmbiguityKind.NONE


@pytest.mark.parametrize("query", COMPANY_FACT_QUERIES)
def test_company_info_never_asks_back(query: str) -> None:
    assert assess(query, None).kind is AmbiguityKind.NONE


def test_where_are_they_located_is_not_a_dangling_pronoun() -> None:
    assert assess("where are they located?", None).kind is AmbiguityKind.NONE


async def test_bare_price_is_asked_by_rules_without_the_model(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(monkeypatch, "pricing?")
    assert response.outcome == CLARIFY_OUTCOME
    assert calls == []


async def test_phone_is_answered(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(monkeypatch, "phone number?", ["Call us."])
    assert response.outcome == "answered"
    assert "clarify_block" not in calls[0]


# ---------------------------------------------------------------------------
# 7. Generic one-product questions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", GENERIC_PRODUCT_QUERIES)
def test_generic_product_questions_are_answered(query: str) -> None:
    assert assess(query, None).kind is AmbiguityKind.NONE
    assert not clarify_eligible(query, None, follow_up=False)


def test_how_much_does_it_cost_still_asks_which_service() -> None:
    assert assess("How much does it cost?", None).kind is AmbiguityKind.REFERENT


async def test_how_does_it_work_is_answered(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(monkeypatch, "how does it work", ["It works."])
    assert response.outcome == "answered"
    assert "clarify_block" not in calls[0]


# ---------------------------------------------------------------------------
# 9. Typos (and ReDoS budget of the new patterns)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "We need to get complience fast, what should we do?",
        "we need to be complaint, where do we start",
        "what should we do about our audti",
        "what should we do about complaince",
    ],
)
def test_typos_still_trigger_the_series(query: str) -> None:
    assert assess(query, None).kind is AmbiguityKind.SLOTS


def test_a_real_complaint_does_not_trigger_the_series() -> None:
    query = "we have a complaint, what should we do?"
    assert assess(query, None).kind is AmbiguityKind.NONE


HOSTILE = [
    "we " + "need " * 2000,
    "help us " + "get " * 2000,
    "the " * 5000 + "one",
    "(" + "a:b;" * 5000,
    "x" * 100_000,
    "i " + " " * 100_000 + "need compliance",
]


@pytest.mark.parametrize("text", HOSTILE, ids=lambda t: f"{t[:8]!r}x{len(t)}")
def test_new_rules_stay_fast_on_hostile_input(text: str) -> None:
    started = time.perf_counter()
    assess(text, None)
    ambiguity._missing_slots(text)
    ambiguity._last_answer_subject_count([_answered(text[:4000])])
    strip_answers(text)
    clarify_eligible(text, None, follow_up=False)
    needs_conversation_context(text)
    assert time.perf_counter() - started < 1.5  # noqa: PLR2004


# ---------------------------------------------------------------------------
# 10. Vague goals trigger the series
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", VAGUE_GOAL_QUERIES)
def test_vague_goals_ask_the_framework_series(query: str) -> None:
    result = assess(query, None)
    assert result.kind is AmbiguityKind.SLOTS
    assert result.missing_slots[0] == "framework"
    assert "maturity" in result.missing_slots


@pytest.mark.parametrize(
    "query",
    [
        "We need Essential Eight compliance",
        "how do I secure my ISO 27001 tenant",
        "we want to get CIS certified",
        *CLEAR_QUERIES,
    ],
)
def test_clear_goals_are_not_asked_back(query: str) -> None:
    assert assess(query, None).kind is AmbiguityKind.NONE


async def test_vague_goal_asks_framework_and_maturity(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(monkeypatch, "I need compliance")
    assert response.outcome == CLARIFY_OUTCOME
    assert calls == []
    assert response.clarification is not None
    headers = [q.header for q in response.clarification.questions]
    assert headers[:2] == ["Framework", "Maturity"]


def test_cost_and_time_in_one_message_is_one_combined_question() -> None:
    query = "how much will this cost and how long will it take"
    assert assess(query, None).kind is AmbiguityKind.REFERENT
    clar = ambiguity.service_clarification(query, _sources())
    assert clar is not None
    (question,) = clar.questions
    assert question.multi_select is True
    assert "pricing and timelines" in question.question
    _assert_grounded(question)


async def test_cost_and_time_message_asks_once(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(
        monkeypatch, "how much will this cost and how long will it take"
    )
    assert response.outcome == CLARIFY_OUTCOME
    assert calls == []
    assert response.clarification is not None
    assert len(response.clarification.questions) == 1
    assert response.clarification.questions[0].multi_select is True


# ---------------------------------------------------------------------------
# 11. Framework options
# ---------------------------------------------------------------------------


def test_framework_options_come_from_the_whole_catalogue() -> None:
    # Only a Zero Trust document was retrieved: the fixed priority order still
    # applies (Zero Trust is 4th, so it is cut), and nothing is invented.
    only_zt = [_chunk("doc-zt", "zero-trust-learn-more.md", 0.5, "Zero Trust")]
    options = framework_options(only_zt)
    labels = [o.label for o in options]
    assert labels[0] == "Essential Eight"
    assert labels[-1] == NOT_SURE_LABEL
    assert set(labels[:-1]) <= set(ambiguity.offering_labels())
    assert len(options) <= settings.clarify_max_options
    assert options[-1].document_ids == []
    named = framework_options(only_zt, "zero trust audit")
    assert named[0].label == "Zero Trust"
    assert named[0].document_ids == ["doc-zt"]


def test_not_sure_is_always_the_last_option() -> None:
    options = framework_options(_sources())
    assert options[-1].label == NOT_SURE_LABEL
    assert not options[-1].recommended
    assert len({o.id for o in options}) == len(options)


def test_series_preamble_lists_the_catalogue_without_claiming_it_is_all() -> None:
    clar = slot_clarification("audit?", ("framework",), _sources())
    assert clar is not None
    assert clar.preamble.startswith("Happy to help you get ready.")
    assert (
        "DeployIQ helps with Essential Eight, ISO 27001, CIS Benchmark, "
        "Zero Trust and Security Baseline." in clar.preamble
    )
    assert "covers" not in clar.preamble


def test_not_sure_reply_asks_for_a_recommendation() -> None:
    clar = slot_clarification("audit?", ("framework",), _sources())
    assert clar is not None
    history = [
        ChatTurn(role="user", content="audit?"),
        ChatTurn(
            role="assistant",
            content="Which framework?",
            outcome=CLARIFY_OUTCOME,
            clarification=clar,
        ),
    ]
    resolved = resolve_clarification_reply(NOT_SURE_LABEL, history)
    assert resolved is not None
    assert "recommend the best fit" in resolved.query
    assert resolved.query.startswith("audit? (Framework: not sure")
    assert resolved.document_ids == []


# ---------------------------------------------------------------------------
# 12. Folded reply query does not force the PARTIAL shape
# ---------------------------------------------------------------------------

FOLDED = (
    "How do we get ready for an audit on Microsoft 365 "
    "(Framework: ISO 27001; Maturity: Partly implemented)"
)
_SHAPE_SOURCES = [
    _chunk("d1", "iso-27001.md", 0.5, "ISO 27001 controls for Microsoft 365 tenants."),
    _chunk("d2", "m365.md", 0.4, "Microsoft 365 security configuration guidance."),
]


def test_folded_reply_is_not_partial() -> None:
    shape = refine_shape(AnswerShape.HOW_TO, FOLDED, _SHAPE_SOURCES)
    assert shape is AnswerShape.HOW_TO
    assert shape is not AnswerShape.PARTIAL


def test_label_words_are_ignored_for_coverage() -> None:
    query = "Framework Maturity Deadline Environment Partly Mostly ISO 27001"
    assert refine_shape(AnswerShape.GENERAL, query, _SHAPE_SOURCES) is (
        AnswerShape.GENERAL
    )


def test_real_partial_coverage_is_still_detected() -> None:
    query = "Compare ISO 27001 and Acme Quantum (Framework: ISO 27001)"
    assert refine_shape(AnswerShape.COMPARISON, query, _SHAPE_SOURCES) is (
        AnswerShape.PARTIAL
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("q? (Framework: ISO 27001; Maturity: Partly implemented)", "q?"),
        ("q? (just words)", "q? (just words)"),
        ("no parens", "no parens"),
        ("trailing )", "trailing )"),
        ("q? (a: b) (c: d)", "q? (a: b)"),
    ],
)
def test_strip_answers(query: str, expected: str) -> None:
    assert strip_answers(query) == expected


# ---------------------------------------------------------------------------
# Live findings: deterministic framework options, generic slot options
# ---------------------------------------------------------------------------

_FRAMEWORK_ORDER = ["Essential Eight", "ISO 27001", "CIS Benchmark"]
_RETRIEVAL_SHUFFLES = [
    [
        ("Zero Trust.pdf", 0.9),
        ("Security Baseline.pdf", 0.8),
        ("Essential Eight.pdf", 0.7),
    ],
    [("Security Baseline.pdf", 0.9), ("Zero Trust.pdf", 0.8)],
    [("CIS Benchmark.pdf", 0.9), ("Zero Trust.pdf", 0.8), ("ISO 27001.pdf", 0.1)],
    [],
]


def _retrieved(spec: list[tuple[str, float]]) -> list[SourceChunk]:
    return [
        _chunk(f"d{i}", name, score, "maturity weeks gap")
        for i, (name, score) in enumerate(spec)
    ]


@pytest.mark.parametrize("spec", _RETRIEVAL_SHUFFLES)
def test_framework_options_ignore_retrieval_order_and_include_iso(spec) -> None:
    labels = [o.label for o in framework_options(_retrieved(spec))]
    assert labels == [*_FRAMEWORK_ORDER, NOT_SURE_LABEL]


def test_framework_options_lead_with_what_the_query_implies() -> None:
    labels = [
        o.label
        for o in framework_options(
            _retrieved(_RETRIEVAL_SHUFFLES[0]), "we want certification"
        )
    ]
    assert labels[0] == "ISO 27001"
    assert labels[-1] == NOT_SURE_LABEL
    assert len(labels) == get_settings().clarify_max_options


def test_framework_options_include_a_named_zero_trust_even_past_the_cut() -> None:
    labels = [o.label for o in framework_options([], "we never trust any device")]
    assert labels[0] == "Zero Trust"
    assert "Essential Eight" in labels


def test_series_always_has_four_questions_with_generic_slot_options() -> None:
    # Retrieved text mentions none of the slot evidence: options still stand.
    sources = [_chunk("d1", "Zero Trust.pdf", 0.9, "unrelated prose")]
    query = "we need compliance asap"
    result = assess(query, None)
    clar = slot_clarification(query, result.missing_slots, sources)
    assert clar is not None
    assert [q.header for q in clar.questions] == [
        "Framework",
        "Maturity",
        "Deadline",
        "Environment",
    ]
    assert all(len(q.options) >= 3 for q in clar.questions)  # noqa: PLR2004
    assert len(clar.questions) <= get_settings().clarify_max_questions


def test_environment_is_skipped_only_when_the_query_names_it() -> None:
    sources = [_chunk("d1", "Zero Trust.pdf", 0.9, "unrelated prose")]
    query = "what do we need to do for compliance in our Microsoft 365 tenant"
    result = assess(query, None)
    assert result.kind is AmbiguityKind.SLOTS
    assert result.missing_slots == ("framework", "maturity")
    clar = slot_clarification(query, result.missing_slots, sources)
    assert clar is not None
    assert [q.header for q in clar.questions] == ["Framework", "Maturity"]
    bare = assess("what do we need to do for compliance", None)
    assert "environment" in bare.missing_slots


def test_a_named_platform_is_not_a_named_framework() -> None:
    result = assess("how do we get compliant on your platform", None)
    assert result.kind is AmbiguityKind.SLOTS
    assert "framework" in result.missing_slots


@pytest.mark.parametrize(
    "query",
    [
        "is that product safe to purchase?",
        "Is this service safe to buy",
        "is it worth it",
    ],
)
def test_purchase_trust_questions_are_answered_with_or_without_history(query) -> None:
    named = [
        ChatTurn(role="user", content="tell me about E8"),
        _answered("Essential Eight is..."),
    ]
    unnamed = [ChatTurn(role="user", content="hi there"), _answered("Welcome")]
    for history in (None, named, unnamed):
        assert assess(query, history).kind is AmbiguityKind.NONE
        assert not clarify_eligible(query, history, follow_up=False)


def test_how_does_it_work_without_history_is_answered() -> None:
    assert assess("how does it work", None).kind is AmbiguityKind.NONE
    assert not clarify_eligible("how does it work", None, follow_up=False)


def test_price_questions_still_get_the_service_card() -> None:
    assert assess("how much does it cost?", None).kind is AmbiguityKind.REFERENT


@pytest.mark.parametrize("query", ["idk", "i need help", "what should we buy"])
def test_subject_less_vague_prompts_get_the_goal_card_not_a_service_card(
    query: str,
) -> None:
    assert assess(query, None).kind is AmbiguityKind.GOAL


def test_named_subjects_keep_the_service_flow() -> None:
    assert assess("how much does it cost?", None).kind is AmbiguityKind.REFERENT
    assert assess("E8 pricing", None).kind is AmbiguityKind.NONE
