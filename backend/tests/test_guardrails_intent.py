# pylint: disable=protected-access
"""Intent classifier decision rules (app.guardrails.intent), offline.

The vectors are synthetic (one axis per label), so these tests check the
decision rules only: purity, catalogue terms, margins, buying cue, fail-open.
Real-embedding behaviour is verified live (scripts/live_chat_client.py).
"""

from __future__ import annotations

import numpy as np
import pytest

from app.guardrails import intent
from app.guardrails import intent_examples as ex
from app.harness.decision import Intent

_AXES = {
    Intent.QUESTION: 0,
    Intent.ACCOUNT_SUPPORT: 1,
    Intent.COMPLAINT: 2,
    Intent.BUYING: 3,
    Intent.GREETING: 4,
}


def _index() -> intent._Index:
    rows = [
        (Intent.QUESTION, None, "what is DeployIQ"),
        (Intent.ACCOUNT_SUPPORT, ex.CANCEL, "I want to cancel my subscription"),
        (Intent.COMPLAINT, ex.FRUSTRATION, "this is useless"),
        (Intent.BUYING, None, "how much does it cost"),
        (Intent.GREETING, ex.GREETING, "hi"),
    ]
    # One extra, unused axis: a query can sit far from every example.
    matrix = np.eye(len(_AXES), len(_AXES) + 1, dtype=np.float32)
    return intent._Index(
        model="fake",
        matrix=matrix,
        intents=tuple(r[0] for r in rows),
        replies=tuple(r[1] for r in rows),
        texts=tuple(r[2] for r in rows),
    )


def _vec(noise: float = 0.0, **weights: float) -> list[float]:
    vector = [0.0] * len(_AXES) + [noise]
    for name, weight in weights.items():
        vector[_AXES[Intent(name)]] = weight
    return vector


def test_pure_support_intent_gets_its_reply_key() -> None:
    match = intent.nearest_intent(
        "I want to cancel my subscription",
        _vec(account_support=0.9, question=0.3),
        _index(),
    )
    assert match.result.intent == Intent.ACCOUNT_SUPPORT
    assert match.result.pure
    assert match.reply_key == ex.CANCEL


@pytest.mark.parametrize(
    "text",
    [
        "What happens to my data if I cancel?",
        "my MFA is not working, does DeployIQ help?",
        "I'm unhappy with my MSP, can you replace them?",
        "Does the Security Baseline Assessment cover 100% of the Essential Eight controls?",
        "Can DeployIQ cancel stale admin sessions?",
        "can I cancel the Foundation package",
    ],
)
def test_naming_the_catalogue_is_never_pure(text: str) -> None:
    assert ex.names_catalogue_term(text)
    match = intent.nearest_intent(text, _vec(account_support=0.95), _index())
    assert not match.result.pure
    assert match.reply_key is None


@pytest.mark.parametrize(
    "text",
    ["I want to cancel my subscription", "this bot is useless", "we got hacked", "hi"],
)
def test_pure_small_talk_names_no_catalogue_term(text: str) -> None:
    assert not ex.names_catalogue_term(text)


def test_question_nearest_is_question() -> None:
    match = intent.nearest_intent(
        "whatever", _vec(question=0.8, account_support=0.7), _index()
    )
    assert match.result.intent == Intent.QUESTION
    assert match.reply_key is None


def test_small_margin_over_question_is_question() -> None:
    match = intent.nearest_intent(
        "please stop", _vec(account_support=0.62, question=0.6), _index()
    )
    assert match.result.intent == Intent.QUESTION
    assert not match.result.pure


def test_low_score_is_question() -> None:
    match = intent.nearest_intent("meh", _vec(noise=0.9, complaint=0.3), _index())
    assert match.result.intent == Intent.QUESTION


def test_long_message_is_never_pure() -> None:
    text = "I want to cancel " + "and also tell you about my week " * 4
    match = intent.nearest_intent(text, _vec(account_support=0.9), _index())
    assert match.result.intent == Intent.ACCOUNT_SUPPORT
    assert not match.result.pure


def test_buying_needs_purchase_wording_and_is_never_canned() -> None:
    no_cue = intent.nearest_intent("what is CIS", _vec(buying=0.7), _index())
    assert no_cue.result.intent == Intent.QUESTION
    cue = intent.nearest_intent(
        "I want to buy DeployIQ", _vec(question=0.8, buying=0.5), _index()
    )
    assert cue.result.intent == Intent.BUYING
    assert not cue.result.pure
    assert cue.reply_key is None


