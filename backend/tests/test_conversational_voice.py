"""Canned small-talk replies: human voice, stable variants, no AI tells."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.guardrails.conversational import (
    all_capability_responses,
    all_farewell_responses,
    all_greeting_responses,
    all_thanks_responses,
    detect_conversational_intent,
    get_capability_response,
    get_farewell_response,
    get_greeting_response,
    get_thanks_response,
)

_BANNED = (
    "feel free",
    "let me know",
    "i'll find",
    "reach out",
    "knowledge base",
    "supplied context",
    chr(0x2014),
    chr(0x2013),
)

_INTENTS = {
    "greeting": (all_greeting_responses, get_greeting_response, True),
    "thanks": (all_thanks_responses, get_thanks_response, False),
    "farewell": (all_farewell_responses, get_farewell_response, False),
    "capability": (all_capability_responses, get_capability_response, False),
}


@pytest.mark.parametrize("intent", list(_INTENTS))
def test_every_variant_is_clean(intent: str) -> None:
    all_fn, _, _ = _INTENTS[intent]
    variants = all_fn()
    assert len(variants) == 3
    assert len(set(variants)) == 3
    for reply in variants:
        lowered = reply.lower()
        assert not any(b in lowered for b in _BANNED), reply
        assert "{brand}" not in reply
        assert "{" not in reply


def test_greeting_variants_name_the_brand() -> None:
    brand = get_settings().brand_name
    assert all(brand in reply for reply in all_greeting_responses())


@pytest.mark.parametrize("intent", list(_INTENTS))
def test_variant_is_stable_for_same_query(intent: str) -> None:
    _, get_fn, _ = _INTENTS[intent]
    assert get_fn("hello there") == get_fn("hello there")
    assert get_fn("  Hello There ") == get_fn("hello there")


@pytest.mark.parametrize("intent", list(_INTENTS))
def test_variant_varies_across_queries(intent: str) -> None:
    all_fn, get_fn, _ = _INTENTS[intent]
    seen = {get_fn(f"query {i}") for i in range(40)}
    assert seen == set(all_fn())


@pytest.mark.parametrize(
    ("query", "all_fn"),
    [
        ("hi", all_greeting_responses),
        ("good morning", all_greeting_responses),
        ("hey there", all_greeting_responses),
        ("thanks a lot", all_thanks_responses),
        ("ok thanks", all_thanks_responses),
        ("thanks so much", all_thanks_responses),
        ("cheers", all_thanks_responses),
        ("ty", all_thanks_responses),
        ("bye", all_farewell_responses),
        ("see you later", all_farewell_responses),
        ("what can you do", all_capability_responses),
    ],
)
def test_small_talk_is_recognised(query: str, all_fn) -> None:
    assert detect_conversational_intent(query) in all_fn()


@pytest.mark.parametrize(
    "query",
    [
        "hi, what is deployiq",
        "thanks, but what is zero trust?",
        "good morning, how does essential eight work",
        "what can you do with Essential Eight reports",
    ],
)
def test_real_questions_still_pass_through(query: str) -> None:
    assert detect_conversational_intent(query) is None
