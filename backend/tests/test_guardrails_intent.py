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


@pytest.mark.parametrize(
    "text",
    [
        "Does your AI make binding decisions?",
        "When AI is used, who stays accountable?",
        "Are the recommendations AI-generated or reviewed by humans?",
    ],
)
def test_ai_governance_questions_never_get_the_identity_reply(text: str) -> None:
    index = _one_row_index(Intent.IDENTITY, ex.IDENTITY, "is this an AI?")
    match = intent.nearest_intent(text, _canned_vec(), index)
    assert not match.result.pure
    assert match.reply_key is None


@pytest.mark.parametrize(
    ("label", "reply", "example", "text"),
    [
        (
            Intent.INCIDENT,
            ex.INCIDENT,
            "we have ransomware",
            "How does ransomware encrypt files?",
        ),
        (
            Intent.ACCOUNT_SUPPORT,
            ex.MANAGER,
            "get me your boss",
            "My boss hates me, what should I do?",
        ),
        (
            Intent.CAPABILITY,
            ex.ASSURANCE,
            "do you guarantee security",
            "Do you guarantee uninterrupted delivery?",
        ),
        (
            Intent.CAPABILITY,
            ex.ASSURANCE,
            "do you guarantee security",
            "do you guarantee results",
        ),
    ],
)
def test_reply_needs_what_it_is_for(
    label: Intent, reply: str, example: str, text: str
) -> None:
    match = intent.nearest_intent(
        text, _canned_vec(), _one_row_index(label, reply, example)
    )
    assert not match.result.pure
    assert match.reply_key is None


@pytest.mark.parametrize(
    ("label", "reply", "text"),
    [
        (Intent.IDENTITY, ex.IDENTITY, "is this an AI?"),
        (Intent.IDENTITY, ex.IDENTITY, "who are you"),
        (Intent.CAPABILITY, ex.PERSONA_PRIVACY, "is our conversation confidential"),
        (Intent.INCIDENT, ex.INCIDENT, "a hacker got into our network"),
        (Intent.ACCOUNT_SUPPORT, ex.MANAGER, "let me talk to your supervisor"),
        (Intent.CAPABILITY, ex.ASSURANCE, "can you guarantee we will never be hacked?"),
        (Intent.CAPABILITY, ex.ASSURANCE, "promise me it's safe"),
    ],
)
def test_genuine_canned_intents_keep_their_reply(
    label: Intent, reply: str, text: str
) -> None:
    match = intent.nearest_intent(
        text, _canned_vec(), _one_row_index(label, reply, text)
    )
    assert match.result.pure
    assert match.reply_key == reply


@pytest.mark.parametrize(
    "text",
    [
        "I want to purchase Frontier for 200 users",
        "we'd like to buy the Starter package",
        "can we order Strengthen for our team",
    ],
)
def test_explicit_purchase_ask_is_buying_whatever_the_score(text: str) -> None:
    match = intent.nearest_intent(text, _vec(question=0.3, buying=0.1), _index())
    assert match.result.intent == Intent.BUYING
    assert not match.result.pure


@pytest.mark.parametrize(
    "text",
    [
        "My name is John Smith, email john.smith@acme.com.au, phone 0412 345 678. "
        "Can someone call me about Essential Eight?",
        "call me on 0412 345 678, I'm John from Acme",
        "please email me at jo@example.com about Zero Trust",
    ],
)
async def test_callback_with_own_details_gets_the_callback_reply(
    monkeypatch, text: str
) -> None:
    async def boom(*_a, **_k):
        raise AssertionError("no embedding for a callback with contact details")

    monkeypatch.setattr(intent.embeddings, "embed_text", boom)
    match = await intent.classify_intent(text)
    assert match.result.pure
    assert match.reply_key == ex.CALLBACK_DETAILS


@pytest.mark.parametrize(
    "text",
    [
        "what is your phone number",
        "can someone call me",
        "my number is 0412 345 678",
    ],
)
def test_callback_rule_needs_an_ask_and_own_details(text: str) -> None:
    assert not intent.callback_with_details(text)


@pytest.mark.parametrize(
    ("text", "buying"),
    [
        ("How much is Frontier?", True),
        ("What does Frontier cost?", True),
        ("By how much did ransomware drop?", False),
        ("Do I need to sign in to confirm a booking?", False),
        ("book a demo", True),
    ],
)
def test_buying_cue_needs_price_or_purchase_wording(text: str, buying: bool) -> None:
    assert intent.has_buying_cue(text) is buying


