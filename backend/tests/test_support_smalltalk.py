# pylint: disable=redefined-outer-name,unused-argument
"""Fixed replies for support, callback, assurance, identity and acknowledgements.

Support, callback, complaint and assurance replies are chosen by the intent
classifier (tests/test_guardrails_intent.py covers the decision rules); here
the reply texts, the anchored identity/acknowledgement patterns and the
orchestrator routing with a stubbed classifier.
"""

from __future__ import annotations

import random
import re
import time

import pytest

from app.guardrails import conversational as conv
from app.guardrails import intent_examples as ex
from app.guardrails.intent import IntentMatch
from app.harness import orchestrator
from app.harness.clarify import CLARIFY_OUTCOME
from app.harness.decision import Intent, IntentResult
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

_FIXED_REPLIES = {
    ex.CANCEL: conv.SUPPORT_CANCEL_REPLY,
    ex.BROKEN: conv.SUPPORT_BROKEN_REPLY,
    ex.MANAGER: conv.SUPPORT_MANAGER_REPLY,
    ex.FRUSTRATION: conv.SUPPORT_FRUSTRATION_REPLY,
    ex.CALLBACK: conv.CALLBACK_REPLY,
    ex.CALLBACK_DETAILS: conv.CALLBACK_DETAILS_REPLY,
    ex.ASSURANCE: conv.ASSURANCE_REPLY,
    ex.INCIDENT: conv.INCIDENT_REPLY,
}

# Reply texts kept as they were (they mention "100%" / "reach out" on purpose).
_UNCHANGED_TEXTS = {ex.ASSURANCE, ex.INCIDENT}

