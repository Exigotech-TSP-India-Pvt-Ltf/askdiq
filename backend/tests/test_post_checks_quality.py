"""Quality tests for post-generation checks: refusals, claims, relevance, brackets."""

import pytest

from app.guardrails import post_checks
from app.guardrails.post_checks import (
    _NEGATIVE_ANSWER_RE,
    _unsupported_figures,
    check_groundedness,
    is_off_topic_by_overlap,
    is_refusal,
    query_has_overlap,
    split_claims,
    supported_claim_ratio,
    unsupported_claims,
)
from app.harness.citations import apply_citations, strip_free_brackets
from app.schemas.query import SourceChunk


def _source(content: str, doc: str = "00000000-0000-0000-0000-000000000002"):
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id=doc,
        content=content,
        score=0.8,
        metadata={},
    )


# --- refusal regex -----------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "The documents do not specify exact pricing.",
        "We don't have details on that here.",
        "DeployIQ doesn't include that feature.",
        "I cannot provide that information.",
        "There is no information about that.",
        "That is not available in the context.",
        "Certifications are not yet claimed.",
    ],
)
def test_negative_regex_matches_real_negation(answer):
    assert _NEGATIVE_ANSWER_RE.search(answer) is not None


@pytest.mark.parametrize(
    "answer",
    [
        "DeployIQ does include Essential Eight reporting.",
        "DeployIQ does provide continuous monitoring.",
        "The platform does cover Zero Trust identity controls.",
        "Reports are available in the dashboard.",
    ],
)
def test_negative_regex_ignores_positive_does_phrases(answer):
    assert _NEGATIVE_ANSWER_RE.search(answer) is None
    assert is_refusal(answer) is False


@pytest.mark.parametrize(
    "answer",
    [
        "We don't have details on that here. Talk to one of our specialists.",
        "That is outside what I can help with.",
        "I want to give you an accurate answer and I'm not certain.",
        "Please talk to one of our specialists.",
    ],
)
def test_friendly_refusals_are_detected(answer):
    assert is_refusal(answer) is True


def test_long_answer_with_late_negative_is_not_a_refusal():
    answer = (
        "DeployIQ delivers Essential Eight controls across identity and devices. "
        "It reports continuously on posture. "
        "It supports governance workflows for administrators. "
        "Pricing is not available publicly. " + "More detail here. " * 20
    )
    assert is_refusal(answer) is False


def test_refusal_with_dollar_figure_is_not_a_refusal():
    assert is_refusal("We don't have details, but it costs $50 a month.") is False


async def test_negative_does_include_runs_normal_checks(monkeypatch):
    async def _fake_embed(texts):
        return [[0.0, 1.0] for _ in texts]

    monkeypatch.setattr(post_checks, "embed_texts", _fake_embed)
    sources = [
        SourceChunk(
            chunk_id="00000000-0000-0000-0000-000000000001",
            document_id="00000000-0000-0000-0000-000000000002",
            content="Unrelated",
            score=0.8,
            metadata={},
            embedding=[1.0, 0.0],
        )
    ]
    grounded, _c, flag = await check_groundedness(
        "DeployIQ does include a flux capacitor.", sources, 0.45
    )
    assert grounded is False
    assert flag == "possible_hallucination"


# --- assurance / commercial claims ------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "DeployIQ is safe to purchase.",
        "We guarantee delivery.",
        "DeployIQ is certified to ISO 27001.",
        "It is risk-free.",
        "You get 24/7 support.",
        "Expect a strong ROI.",
        "Onboarding is done within 48 hours.",
        "Deploy in minutes not weeks.",
        "Our SLA covers this.",
        "We offer 20% off.",
        "The Starter plan is free.",
        "It has a 99.9% uptime.",
    ],
)
def test_unsupported_assurance_claims_flagged(answer):
    assert unsupported_claims(answer, "DeployIQ delivers Essential Eight reporting.")


def test_supported_claims_allowed():
    assert (
        unsupported_claims(
            "The Starter plan is free and onboarding is within 48 hours.",
            "Starter (Free) plan. Onboarding is completed within 48 hours.",
        )
        == []
    )


def test_free_of_phrase_is_not_a_claim():
    assert unsupported_claims("The report is free of jargon.", "Plain text.") == []


def test_currency_words_checked_like_dollar_figures():
    assert _unsupported_figures("It costs 500 AUD per month.", "No pricing.") != []
    assert _unsupported_figures("It is 20 per user.", "No pricing.") != []
    assert _unsupported_figures("It costs 500 AUD.", "Pricing: 500 AUD.") == []


async def test_unsupported_claim_makes_answer_ungrounded(monkeypatch):
    async def _boom(*_a, **_k):
        raise AssertionError("must short-circuit before embedding")

    monkeypatch.setattr(post_checks, "embed_texts", _boom)
    grounded, confidence, flag = await check_groundedness(
        "DeployIQ is safe to purchase.", [_source("DeployIQ Essential Eight.")], 0.45
    )
    assert (grounded, confidence, flag) == (False, 0.0, "unsupported_claim")


