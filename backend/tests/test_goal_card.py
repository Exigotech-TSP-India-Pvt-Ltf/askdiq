"""The GOAL card: a vague help/need/situation statement that names nothing is
asked "what are you hoping to get done?" (rule-decided before retrieval, no
LLM), never refused as off-topic and never given a random service card."""

from __future__ import annotations

import random

import pytest

from app.harness.ambiguity import (
    AmbiguityKind,
    assess,
    goal_clarification,
    rule_clarification,
)
from app.harness.clarify import (
    CLARIFY_OUTCOME,
    GOAL_AUDIT_QUERY,
    GOAL_HEADER,
    GOAL_QUERIES,
    GOAL_QUESTION,
    resolve_clarification_reply,
)
from app.schemas.query import ChatTurn, Clarification, ClarificationAnswer
from tests.test_clarify_trigger import (
    _assert_grounded,
    _no_decision_call,
    _run,
    _sources,
)

LOST = "No worries, plenty of people aren't sure where to start."
WORRIED = "Sounds like something's on your mind."
SMALL = "Happy to help a small business get this right."
DEFAULT = "Happy to point you in the right direction."

VAGUE_CUES = {
    "tell me something": DEFAULT,
    "i want to know about stuff": DEFAULT,
    "i am confused": LOST,
    "need advice": DEFAULT,
    "what's best": DEFAULT,
    "idk": LOST,
    "we are a small business": SMALL,
    "our tenant has problems": WORRIED,
    "what should we buy": DEFAULT,
    "compare your options": DEFAULT,
    "i need help": DEFAULT,
    "not sure where to start": LOST,
    "worried about hackers": WORRIED,
    "my boss asked me to look into this": DEFAULT,
    "I have a question": DEFAULT,
    "what are my options": DEFAULT,
    "where do I begin?": LOST,
    "How do I get started?": LOST,
    "recommend something": DEFAULT,
    "Hi, I need some advice please": DEFAULT,
    "something about security": DEFAULT,
}

MUST_NOT = [
    "hi",
    "thanks",
    "what can you do",
    "help",
    "E8 pricing",
    "Tell me about zero trust",
    "we got hacked",
    "we have been breached, what should we do",
    "where is your office located",
    "who is the ceo",
    "what's the weather",
    "weather",
    "what is a cat",
    "How do I set up conditional access?",
    "I need help setting up conditional access policies",
    "I need help with DeployIQ pricing",
    "how does it work?",
    "What does Essential Eight maturity level 2 require?",
]


def _card(query: str) -> Clarification:
    assessment = assess(query, None)
    card = goal_clarification(query, _sources(), assessment.cue)
    assert card is not None
    return card


@pytest.mark.parametrize(("query", "preamble"), list(VAGUE_CUES.items()))
def test_vague_prompts_get_the_goal_card(query: str, preamble: str) -> None:
    assessment = assess(query, None)
    assert assessment.kind is AmbiguityKind.GOAL
    card = goal_clarification(query, _sources(), assessment.cue)
    assert card is not None
    assert card.preamble == preamble
    [question] = card.questions
    assert (question.header, question.question) == (GOAL_HEADER, GOAL_QUESTION)
    assert [o.label for o in question.options] == [
        "Understand what you do",
        "Get audit ready",
        "Fix or tighten security",
        "Pricing or talk to someone",
    ]
    _assert_grounded(question)


@pytest.mark.parametrize("query", MUST_NOT)
def test_clear_or_other_prompts_never_get_the_goal_card(query: str) -> None:
    assert assess(query, None).kind is not AmbiguityKind.GOAL


def test_a_prompt_following_an_answer_never_gets_the_goal_card() -> None:
    history = [
        ChatTurn(role="user", content="What is Zero Trust?"),
        ChatTurn(role="assistant", content="Zero Trust is ...", outcome="answered"),
    ]
    assert assess("i need help", history).kind is not AmbiguityKind.GOAL


def test_a_reply_to_a_clarification_never_gets_the_goal_card() -> None:
    assert assess("i need help", None, clarification_reply=True).kind is (
        AmbiguityKind.NONE
    )


def test_the_consecutive_cap_stops_the_goal_card() -> None:
    card = _card("i need help")
    history = [
        ChatTurn(
            role="assistant", content="q", outcome=CLARIFY_OUTCOME, clarification=card
        )
    ] * 2
    assert assess("idk", history).kind is AmbiguityKind.NONE


def test_long_prompts_are_never_vague() -> None:
    assert assess("i need help " + "with my setup " * 40, None).kind is (
        AmbiguityKind.NONE
    )