@pytest.mark.parametrize("text", ["quote", "quote pls", "quote for Zero Trust"])
def test_short_quote_ask_is_buying_at_a_low_score(text: str) -> None:
    # fin-clar-quote-name: "quote" scored 0.29 against the buying examples.
    match = intent.nearest_intent(text, _vec(question=0.29, buying=0.2), _index())
    assert match.result.intent == Intent.BUYING
    # Live, "quote" sat nearest an unclear "bye" (0.41): still buying.
    unclear = intent.nearest_intent(
        text, _vec(noise=0.8, question=0.29, greeting=0.41, buying=0.2), _index()
    )
    assert unclear.result.intent == Intent.BUYING


def test_clear_small_talk_with_a_price_word_keeps_its_label() -> None:
    match = intent.nearest_intent(
        "your prices are a joke", _vec(complaint=0.9, question=0.3), _index()
    )
    assert match.result.intent == Intent.COMPLAINT


def test_long_quote_question_still_needs_the_buying_score() -> None:
    text = "what does the word quote mean in the terms of service page"
    match = intent.nearest_intent(text, _vec(question=0.6, buying=0.2), _index())
    assert match.result.intent == Intent.QUESTION


@pytest.mark.parametrize(
    "text",
    [
        "Who will I speak with if I book a session?",
        "how do I get started?",
        "what does the demo include?",
    ],
)
def test_booking_info_question_is_not_buying(text: str) -> None:
    # sec-a-booking-intro-01: answered from the booking page, no sales pitch.
    assert intent.is_booking_info_question(text)
    match = intent.nearest_intent(text, _vec(question=0.5, buying=0.6), _index())
    assert match.result.intent == Intent.QUESTION


@pytest.mark.parametrize(
    "text",
    ["book a demo", "can I book a demo?", "what does the demo cost?"],
)
def test_booking_request_or_price_ask_stays_buying(text: str) -> None:
    assert not intent.is_booking_info_question(text)
    match = intent.nearest_intent(text, _vec(question=0.5, buying=0.6), _index())
    assert match.result.intent == Intent.BUYING


@pytest.mark.parametrize(
    ("label", "reply", "example", "text"),
    [
        # para-phone: the visitor wants our number, not a callback.
        (
            Intent.CONTACT,
            ex.CALLBACK,
            "please ring me",
            "whats the best number to ring you on",
        ),
        # ob-02: a privacy question near "who made this chatbot".
        (
            Intent.IDENTITY,
            ex.PERSONA_CREATOR,
            "who made this chatbot",
            "what happens to the stuff i type into this chatbot thing?",
        ),
    ],
)
def test_persona_and_callback_replies_need_their_cue(
    label: Intent, reply: str, example: str, text: str
) -> None:
    match = intent.nearest_intent(
        text, _canned_vec(), _one_row_index(label, reply, example)
    )
    assert not match.result.pure
    assert match.reply_key is None


@pytest.mark.parametrize(
    ("label", "reply", "text"),
    [
        (Intent.CONTACT, ex.CALLBACK, "give me a call on 0400 000 000"),
        (Intent.CONTACT, ex.CALLBACK, "can your team call me this afternoon"),
        (Intent.IDENTITY, ex.PERSONA_CREATOR, "who do you work for"),
        (Intent.IDENTITY, ex.PERSONA_MODEL, "are you built on openai"),
        (Intent.CAPABILITY, ex.PERSONA_LANGUAGES, "can I chat with you in Spanish"),
        (Intent.CAPABILITY, ex.PERSONA_PRIVACY, "does anyone read what I type here"),
    ],
)
def test_every_example_style_keeps_its_cue(
    label: Intent, reply: str, text: str
) -> None:
    match = intent.nearest_intent(
        text, _canned_vec(), _one_row_index(label, reply, text)
    )
    assert match.reply_key == reply


def test_every_canned_example_passes_its_own_guards() -> None:
    # A guard must never block the examples that define the reply.
    for _label, reply, text in ex.EXAMPLES:
        if reply is not None:
            assert not intent._key_mismatch(text, reply), text