_IDENTITY = [
    "what is your name",
    "who are you",
    "who are u",
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
    # No phone numbers (the product name "Microsoft 365" is fine).
    assert not re.search(r"\d{3}", reply.replace("Microsoft 365", "")), reply
    assert "{" not in reply


@pytest.mark.parametrize(("key", "expected"), list(_FIXED_REPLIES.items()))
def test_reply_keys_map_to_fixed_replies(key: str, expected: str) -> None:
    reply = conv.canned_reply_for(key, "anything")
    assert reply == expected
    # Voice guide: no specialist hand-off outside the sales line.
    assert "specialist" not in reply.lower()
    if key not in _UNCHANGED_TEXTS:
        _clean(reply)


def test_every_example_reply_key_renders() -> None:
    keys = {row[1] for row in ex.EXAMPLES if row[1] is not None}
    for key in keys:
        reply = conv.canned_reply_for(key, "hello")
        assert reply, key
        if key not in _UNCHANGED_TEXTS:
            _clean(reply)
    assert conv.canned_reply_for(None, "hi") is None
    assert conv.canned_reply_for("no-such-key", "hi") is None


def test_support_and_callback_replies_point_to_contact_page() -> None:
    for key in (
        ex.CANCEL,
        ex.BROKEN,
        ex.MANAGER,
        ex.FRUSTRATION,
        ex.CALLBACK,
        ex.CALLBACK_DETAILS,
    ):
        reply = conv.canned_reply_for(key, "x")
        assert "Contact page" in reply
        assert "team" in reply


def test_human_request_stays_with_contact_detector() -> None:
    for q in (
        "can i speak to a human",
        "can I speak to someone",
        "can i speak to a person",
        "let me chat with a real person",
    ):
        assert conv.detect_contact_request(q) is not None


@pytest.mark.parametrize(
    "query",
    [
        "where are your offices?",
        "Sydney office address?",
        "do you have a Melbourne office?",
        "what about email?",
        "What is your phone number?",
        "How do I contact you?",
        "contact details",
        "security",
    ],
)
def test_contact_details_are_not_canned(query: str) -> None:
    """The Contact Us page answers these, so they reach retrieval."""
    assert conv.detect_contact_request(query) is None
    assert conv.detect_conversational_intent(query) is None
    assert conv.detect_identity_question(query) is None
    assert conv.detect_incident_request(query) is None


@pytest.mark.parametrize("query", _IDENTITY)
def test_identity_questions(query: str) -> None:
    reply = conv.detect_identity_question(query)
    assert reply is not None
    _clean(reply)
    assert "DeployIQ" in reply or "assistant" in reply


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

    monkeypatch.setattr(orchestrator, "retrieve_parts", boom)


def _stub_classifier(monkeypatch, intent: Intent, key: str | None, pure: bool):
    seen: list[str] = []

    async def fake(text: str) -> IntentMatch:
        seen.append(text)
        return IntentMatch(IntentResult(intent, 0.9, pure), key if pure else None)

    monkeypatch.setattr(orchestrator, "classify_intent", fake)
    return seen


@pytest.mark.parametrize(
    ("query", "intent", "key"),
    [
        ("cancel my subscription", Intent.ACCOUNT_SUPPORT, ex.CANCEL),
        ("call me on 0412 345 678", Intent.CONTACT, ex.CALLBACK),
        ("is it 100% secure", Intent.CAPABILITY, ex.ASSURANCE),
        ("this is useless", Intent.COMPLAINT, ex.FRUSTRATION),
    ],
)
async def test_pure_intent_routes_to_canned(
    no_retrieval, monkeypatch, query: str, intent: Intent, key: str
) -> None:
    _stub_classifier(monkeypatch, intent, key, pure=True)
    response = await _run(query)
    assert response.outcome == "conversational"
    assert response.answer == _FIXED_REPLIES[key]
    assert response.sources == []
    assert response.guardrail_flags == []
    assert "0412" not in response.answer


async def test_impure_intent_reaches_retrieval(monkeypatch) -> None:
    reached = []

    async def fake_retrieve(*_a, **_k):
        reached.append(True)
        raise RuntimeError("past canned routing")

    monkeypatch.setattr(orchestrator, "retrieve_parts", fake_retrieve)
    _stub_classifier(monkeypatch, Intent.ACCOUNT_SUPPORT, ex.CANCEL, pure=False)
    with pytest.raises(RuntimeError):
        await _run("Can DeployIQ cancel stale admin sessions?")
    assert reached


async def test_classifier_sees_normalised_text(monkeypatch) -> None:
    async def fake_retrieve(*_a, **_k):
        raise RuntimeError("past canned routing")

    monkeypatch.setattr(orchestrator, "retrieve_parts", fake_retrieve)
    seen = _stub_classifier(monkeypatch, Intent.QUESTION, None, pure=False)
    with pytest.raises(RuntimeError):
        await _run("<b>\u00bfwhat is Deplo\u200byIQ</b>")
    assert seen == ["what is DeployIQ"]


async def test_anchored_patterns_win_without_the_classifier(no_retrieval) -> None:
    # The conftest keeps the classifier offline: anchored replies still work.
    named = await _run("what is your name")
    assert named.outcome == "blocked"
    assert named.token_details["refusal_template"] == "persona_name"
    assert (await _run("ok")).outcome == "conversational"
    assert (await _run("we got hacked")).answer == conv.INCIDENT_REPLY


async def test_clarification_reply_is_not_intercepted(monkeypatch) -> None:
    reached = []

    async def fake_retrieve(*_a, **_k):
        reached.append(True)
        raise RuntimeError("past canned routing")

    monkeypatch.setattr(orchestrator, "retrieve_parts", fake_retrieve)
    _stub_classifier(monkeypatch, Intent.ACKNOWLEDGEMENT, ex.ACK, pure=True)
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
    conv.detect_incident_request,
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
        ex.names_catalogue_term(payload)
    assert time.perf_counter() - start < 0.5


_IDENTITY_PROBES = (
    "Are you a real person?",
    "are you human",
    "Do you have feelings?",
    "do you have emotions or opinions?",
    "Do you remember me?",
    "How old are you?",
    "Where do you live?",
)
_REGULATED = (
    "Can my company be fined under the Privacy Act?",
    "Is this liability clause enforceable?",
    "Should I invest in crypto?",
    "Is my cybersecurity spend tax deductible?",
    "Can I sue my employer for unfair dismissal?",
)
_KB_QUESTIONS = (
    "What does your privacy policy say about data retention?",
    "What does your ISO 27001 service cover?",
    "Where are you located?",
    "Do you have an ISO 27001 service?",
    "How does Zero Trust reduce liability?",
    "Is it fine to use MFA?",
)


@pytest.mark.parametrize("query", _IDENTITY_PROBES)
async def test_identity_probe_gets_identity_template(no_retrieval, query: str) -> None:
    response = await _run(query)
    assert response.outcome == "blocked"
    assert response.guardrail_flags == ["identity_probe"]
    assert response.token_details["refusal_template"] in {
        "identity",
        "persona_human",
        "persona_memory",
    }
    assert "virtual assistant" in response.answer or "conversation" in response.answer
    assert response.sources == []
    assert response.confidence == 0.0


@pytest.mark.parametrize("query", _REGULATED)
def test_regulated_advice_detected(query: str) -> None:
    assert conv.pre_llm_policy(query, regulated=True) == conv.REGULATED_POLICY
    assert conv.pre_llm_policy(query, regulated=False) is None


@pytest.mark.parametrize("query", _KB_QUESTIONS)
def test_kb_questions_are_no_policy_refusal(query: str) -> None:
    assert conv.pre_llm_policy(query, regulated=True) is None


@pytest.mark.parametrize("query", ("zt", "e8", "mdr", "sba", "ml2", "professional"))
def test_kb_abbreviations_name_the_catalogue(query: str) -> None:
    assert ex.names_catalogue_term(query)