def _refused(outcome: str) -> list[ChatTurn]:
    return [
        ChatTurn(role="user", content="blah"),
        ChatTurn(role="assistant", content="Sorry", outcome=outcome),
    ]


def test_a_third_dead_end_is_replaced_by_the_card() -> None:
    nothing = "my cousin likes boats"
    assert assess(nothing, None).kind is AmbiguityKind.NONE
    assert assess(nothing, _refused("blocked")).kind is AmbiguityKind.NONE
    two = _refused("blocked") + _refused("ungrounded")
    assert assess(nothing, two).kind is AmbiguityKind.GOAL
    # ... unless something concrete was named, or it was an incident.
    assert assess("what is E8", two).kind is not AmbiguityKind.GOAL
    assert assess("we got hacked", two).kind is not AmbiguityKind.GOAL


async def test_orchestrator_asks_the_goal_card_before_retrieval_refusal(
    monkeypatch,
) -> None:
    _no_decision_call(monkeypatch)
    low = _sources(scale=0.2)  # a vague prompt scores very low
    response, calls, _ = await _run(monkeypatch, "i am confused", sources=low)
    assert calls == []
    assert response.outcome == CLARIFY_OUTCOME
    assert response.clarification is not None
    assert response.clarification.preamble == LOST
    assert response.clarification.questions[0].header == GOAL_HEADER


async def test_orchestrator_answers_a_clear_prompt_instead(monkeypatch) -> None:
    response, calls, _ = await _run(
        monkeypatch, "E8 pricing", ["Pricing is on request [1]."]
    )
    assert len(calls) == 1
    assert response.outcome != CLARIFY_OUTCOME


async def test_goal_card_needs_two_covered_options() -> None:
    sources = [s for s in _sources() if s.document_id == "doc-home"]
    ruled = await rule_clarification(assess("idk", None), "idk", sources)
    assert ruled.clarification is None  # only the overview is covered


# ---------------------------------------------------------------------------
# Picks fold into fixed queries
# ---------------------------------------------------------------------------


def _history(query: str) -> tuple[list[ChatTurn], Clarification]:
    card = _card(query)
    return (
        [
            ChatTurn(role="user", content=query),
            ChatTurn(
                role="assistant",
                content="q",
                outcome=CLARIFY_OUTCOME,
                clarification=card,
            ),
        ],
        card,
    )


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_a_goal_pick_replaces_the_vague_original(index: int) -> None:
    history, card = _history("i need help")
    option = card.questions[0].options[index]
    resolved = resolve_clarification_reply(
        "",
        history,
        [ClarificationAnswer(question_id="Q1", option_ids=[option.id])],
    )
    assert resolved is not None
    assert resolved.query == GOAL_QUERIES[option.label]
    assert "(Goal" not in resolved.query
    assert resolved.document_ids == option.document_ids


def test_exact_pick_queries() -> None:
    assert GOAL_QUERIES["Understand what you do"] == (
        "What does DeployIQ do and which services does it offer to "
        "organisations like yours?"
    )
    assert (
        GOAL_QUERIES["Get audit ready"]
        == GOAL_AUDIT_QUERY
        == (
            "How does DeployIQ help an organisation get audit ready: which frameworks "
            "(Essential Eight, ISO 27001, CIS) does it cover and what evidence and "
            "timeline?"
        )
    )
    assert GOAL_QUERIES["Fix or tighten security"] == (
        "How does DeployIQ assess and improve Microsoft 365 security: Security "
        "Baseline Assessment, Zero Trust, CIS Benchmark?"
    )
    assert GOAL_QUERIES["Pricing or talk to someone"] == (
        "How do I get pricing and talk to a DeployIQ specialist: engagement "
        "model, timelines, contact?"
    )


def test_a_pick_keeps_an_original_that_has_context() -> None:
    history, card = _history("we are a small business")
    resolved = resolve_clarification_reply(
        "",
        history,
        [
            ClarificationAnswer(
                question_id="Q1", option_ids=[card.questions[0].options[0].id]
            )
        ],
    )
    assert resolved is not None
    assert resolved.query.startswith(GOAL_QUERIES["Understand what you do"])
    assert resolved.query.endswith("(we are a small business)")


def test_other_keeps_the_free_text_fold() -> None:
    history, _ = _history("i need help")
    resolved = resolve_clarification_reply(
        "",
        history,
        [
            ClarificationAnswer(
                question_id="Q1", option_ids=[], other_text="email security"
            )
        ],
    )
    assert resolved is not None
    assert resolved.query == "i need help (Goal: email security)"


