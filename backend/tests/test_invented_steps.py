"""Steps / advice invented after a decline are removed unless the sources state them."""

from __future__ import annotations

import time

from app.harness.decline import clean_declines, strip_invented_after_decline

QUERY = "How do I configure mTLS for Zero Trust?"
ZT_SOURCES = (
    "Zero Trust verifies every identity, device and request before granting access. "
    "DeployIQ helps assess Zero Trust maturity across identity and endpoints."
)
MTLS_INLINE = (
    "I can't see that detail, so it's best to check with one of our specialists. "
    "1. Contact via form [1] 2. Ask for Professional engagement [2]"
)
MTLS_LINES = (
    "I can't see that detail here. [1]\n"
    "Here is how to proceed:\n"
    "1. Contact via form [1]\n"
    "2. Ask for Professional engagement [2]\n"
    "Talk to one of our specialists."
)


def test_mtls_inline_steps_removed() -> None:
    cleaned, declined = clean_declines(MTLS_INLINE, QUERY, ZT_SOURCES)
    assert declined
    assert (
        cleaned
        == "I can't see that detail, so it's best to check with one of our specialists."
    )
    assert "[" not in cleaned
    assert "Professional" not in cleaned


def test_mtls_multiline_steps_and_lead_in_removed() -> None:
    cleaned, declined = clean_declines(MTLS_LINES, QUERY, ZT_SOURCES)
    assert declined
    assert cleaned == "I can't see that detail here.\nTalk to one of our specialists."


def test_bulleted_instructions_removed() -> None:
    text = (
        "I don't see that covered. [1]\n- Open the admin portal [1]\n"
        "- Upload your certificate [2]\nPlease contact our specialists."
    )
    out = strip_invented_after_decline(text, ZT_SOURCES, QUERY)
    assert out == "I don't see that covered. [1]\nPlease contact our specialists."


def test_supported_list_after_decline_stays() -> None:
    sources = (
        "Zero Trust verifies identity and device health. Conditional access policies "
        "enforce multi-factor authentication for every request."
    )
    text = (
        "I can't see that detail, but here is what we cover:\n"
        "- Verify identity and device health [1]\n"
        "- Conditional access policies enforce multi-factor authentication [1]"
    )
    assert strip_invented_after_decline(text, sources, QUERY) == text


def test_unsupported_figure_removed_even_if_words_match() -> None:
    text = (
        "I can't see that detail.\n- Verify identity and device health within 45 days"
    )
    out = strip_invented_after_decline(text, ZT_SOURCES, QUERY)
    assert out == "I can't see that detail."


def test_free_instruction_removed_but_handoff_kept() -> None:
    text = (
        "I can't see that detail. Submit the onboarding form. Our specialists can help."
    )
    out = strip_invented_after_decline(text, ZT_SOURCES, QUERY)
    assert out == "I can't see that detail. Our specialists can help."


def test_no_decline_unchanged() -> None:
    text = "Here are the steps:\n1. Contact via form [1]\n2. Ask for Professional engagement [2]"
    assert strip_invented_after_decline(text, ZT_SOURCES, QUERY) == text
    cleaned, declined = clean_declines(text, QUERY, ZT_SOURCES)
    assert not declined
    assert "Professional engagement" in cleaned


def test_without_sources_text_nothing_stripped() -> None:
    cleaned, declined = clean_declines(MTLS_INLINE, QUERY)
    assert not declined
    assert "Professional engagement" in cleaned


def test_linear_time_on_large_answer() -> None:
    body = "I can't see that. " + "1. Ask for Professional engagement [1] " * 600
    body = body[:20000]
    start = time.perf_counter()
    strip_invented_after_decline(body, ZT_SOURCES * 200, QUERY)
    clean_declines(
        "I can't see that.\n" + "- Open the portal [1]\n" * 900, QUERY, ZT_SOURCES
    )
    assert time.perf_counter() - start < 2.0
