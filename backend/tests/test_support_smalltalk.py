"""Fixed replies for support, callback, assurance, identity and acknowledgements."""

from __future__ import annotations

import random
import re
import time

import pytest

from app.guardrails import conversational as conv
from app.harness import orchestrator
from app.harness.clarify import CLARIFY_OUTCOME
from app.schemas.query import ChatTurn

_BANNED = (
    "feel free",
    "let me know",
    "reach out",
    "i can't see",
    "knowledge base",
    chr(0x2014),
    chr(0x2013),
    " - ",
)

_SUPPORT = {
    "cancel my subscription": conv.SUPPORT_CANCEL_REPLY,
    "I want to cancel": conv.SUPPORT_CANCEL_REPLY,
    "cancellation please": conv.SUPPORT_CANCEL_REPLY,
    "I want a refund": conv.SUPPORT_CANCEL_REPLY,
    "how do I unsubscribe?": conv.SUPPORT_CANCEL_REPLY,
    "close my account": conv.SUPPORT_CANCEL_REPLY,
    "how do I cancel my Essential Eight assessment": conv.SUPPORT_CANCEL_REPLY,
    "what is the cancellation policy for DeployIQ": conv.SUPPORT_CANCEL_REPLY,
    "your product broke my tenant": conv.SUPPORT_BROKEN_REPLY,
    "it stopped working": conv.SUPPORT_BROKEN_REPLY,
    "this is useless": conv.SUPPORT_FRUSTRATION_REPLY,
    "this is rubbish!": conv.SUPPORT_FRUSTRATION_REPLY,
    "not happy": conv.SUPPORT_FRUSTRATION_REPLY,
    "I'm not happy with this": conv.SUPPORT_FRUSTRATION_REPLY,
    "I want to make a complaint": conv.SUPPORT_FRUSTRATION_REPLY,
    "i want to speak to a manager": conv.SUPPORT_MANAGER_REPLY,
}

_CALLBACK = [
    "call me",
    "phone me",
    "ring me",
    "can someone call me",
    "call me on 0412 345 678 about pricing",
]

_ASSURANCE = [
    "confirm that deployiq is 100% secure",
    "is it 100% secure",
    "is Zero Trust 100% secure?",
    "do you guarantee security",
    "promise me it's safe",
    "is it hack proof",
    "is it unhackable",
    "is it bulletproof",
]

_IDENTITY = [
    "what is your name",
    "who are you",
    "who made you",
    "who built you",
    "what model are you",
    "which AI are you",
    "are you a bot",
    "are you human?",
    "are you real",
    "are you chatgpt",
]

_ACKS = [
    "yes",
    "No",
    "ok",
    "okay",
    "k",
    "sure",
    "cool",
    "lol",
    "haha",
    "test",
    "testing",
    "\U0001f44d",
    "nice",
    "great",
    "hm",
    "hmm",
    "right",
    "alright!",
]


def _clean(reply: str) -> None:
    lowered = reply.lower()
    assert not any(b in lowered for b in _BANNED), reply
    assert not re.search(r"\d{3}", reply), reply
    assert "{" not in reply


@pytest.mark.parametrize(("query", "expected"), list(_SUPPORT.items()))
def test_support_requests(query: str, expected: str) -> None:
    reply = conv.detect_support_request(query)
    assert reply == expected
    _clean(reply)
    assert "specialist" in reply or "team" in reply
    assert "Contact page" in reply


def test_human_request_stays_with_contact_detector() -> None:
    for q in (
        "can i speak to a human",
        "can I speak to someone",
        "can i speak to a person",
    ):
        assert conv.detect_support_request(q) is None
        assert conv.detect_contact_request(q) is not None


@pytest.mark.parametrize("query", _CALLBACK)
def test_callback_requests(query: str) -> None:
    reply = conv.detect_callback_request(query)
    assert reply == conv.CALLBACK_REPLY
    _clean(reply)
    assert "0412" not in reply
    assert "team" in reply


@pytest.mark.parametrize("query", _ASSURANCE)
def test_assurance_requests(query: str) -> None:
    reply = conv.detect_assurance_request(query)
    assert reply == conv.ASSURANCE_REPLY
    assert "specialist" in reply
    assert not any(b in reply.lower() for b in _BANNED)


@pytest.mark.parametrize("query", _IDENTITY)
def test_identity_questions(query: str) -> None:
    reply = conv.detect_identity_question(query)
    assert reply is not None
    _clean(reply)
    assert "DeployIQ" in reply or "assistant" in reply
    assert "{" not in reply


@pytest.mark.parametrize("query", _ACKS)
def test_acknowledgements(query: str) -> None:
    reply = conv.detect_acknowledgement(query)
    assert reply in conv.all_acknowledgement_responses()
    _clean(reply)
    assert conv.detect_acknowledgement(query) == reply