async def test_get_audit_ready_chains_into_the_framework_series(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    history, card = _history("i need help")
    pick = next(o for o in card.questions[0].options if o.label == "Get audit ready")
    resolved = resolve_clarification_reply(
        "", history, [ClarificationAnswer(question_id="Q1", option_ids=[pick.id])]
    )
    assert resolved is not None
    response, calls, _ = await _run(
        monkeypatch,
        resolved.query,
        history=history,
        clarification_reply=True,
        boost_document_ids=resolved.document_ids,
    )
    assert calls == []
    assert response.outcome == CLARIFY_OUTCOME
    assert response.clarification is not None
    headers = [q.header for q in response.clarification.questions]
    assert headers[:2] == ["Framework", "Maturity"]
    # After that card the reply is answered: no more cards.
    follow = [
        *history,
        ChatTurn(
            role="assistant",
            content="q2",
            outcome=CLARIFY_OUTCOME,
            clarification=response.clarification,
        ),
    ]
    assert assess("Essential Eight; Partly", follow, clarification_reply=True).kind is (
        AmbiguityKind.NONE
    )


def test_other_goal_picks_never_chain() -> None:
    history, card = _history("i need help")
    for option in card.questions[0].options:
        if option.label == "Get audit ready":
            continue
        query = GOAL_QUERIES[option.label]
        assert assess(query, history, clarification_reply=True).kind is (
            AmbiguityKind.NONE
        )


@pytest.mark.parametrize(
    "query",
    [
        "we need to be audit ready",
        "We need to be audit ready by June",
        "how do we get audit ready",
        "I want to be audit-ready",
    ],
)
def test_audit_ready_reaches_the_framework_series(query: str) -> None:
    assessment = assess(query, None)
    assert assessment.kind is AmbiguityKind.SLOTS
    assert assessment.missing_slots[:2] == ("framework", "maturity")


# ---------------------------------------------------------------------------
# Seeded fuzz: vague -> never a dead end, clear -> never a goal card
# ---------------------------------------------------------------------------

_VAGUE_CORES = (
    "i need help",
    "we need advice",
    "i want to know about stuff",
    "we have a question",
    "i have a question",
    "not sure where to start",
    "no idea",
    "idk",
    "i am confused",
    "we are confused",
    "what should we do",
    "what should we buy",
    "what's best",
    "what are my options",
    "compare your options",
    "recommend something",
    "tell me something",
    "we are a small business",
    "i run a small company",
    "worried about hackers",
    "our tenant has problems",
    "my boss asked me to look into this",
    "where do i start",
    "how do i get started",
    "we need to be audit ready",
    "we need to get compliant",
    "i want to improve our security",
    "something about security",
)
_CLEAR_CORES = (
    "What does Essential Eight maturity level 2 require",
    "How much does the CIS Benchmark assessment cost",
    "E8 pricing",
    "Tell me about Zero Trust",
    "What is ISO 27001",
    "How long does a Security Baseline Assessment take",
    "Is Essential Eight better than ISO 27001",
    "Where is Exigo Tech located",
    "Who founded DeployIQ",
    "What is the weather today",
    "What is a cat",
    "How do I bake bread",
    "Explain conditional access in Microsoft Entra",
    "Do you support Intune device compliance",
    "How does DeployIQ work",
    "We got hacked, what do we do",
    "Our Microsoft 365 tenant was breached",
    "Can you assess our Azure subscription",
    "What certifications does Exigo Tech hold",
    "How do I contact DeployIQ support",
)


def _variants(rng: random.Random, cores: tuple[str, ...], count: int) -> list[str]:
    prefixes = ("", "", "hi, ", "ok ", "so ", "hey ", "um ")
    suffixes = ("", "", "?", ".", " please", "!", " ?")
    out: list[str] = []
    while len(out) < count:
        text = rng.choice(prefixes) + rng.choice(cores) + rng.choice(suffixes)
        out.append(rng.choice((text, text.lower(), text.capitalize())))
    return out


def test_fuzz_vague_prompts_are_never_a_dead_end() -> None:
    rng = random.Random(20261028)  # noqa: S311
    prompts = _variants(rng, _VAGUE_CORES, 120)
    handled = [q for q in prompts if assess(q, None).kind is not AmbiguityKind.NONE]
    assert len(handled) / len(prompts) >= 0.95, sorted(set(prompts) - set(handled))


def test_fuzz_clear_prompts_never_get_a_goal_card() -> None:
    rng = random.Random(20261029)  # noqa: S311
    prompts = _variants(rng, _CLEAR_CORES, 120)
    bad = [q for q in prompts if assess(q, None).kind is AmbiguityKind.GOAL]
    assert not bad, sorted(set(bad))
