"""Table-driven unit tests for canonical outcome derivation (Task 1.1)."""

import pytest

from app.governance.outcomes import (
    ALL_OUTCOMES,
    GENERATION_OUTCOMES,
    derive_outcome,
)


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        # 1. Conversational intent by query
        (
            {"query": "hello there", "answer": "Hello! I'm the assistant."},
            "conversational",
        ),
        ({"query": "thanks!", "answer": "You're welcome!"}, "conversational"),
        ({"query": "bye", "answer": "Goodbye!"}, "conversational"),
        # 2. Conversational by outcome hint
        ({"outcome_hint": "conversational"}, "conversational"),
        # 3. Conversational by token details
        ({"token_details": {"outcome": "conversational"}}, "conversational"),
        # 4. Blocked pre-retrieval (zero prompt tokens + guardrail flags)
        ({"prompt_tokens": 0, "guardrail_flags": ["pii_detected"]}, "blocked"),
        ({"prompt_tokens": None, "guardrail_flags": ["unclear_input"]}, "blocked"),
        ({"outcome_hint": "blocked"}, "blocked"),
        # 5. LLM failure
        ({"outcome_hint": "llm_failed"}, "llm_failed"),
        ({"outcome_hint": "empty_llm_output"}, "llm_failed"),  # legacy mapping
        # 6. Ungrounded
        (
            {"outcome_hint": "ungrounded", "grounded": False, "prompt_tokens": 100},
            "ungrounded",
        ),
        (
            {"outcome_hint": "fallback", "grounded": False, "prompt_tokens": 100},
            "ungrounded",
        ),  # legacy mapping
        (
            {"grounded": False, "prompt_tokens": 120, "completion_tokens": 50},
            "ungrounded",
        ),
        # 7. Answered
        ({"grounded": True, "prompt_tokens": 200, "completion_tokens": 80}, "answered"),
        ({"outcome_hint": "answered", "grounded": True}, "answered"),
    ],
)
def test_derive_outcome_table_driven(kwargs, expected):
    result = derive_outcome(**kwargs)
    assert result == expected
    assert result in ALL_OUTCOMES


def test_generation_outcomes_subset():
    assert "answered" in GENERATION_OUTCOMES
    assert "ungrounded" in GENERATION_OUTCOMES
    assert "llm_failed" in GENERATION_OUTCOMES
    assert "conversational" not in GENERATION_OUTCOMES
    assert "blocked" not in GENERATION_OUTCOMES