def test_acknowledgement_has_three_variants_and_is_whole_message_only() -> None:
    assert len(set(conv.all_acknowledgement_responses())) == 3
    for q in ("yes I would like pricing", "no thanks", "ok so what is Zero Trust"):
        assert conv.detect_acknowledgement(q) is None


async def _run(query: str, history: list[ChatTurn] | None = None, **kw):
    return await orchestrator.run_query(
        query=query,
        top_k=3,
        filters=None,
        vector_store=None,  # type: ignore[arg-type]
        request_id="r",
        stage_timings={},
        history=history,
        **kw,
    )


@pytest.fixture
def no_retrieval(monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("retrieval must not run for a canned reply")

    monkeypatch.setattr(orchestrator, "retrieve", boom)


@pytest.mark.parametrize(
    "query",
    [
        "cancel my subscription",
        "call me on 0412 345 678 about pricing",
        "is it 100% secure",
        "what is your name",
        "ok",
        "this is useless",
    ],
)
async def test_orchestrator_routes_to_canned(no_retrieval, query: str) -> None:
    response = await _run(query)
    assert response.outcome == "conversational"
    assert response.sources == []
    assert response.guardrail_flags == []
    assert "0412" not in response.answer


async def test_orchestrator_priority_support_before_contact(no_retrieval) -> None:
    response = await _run("I want a refund, how do I contact you")
    assert response.answer == conv.SUPPORT_CANCEL_REPLY


async def test_clarification_reply_is_not_intercepted(monkeypatch) -> None:
    reached = []

    async def fake_retrieve(*_a, **_k):
        reached.append(True)
        raise RuntimeError("past canned routing")

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    history = [
        ChatTurn(role="user", content="tell me about security"),
        ChatTurn(role="assistant", content="Which one?", outcome=CLARIFY_OUTCOME),
    ]
    for query in ("yes", "ok"):
        reached.clear()
        with pytest.raises(RuntimeError):
            await _run(query, history)
        assert reached

    reached.clear()
    with pytest.raises(RuntimeError):
        await _run("yes", None, clarification_reply=True)
    assert reached


async def test_ack_intercepted_after_normal_answer(no_retrieval) -> None:
    history = [
        ChatTurn(role="user", content="what is Zero Trust"),
        ChatTurn(role="assistant", content="It is a model.", outcome="answered"),
    ]
    response = await _run("ok", history)
    assert response.outcome == "conversational"


_TOPICS = (
    "DeployIQ",
    "Essential Eight",
    "Zero Trust",
    "the Starter plan",
    "ISO 27001 support",
    "patching",
    "application control",
    "multi-factor authentication",
    "backups",
    "the assessment service",
)
_STEMS = (
    "What does {t} include?",
    "How does {t} work?",
    "Tell me more about {t}",
    "Who is {t} designed for?",
    "Explain {t} in simple terms",
    "Which frameworks does {t} map to?",
    "What are the benefits of {t}?",
    "How long does {t} take to set up?",
    "Does {t} cover small businesses?",
    "What evidence does {t} produce?",
    "How is {t} different from a standard audit?",
    "Where can I learn about {t}?",
)


def _negatives() -> list[str]:
    rng = random.Random(20260101)  # noqa: S311
    pairs = [(s, t) for s in _STEMS for t in _TOPICS]
    rng.shuffle(pairs)
    return [s.format(t=t) for s, t in pairs[:120]]


_HELPERS = (
    conv.detect_support_request,
    conv.detect_callback_request,
    conv.detect_assurance_request,
    conv.detect_identity_question,
    conv.detect_acknowledgement,
)


def test_negative_set_matches_no_helper() -> None:
    negatives = _negatives()
    assert len(set(negatives)) == 120
    for query in negatives:
        for helper in _HELPERS:
            assert helper(query) is None, (helper.__name__, query)


def test_named_service_questions_are_not_blocked() -> None:
    for q in (
        "How does DeployIQ keep my data safe?",
        "What security controls does Zero Trust include?",
        "Is the Starter plan free?",
    ):
        for helper in _HELPERS:
            assert helper(q) is None, (helper.__name__, q)


@pytest.mark.parametrize(
    "filler", ["a ", "call ", "100 ", "guarantee ", "are you ", "cancel "]
)
def test_adversarial_input_is_fast(filler: str) -> None:
    payloads = [
        filler * 100,
        (filler + "  ") * 60,
        "guarantee " + "x " * 140 + "safe",
        "promise" + " a" * 140,
        "this is " + "so " * 90,
    ]
    start = time.perf_counter()
    for payload in payloads:
        for helper in _HELPERS:
            helper(payload[:300])
            helper(payload[:200])
    assert time.perf_counter() - start < 0.5
