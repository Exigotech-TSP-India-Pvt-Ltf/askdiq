"""Tests for vague, random, mixed, and edge-case inputs across all guardrail layers."""

import pytest

from app.guardrails.pre_checks import (
    run_pre_checks,
    sanitize_query,
)
from app.harness.orchestrator import _fallback_response
from app.retrieval.retriever import needs_conversation_context

# =========================================================================
# Category 1: Single-Letter & Ultra-Short Inputs
# =========================================================================


@pytest.mark.parametrize("query", ["a", "y", "k", "x", "b", "z"])
def test_single_letter_inputs_flagged_as_unclear(query):
    flags = run_pre_checks(query, 2000)
    assert "unclear_input" in flags


@pytest.mark.parametrize("query", ["E8", "e8", "ai", "db", "ip"])
def test_whitelisted_short_acronyms_allowed(query):
    flags = run_pre_checks(query, 2000)
    assert "unclear_input" not in flags
    assert "spam_detected" not in flags


# =========================================================================
# Category 2: Pure Gibberish & Keyboard Mash
# =========================================================================


@pytest.mark.parametrize(
    "query",
    [
        "dffghjgjg",
        "asdfgh",
        "qwertyuiop",
        "zxcvbnm",
        "kljhgfdsa",
        "bcdfghjkl",
        "aaaaabbbbb",
        "qazwsxedc",
        "plmoknijb",
    ],
)
def test_pure_gibberish_detected_as_spam(query):
    flags = run_pre_checks(query, 2000)
    assert "spam_detected" in flags


# =========================================================================
# Category 3: Multi-Word Symbol-Punctuated Mash
# =========================================================================


@pytest.mark.parametrize(
    "query",
    [
        "ahjhkjskjdagjg jasljljdlo9i a;;o0d,ndnk sa;k;k;f",
        "asd;;; qwe::: zxc,,,",
        "fkdlskfj!@#$ lskdfj%^&*",
        "a;;o0d,ndnk",
        "sa;k;k;f",
        "jasljljdlo9i",
        "dfghjk! dfghjk? dfghjk.",
    ],
)
def test_multi_word_punctuated_mash_detected(query):
    flags = run_pre_checks(query, 2000)
    assert "spam_detected" in flags


# =========================================================================
# Category 4: Mixed Inputs (Noise + Question)
# =========================================================================


def test_mixed_query_suffix_noise_cleaned():
    cleaned, flags = sanitize_query("what is deployiq? akjj")
    assert flags == []
    assert cleaned == "what is deployiq?"


def test_mixed_query_prefix_noise_cleaned():
    cleaned, flags = sanitize_query("akjj what is deployiq?")
    assert flags == []
    assert cleaned == "what is deployiq?"


def test_mixed_query_infix_noise_cleaned():
    cleaned, flags = sanitize_query("what is akjj deployiq?")
    assert flags == []
    assert cleaned == "what is deployiq?"


def test_mixed_query_dominant_noise_blocked():
    _cleaned, flags = sanitize_query(
        "ahjhkjskjdagjg deployiq jasljljdlo9i a;;o0d,ndnk sa;k;k;f"
    )
    assert "spam_detected" in flags


def test_mixed_query_repeated_noise_with_one_keyword_blocked():
    _cleaned, flags = sanitize_query("deployiq akjj akjj akjj akjj")
    assert "spam_detected" in flags


# =========================================================================
# Category 5: Punctuation, Whitespace & Symbol-Only Noise
# =========================================================================


@pytest.mark.parametrize(
    "query",
    [
        "???",
        "...",
        "!?!?!",
        "---",
        "***",
        "   ",
    ],
)
def test_punctuation_and_whitespace_noise_flagged(query):
    flags = run_pre_checks(query, 2000)
    assert "empty_query" in flags or "unclear_input" in flags


# =========================================================================
# Category 6: False-Positive Guards for Legitimate Terse Domain Queries
# =========================================================================


@pytest.mark.parametrize(
    "query",
    [
        "What is E8?",
        "MFA",
        "SOC2",
        "ACSC",
        "RBAC",
        "Zero Trust?",
        "Pricing?",
        "Why DeployIQ?",
        "What does DeployIQ do?",
        "Tell me about the Essential Eight",
    ],
)
def test_legitimate_domain_queries_pass_cleanly(query):
    cleaned, flags = sanitize_query(query)
    assert flags == []
    assert len(cleaned) > 0


# =========================================================================
# Category 7: Multi-Turn History Contamination Gate
# =========================================================================


@pytest.mark.parametrize(
    "query",
    [
        "dffghjgjg",
        "a",
        "y",
        "???",
        "ahjhkjskjdagjg jasljljdlo9i",
        "asdfgh",
    ],
)
def test_random_or_noise_queries_never_inherit_history(query):
    assert needs_conversation_context(query) is False


@pytest.mark.parametrize(
    "query",
    [
        "why?",
        "how?",
        "tell me more",
        "what about pricing?",
        "and the enterprise plan?",
        "does it support SSO?",
        "how does that work?",
        "more details",
    ],
)
def test_legitimate_follow_ups_inherit_history(query):
    assert needs_conversation_context(query) is True


# =========================================================================
# Category 8: Polite and Professional Fallback Messages
# =========================================================================


def test_unclear_input_fallback_message():
    resp = _fallback_response(
        request_id="req-1", flags=["unclear_input"], latency_ms=10.0
    )
    assert "say a bit more" in resp.answer
    assert resp.grounded is False


def test_spam_detected_fallback_message():
    resp = _fallback_response(
        request_id="req-2", flags=["spam_detected"], latency_ms=10.0
    )
    assert "didn't come through clearly" in resp.answer
    assert resp.grounded is False


def test_off_topic_fallback_message():
    resp = _fallback_response(request_id="req-3", flags=["off_topic"], latency_ms=10.0)
    assert "outside what I can help with" in resp.answer
    assert "specialists" in resp.answer
    assert resp.grounded is False
