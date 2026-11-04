"""PERSONA: deterministic replies to questions about the assistant itself."""

from __future__ import annotations

import time

import pytest

from app.guardrails import conversational as conv
from app.harness import orchestrator
from app.harness.prompt_catalog import PERSONA_TEMPLATES, persona_template

_PERSONA = (
    ("what's your name?", "name"),
    ("what should I call you", "name"),
    ("who built you?", "creator"),
    ("who made this chatbot?", "creator"),
    ("who do you work for?", "creator"),
    ("who owns this bot", "creator"),
    ("who are you", "purpose"),
    ("what is this", "purpose"),
    ("what can you do?", "capabilities"),
    ("what can you help me with", "capabilities"),
    ("are you ChatGPT?", "model"),
    ("which model are you running on?", "model"),
    ("how were you built", "model"),
    ("what languages do you speak?", "languages"),
    ("can I write in Hindi?", "languages"),
    ("is this chat private?", "privacy"),
    ("does anyone read what I type?", "privacy"),
    ("do you remember me", "memory"),
    ("Do you remember what we talked about last week?", "memory"),
    ("are you human?", "human"),
    ("am I talking to a real person", "human"),
    ("are you real or a bot?", "human"),
)
_PRODUCT = (
    "what can DeployIQ do for Intune?",
    "what do you know about Zero Trust?",
    "who made DeployIQ?",
    "can you help me with Intune licensing",
    "is it private cloud?",
    "who runs it?",
    "how do you handle my personal information",
)
_BANNED = ("language model", "feelings", "not a person", "knowledge base", "specialist")


@pytest.mark.parametrize(("query", "intent"), _PERSONA)
def test_persona_detected(query: str, intent: str) -> None:
    assert conv.detect_persona(query) == intent


@pytest.mark.parametrize("query", _PRODUCT)
def test_product_questions_are_not_persona(query: str) -> None:
    assert conv.detect_persona(query) is None


@pytest.mark.parametrize("intent", list(PERSONA_TEMPLATES))
def test_templates_follow_voice(intent: str) -> None:
    text = persona_template(intent, "https://example.com/privacy-policy")
    lowered = text.lower()
    assert "{" not in text
    assert not any(b in lowered for b in _BANNED), text
    assert chr(0x2014) not in text
    assert text.count("!") <= 1


def test_privacy_reply_links_or_points_to_site() -> None:
    url = "https://example.com/privacy"
    assert url in persona_template("privacy", url)
    assert "website" in persona_template("privacy")


def test_detection_is_fast_on_adversarial_input() -> None:
    start = time.perf_counter()
    for filler in ("are you ", "what can you ", "who ", "is this chat "):
        conv.detect_persona((filler * 40)[:120])
    assert time.perf_counter() - start < 0.2


async def test_persona_response_shape(monkeypatch) -> None:
    async def boom(*_a, **_k):
        raise AssertionError("retrieval must not run for a persona reply")

    monkeypatch.setattr(orchestrator, "retrieve_parts", boom)
    for query, intent, outcome in (
        ("who built you?", "creator", "blocked"),
        ("what languages do you speak?", "languages", "conversational"),
        ("is this chat private?", "privacy", "conversational"),
    ):
        response = await orchestrator.run_query(
            query=query,
            top_k=3,
            filters=None,
            vector_store=None,  # type: ignore[arg-type]
            request_id="r",
            stage_timings={},
        )
        assert response.outcome == outcome
        assert response.grounded is False
        assert response.confidence == 0.0
        assert response.sources == []
        assert response.token_details["refusal_template"] == f"persona_{intent}"
        blocked = outcome == "blocked"
        assert (response.guardrail_flags == ["identity_probe"]) == blocked