async def test_supported_claim_reaches_semantic_check(monkeypatch):
    async def _fake_embed(texts):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(post_checks, "embed_texts", _fake_embed)
    grounded, _c, flag = await check_groundedness(
        "DeployIQ is safe to use.", [_source("It is safe to use.")], 0.45
    )
    assert grounded is True
    assert flag is None


# --- claim-level support ----------------------------------------------


def test_split_claims_sentences_bullets_and_cap():
    claims = split_claims(
        "First claim is here. - Second bullet point here\n1. Third one is ok"
    )
    assert claims == [
        "First claim is here.",
        "Second bullet point here",
        "Third one is ok",
    ]
    assert len(split_claims("Some sentence goes here. " * 30)) == 12


def test_supported_claim_ratio_uses_best_chunk_per_claim():
    sources = [[1.0, 0.0], [0.0, 1.0]]
    claims = [[1.0, 0.0], [0.0, 1.0], [-0.7, -0.7]]
    assert supported_claim_ratio(claims, sources, 0.5) == pytest.approx(2 / 3)
    assert supported_claim_ratio([], sources, 0.5) == 0.0


async def test_claim_ratio_below_minimum_is_ungrounded(monkeypatch):
    vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [0.0, 1.0]}

    async def _fake_embed(texts):
        # answer first, then claims; whole answer matches the chunk.
        return [
            [1.0, 0.0]
            if t.startswith("Alpha") or t.startswith("Alpha one")
            else vectors["b"]
            for t in texts
        ]

    monkeypatch.setattr(post_checks, "embed_texts", _fake_embed)
    sources = [
        SourceChunk(
            chunk_id="00000000-0000-0000-0000-000000000001",
            document_id="00000000-0000-0000-0000-000000000002",
            content="Alpha",
            score=0.8,
            metadata={},
            embedding=[1.0, 0.0],
        )
    ]
    answer = "Alpha one is here. Beta two is there. Gamma three is elsewhere."
    grounded, _c, flag = await check_groundedness(answer, sources, 0.45, 0.6)
    assert grounded is False
    assert flag == "possible_hallucination"

    grounded, _c, flag = await check_groundedness(answer, sources, 0.45, 0.3)
    assert grounded is True


async def test_claims_embedded_in_one_batched_call(monkeypatch):
    calls: list[list[str]] = []

    async def _fake_embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(post_checks, "embed_texts", _fake_embed)
    source = SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content="x",
        score=0.8,
        metadata={},
        embedding=[1.0, 0.0],
    )
    grounded, _c, _f = await check_groundedness(
        "First claim is here. Second claim is here.", [source], 0.45
    )
    assert grounded is True
    assert len(calls) == 1
    assert len(calls[0]) == 3


# --- query relevance ---------------------------------------------------


def test_query_has_overlap_true_for_shared_word_or_stem():
    chunks = ["DeployIQ monitors identity governance for administrators."]
    assert query_has_overlap("How does governance work?", chunks) is True
    assert query_has_overlap("administrator access", chunks) is True


def test_query_has_overlap_false_for_nonsense():
    chunks = ["DeployIQ monitors identity governance for administrators."]
    assert query_has_overlap("Topic: blorptivex zumquat", chunks) is False
    assert query_has_overlap("what is a cat", chunks) is True  # no content words


def test_off_topic_by_overlap_needs_weak_score_and_no_overlap():
    chunks = ["DeployIQ monitors identity governance."]
    assert is_off_topic_by_overlap("blorptivex zumquat", chunks, 0.2, 0.3) is True
    assert is_off_topic_by_overlap("blorptivex zumquat", chunks, 0.5, 0.3) is False
    assert is_off_topic_by_overlap("identity governance", chunks, 0.2, 0.3) is False


# --- citations: free-text brackets ------------------------------------


def test_strip_free_brackets_keeps_markers_links_and_code():
    text = (
        "See [Deploy IQ] docs [1] and [Source: cis.pdf] or [2, 3]. "
        "A [link](https://x.y) stays.\n```\nx = a[b]\n```"
    )
    out = strip_free_brackets(text)
    assert "[Deploy IQ]" not in out
    assert "Deploy IQ docs [1]" in out
    assert "cis.pdf" not in out
    assert "[2, 3]" not in out
    assert "[link](https://x.y)" in out
    assert "x = a[b]" in out


def test_apply_citations_strips_free_brackets_and_drops_out_of_range():
    sources = [_source("a", doc="00000000-0000-0000-0000-0000000000a1")]
    answer, citations = apply_citations("It works [note] well [1][9].", sources)
    assert answer == "It works note well [1]."
    assert [c.n for c in citations] == [1]
