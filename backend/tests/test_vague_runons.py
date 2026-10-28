"""Run-ons of vague cues ("i need help, tell me something, idk, what should we
buy") and odd vague one-liners get the Goal card; clear questions never do."""

from __future__ import annotations

import random
import time

import pytest

from app.guardrails.conversational import detect_conversational_intent
from app.harness.ambiguity import AmbiguityKind, assess, goal_clarification
from app.harness.clarify import GOAL_HEADER, GOAL_QUESTION
from tests.test_clarify_trigger import _sources

DEFAULT = "Happy to point you in the right direction."

OWNER_RUNON = "i need help, tell me something, i am confused, idk, what should we buy"

# Canned conversational replies stay as they are (bare help intents).
CANNED_HELP = ("help", "help me", "can you help", "can you help me", "help??")

ODD_VAGUE = [
    "help me please",
    "hmm",
    "ok so",
    "whatever",
    "anything",
    "something",
    "something about stuff",
    "can you help me with something",
    "i dunno",
    "my boss told me to look at this",
    "what do I do",
    "where do I start with all this",
    "i have a few questions",
    "need some advice",
    "I NEED HELP!!!",
    "idk lol",
    "i need help \U0001f605",
    "um... uh... so",
    "HELP ME PLEASE!!!",
    "idk \U0001f937 not sure",
    "Hmm, ok so, whatever",
    "any suggestions?",
    "thoughts?",
    "ideas",
    "not sure",
    "no idea what to do",
    "I'm lost",
    "where to start",
    OWNER_RUNON + " from google?",
    "what should we buy from google?",
    "What should I buy, microsoft laptops?",
    "what should we buy from amazon",
    "idk, what should we buy, an iphone",
]

CLEAR = [
    "what should we buy for essential eight?",
    "help me understand zero trust",
    "I need help with Essential Eight",
    "Tell me about the Security Baseline Assessment",
    "What is ISO 27001?",
    "where are you located?",
    "who is the ceo of exigo tech",
    "what's the weather",
    "weather",
    "what is a cat",
    "how do I bake bread",
    "we got hacked, help",
    "idk, what does deployiq cost",
    "I'm confused about conditional access policies",
    "tell me about google cloud pricing",
    "how do I contact you",
]


def _kind(query: str) -> AmbiguityKind:
    return assess(query, None).kind


@pytest.mark.parametrize("query", [*ODD_VAGUE, OWNER_RUNON])
def test_odd_and_runon_prompts_get_the_goal_card(query: str) -> None:
    assessment = assess(query, None)
    assert assessment.kind is AmbiguityKind.GOAL, query
    card = goal_clarification(query, _sources(), assessment.cue)
    assert card is not None
    [question] = card.questions
    assert (question.header, question.question) == (GOAL_HEADER, GOAL_QUESTION)
    assert len(question.options) == 4


@pytest.mark.parametrize(
    "query",
    [
        "what should we buy from google?",
        OWNER_RUNON + " from google?",
        "What should I buy, microsoft laptops?",
        "what should we buy from Amazon",
    ],
)
def test_third_party_buy_runons_use_the_default_preamble(query: str) -> None:
    assessment = assess(query, None)
    card = goal_clarification(query, _sources(), assessment.cue)
    assert card is not None
    assert card.preamble == DEFAULT


@pytest.mark.parametrize("query", CANNED_HELP)
def test_bare_help_stays_canned(query: str) -> None:
    assert detect_conversational_intent(query) is not None


@pytest.mark.parametrize("query", CLEAR)
def test_clear_prompts_never_get_the_goal_card(query: str) -> None:
    assert _kind(query) is not AmbiguityKind.GOAL, query


# ---------------------------------------------------------------------------
# Seeded fuzz
# ---------------------------------------------------------------------------

_CUES = (
    "i need help",
    "help me",
    "need advice",
    "tell me something",
    "tell me anything",
    "anything",
    "something",
    "whatever",
    "not sure",
    "dunno",
    "no idea",
    "idk",
    "i am confused",
    "i'm lost",
    "where to start",
    "where do i start",
    "what should we buy",
    "what should i do",
    "what do you recommend",
    "what's best",
    "any suggestions",
    "ideas",
    "thoughts",
    "hmm",
    "ok so",
    "um",
    "uh",
    "please",
    "i have a question",
    "we need advice",
    "my boss asked me to look into this",
    "what are my options",
)
_SEPS = (", ", ", ", "; ", ". ", "? ", "! ", " and ", "\n", " ... ", " - ")
_FILLERS = ("lol", "please", "ok", "um", "so", "\U0001f605", "\U0001f937", "??", "!!")
_CLEAR_CORES = (
    "what should we buy for essential eight",
    "help me understand zero trust",
    "i need help with iso 27001",
    "what does essential eight maturity level 2 require",
    "how much does the cis benchmark assessment cost",
    "tell me about deployiq",
    "who founded exigo tech",
    "where is exigo tech located",
    "what certifications does exigo tech hold",
    "i'm confused about essential eight",
    "what is zero trust",
    "can you assess our microsoft 365 tenant",
    "what should i do about ransomware",
    "what is the weather today",
    "how do i bake bread",
    "tell me about the security baseline assessment",
    "any suggestions for cis hardening",
    "which service covers iso 27001 certification",
    "what do you recommend for zero trust",
    "do you support intune device compliance",
)


def _runon(rng: random.Random) -> str:
    parts = rng.sample(_CUES, rng.randint(2, 6))
    out = ""
    for i, cue in enumerate(parts):
        part = cue
        if rng.random() < 0.25:
            part = f"{rng.choice(_FILLERS)} {part}"
        if rng.random() < 0.2:
            part = f"{part} {rng.choice(_FILLERS)}"
        out += part + (rng.choice(_SEPS) if i < len(parts) - 1 else "")
    out += rng.choice(("", "", "?", "!", ".", "??", "!!!", " \U0001f605"))
    return rng.choice((out, out.upper(), out.capitalize(), out.title(), out))


def _clear(rng: random.Random) -> str:
    core = rng.choice(_CLEAR_CORES)
    text = rng.choice(("", "", "hi, ", "ok so ", "um ", "please ")) + core
    text += rng.choice(("", "?", ".", " please", "??"))
    return rng.choice((text, text.capitalize(), text.upper(), text))


def test_fuzz_vague_runons_always_get_the_card() -> None:
    rng = random.Random(20261101)  # noqa: S311
    bad = []
    for _ in range(150):
        query = _runon(rng)
        if _kind(query) is AmbiguityKind.GOAL:
            continue
        if detect_conversational_intent(query) is None:
            bad.append(query)
    assert not bad, bad[:10]


def test_fuzz_clear_questions_never_get_the_card() -> None:
    rng = random.Random(20261102)  # noqa: S311
    bad = [
        q for q in (_clear(rng) for _ in range(150)) if _kind(q) is AmbiguityKind.GOAL
    ]
    assert not bad, bad[:10]


def test_adversarial_300_char_inputs_are_fast() -> None:
    inputs = [
        "," * 300,
        "a " * 150,
        "help " * 60,
        "idk, " * 60,
        "what should we buy " * 16,
        " and " * 60,
        "hm" * 150,
        "!?." * 100,
        "i need " * 40 + "help",
        ("x" * 29 + " ") * 10,
    ]
    start = time.perf_counter()
    for text in inputs:
        assess(text[:300], None)
    assert time.perf_counter() - start < 1.0