async def test_classifier_fails_open(monkeypatch) -> None:
    monkeypatch.setattr(intent._state, "backoff_until", 0.0)

    async def broken(*_a, **_k):
        raise RuntimeError("no embeddings")

    monkeypatch.setattr(intent.embeddings, "embed_texts", broken)
    monkeypatch.setattr(intent._state, "index", None)
    monkeypatch.setattr(intent._state, "task", None)
    match = await intent.classify_intent("I want to cancel my subscription")
    assert match.result.intent == Intent.QUESTION
    assert intent.get_current_intent() == match.result
    assert intent._state.backoff_until > 0


async def test_classifier_uses_cached_index(monkeypatch) -> None:
    monkeypatch.setattr(intent._state, "backoff_until", 0.0)
    monkeypatch.setattr(intent._state, "index", _index())
    monkeypatch.setattr(
        intent,
        "current_model_settings",
        lambda: type("S", (), {"embedding_model": "fake"}),
    )
    calls: list[str] = []

    async def fake_embed(text: str, **_k) -> list[float]:
        calls.append(text)
        return _vec(complaint=0.9)

    monkeypatch.setattr(intent.embeddings, "embed_text", fake_embed)
    match = await intent.classify_intent("this is useless")
    assert calls == ["this is useless"]
    assert match.reply_key == ex.FRUSTRATION
    assert intent.get_current_intent().intent == Intent.COMPLAINT


async def test_overlong_input_skips_the_embedding(monkeypatch) -> None:
    monkeypatch.setattr(intent._state, "backoff_until", 0.0)

    async def boom(*_a, **_k):
        raise AssertionError("no embedding for over-long input")

    monkeypatch.setattr(intent.embeddings, "embed_text", boom)
    match = await intent.classify_intent("x " * 400)
    assert match.result.intent == Intent.QUESTION


def test_examples_are_well_formed() -> None:
    texts = [row[2] for row in ex.EXAMPLES]
    assert len(texts) == len(set(texts))
    for label, reply, text in ex.EXAMPLES:
        assert isinstance(label, Intent)
        assert text.strip() == text
        if label in (Intent.QUESTION, Intent.BUYING):
            assert reply is None
        else:
            assert reply is not None


def _one_row_index(label: Intent, reply: str, text: str) -> intent._Index:
    """QUESTION on axis 0 and one canned label on axis 1."""
    rows = [(Intent.QUESTION, None, "what is DeployIQ"), (label, reply, text)]
    return intent._Index(
        model="fake",
        matrix=np.eye(2, 3, dtype=np.float32),
        intents=tuple(r[0] for r in rows),
        replies=tuple(r[1] for r in rows),
        texts=tuple(r[2] for r in rows),
    )


def _canned_vec() -> list[float]:
    return [0.3, 0.9, 0.0]


@pytest.mark.parametrize(
    "text",
    [
        "where are your offices?",
        "Sydney office address?",
        "do you have a Melbourne office?",
        "what is your phone number",
        "what's your email address",
        "how do I contact you?",
        "how can I get in touch",
        "what about email?",
        "and pricing?",
    ],
)
def test_contact_details_and_follow_ups_are_never_pure(text: str) -> None:
    index = _one_row_index(Intent.CONTACT, ex.CONTACT, "I want to talk to a human")
    match = intent.nearest_intent(text, _canned_vec(), index)
    assert not match.result.pure
    assert match.reply_key is None


@pytest.mark.parametrize(
    ("label", "reply", "text"),
    [
        (Intent.CONTACT, ex.CONTACT, "can I speak to someone"),
        (Intent.CONTACT, ex.CALLBACK, "call me back on 0412 345 678"),
        (Intent.INCIDENT, ex.INCIDENT, "our email was compromised"),
        (Intent.CAPABILITY, ex.ASSURANCE, "is it 100% secure?"),
        (Intent.CAPABILITY, ex.ASSURANCE, "do you guarantee security"),
        (Intent.THANKS, ex.THANKS, "you're awesome"),
    ],
)
def test_pure_support_and_small_talk_keep_their_reply(
    label: Intent, reply: str, text: str
) -> None:
    match = intent.nearest_intent(
        text, _canned_vec(), _one_row_index(label, reply, text)
    )
    assert match.result.pure
    assert match.reply_key == reply


@pytest.mark.parametrize("text", ["security", "cyber security", "is it secure"])
def test_assurance_needs_guarantee_wording(text: str) -> None:
    index = _one_row_index(Intent.CAPABILITY, ex.ASSURANCE, "is it 100% secure?")
    match = intent.nearest_intent(text, _canned_vec(), index)
    assert not match.result.pure
    assert match.reply_key is None


def test_contact_detail_regexes_are_bounded() -> None:
    payload = "office " * 5000 + "what about " * 5000
    assert ex.names_contact_detail(payload)
    assert not ex.is_follow_up_fragment(" " * 5000 + "what about email")
