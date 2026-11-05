"""True claims the quote-backed verifier used to drop (regression guard).

Chunk texts are the stored chunks of data/zero-trust.md, data/privacy.md and
data/packages.md; claims and quotes are the ones the model wrote live.
"""

from __future__ import annotations

import json

import pytest

from app.guardrails.post_checks import unsupported_claims
from app.harness.claims import ground_answer, parse_draft
from app.schemas.query import SourceChunk

PILLARS = (
    "Zero Trust Security, Simplified by Deploy IQ \u203a Zero Trust Pillars\n\n"
    "- Identity\n- Devices\n- Network\n- Infrastructure\n- Applications\n"
    "- Data\n- Visibility & Analytics"
)
AI_DOES_NOT = (
    "Privacy Policy \u203a 4.3 Human Oversight and AI Boundaries\n\n"
    "AI does not:\n\n- deploy or approve security-critical changes\n"
    "- make binding decisions\n- operate without human review"
)
WHY_ZT = (
    "Zero Trust Security \u203a Why Zero Trust Matters\n\n"
    "Zero Trust helps organisations:\n\n- Reduce Attack Surface\n"
    "- Limit lateral movement\n- Protect sensitive data\n"
    "- Detect and contain breaches earlier\n"
    "- Make security decisions based on risk, not location"
)
MDR_ROW = (
    "Packages \u203a Compare packages\n\n"
    "24/7 Managed Detection & Response: included in Frontier; not included "
    "in Baseline, Foundation, Strengthen."
)


def _source(content: str, heading: str = "") -> SourceChunk:
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000101",
        content=content,
        score=0.8,
        metadata={"source_name": "page.md", "section_heading": heading},
    )


def _answered(text: str, quote: str, content: str, query: str, **kw: str) -> bool:
    claim = {"text": text, "source": 1, "quote": quote, "part": 0, **kw}
    raw = json.dumps({"parts": [{"ask": query, "covered": True}], "claims": [claim]})
    draft = parse_draft(raw)
    assert draft is not None
    return ground_answer(
        draft, [_source(content, "Zero Trust Pillars")], query
    ).answered


@pytest.mark.parametrize(
    ("text", "quote", "answered"),
    [
        ("Identity is a Zero Trust pillar.", "- Identity", True),
        ("Data is one of the Zero Trust pillars.", "- Data", True),
        # Words the item, its lead and heading never say stay unbacked.
        ("Identity is the most important pillar.", "- Identity", False),
        # A one-word quote that is no whole line of the chunk is not re-anchored.
        ("Analytics is a Zero Trust pillar.", "Analytics", False),
    ],
)
def test_one_word_list_item_is_anchored_to_its_line(
    text: str, quote: str, answered: bool
) -> None:
    query = "What are the Zero Trust pillars?"
    assert _answered(text, quote, PILLARS, query, kind="item") is answered


@pytest.mark.parametrize(
    ("text", "answered"),
    [
        ("AI does not make binding decisions.", True),
        (
            "AI does not make binding decisions and does not operate without "
            "human review.",
            True,
        ),
        (
            "AI does not make binding decisions, and it does not deploy or "
            "approve security-critical changes or operate without human review.",
            True,
        ),
        ("AI makes binding decisions.", False),
        ("AI does not make binding decisions but does deploy changes.", False),
        ("AI does not operate with human review.", False),
    ],
)
def test_negative_lead_in_carries_into_its_items(text: str, answered: bool) -> None:
    quote = (
        "AI does not: - deploy or approve security-critical changes - make "
        "binding decisions - operate without human review"
    )
    query = "Does your AI make binding decisions?"
    assert _answered(text, quote, AI_DOES_NOT, query) is answered


@pytest.mark.parametrize(
    ("text", "answered"),
    [
        (
            "Zero Trust helps make security decisions based on risk rather than location.",
            True,
        ),
        ("Zero Trust helps make security decisions based on location.", False),
    ],
)
def test_rather_than_is_the_page_s_not(text: str, answered: bool) -> None:
    quote = "Make security decisions based on risk, not location"
    query = "Why does Zero Trust matter?"
    assert _answered(text, quote, WHY_ZT, query) is answered


def test_only_is_backed_by_the_quote_s_own_contrast() -> None:
    text = (
        "The Baseline package does not include 24/7 Managed Detection & "
        "Response; 24/7 MDR is included only in the Frontier package."
    )
    quote = MDR_ROW.split("\n\n", 1)[1]
    assert _answered(text, quote, MDR_ROW, "Why does Baseline include 24/7 MDR?")


def test_round_the_clock_and_24_7_are_one_assurance() -> None:
    source = (
        "24/7 Managed Detection & Response: round-the-clock threat monitoring, "
        "hunting and response by a dedicated security operations team"
    )
    assert unsupported_claims("Frontier adds round-the-clock monitoring.", source) == []
    assert unsupported_claims("Frontier adds 24/7 monitoring.", source) == []
    # A 24-hour figure is a different claim.
    assert unsupported_claims("We respond within 24 hours.", source)


SUPPORT = (
    "Get Started \u203a Support & Resources\n\n"
    "Access guides, get help, or contact our support team for assistance "
    "with DeployIQ.\n\nAI-powered support"
)


@pytest.mark.parametrize(
    ("text", "quote", "answered"),
    [
        (
            "Access guides, get help, or contact our support team for "
            "assistance with DeployIQ.",
            "Access guides, get help, or contact our support team for "
            "assistance with DeployIQ.",
            True,
        ),
        # A hand-off the model wrote (not the quoted page line) is a decline.
        (
            "Please contact our support team for more details.",
            "Access guides, get help, or contact our support team",
            False,
        ),
    ],
)
def test_quoted_page_copy_is_not_a_decline(
    text: str, quote: str, answered: bool
) -> None:
    query = "What's available under Support & Resources?"
    assert _answered(text, quote, SUPPORT, query) is answered
